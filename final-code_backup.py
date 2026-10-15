import cv2
import numpy as np
import time
import os
import re
import csv
import subprocess
import sys
import threading
import contextlib
import RPi.GPIO as GPIO


@contextlib.contextmanager
def suppress_c_stderr():
    """Silences OS-level stderr; hides harmless libjpeg-turbo "Corrupt JPEG
    data" warnings from cheap USB webcam MJPG decoding.

    CHANGED: this used to be entered/exited on every single processed frame
    (dup/dup2/close syscalls each time). It is now entered ONCE for the
    entire capture loop instead, which removes that per-frame syscall cost
    with identical suppression behavior."""
    stderr_fd = sys.stderr.fileno()
    saved_fd = os.dup(stderr_fd)
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull_fd, stderr_fd)
        yield
    finally:
        os.dup2(saved_fd, stderr_fd)
        os.close(devnull_fd)
        os.close(saved_fd)

# -----------------------------
# CAMERA SETTINGS
# -----------------------------
# Find these via: v4l2-ctl --list-devices, then
# v4l2-ctl --device=/dev/videoN --all | grep Serial
TOP_CAM_SERIAL = "152F4120"   # UVC Camera (046d:0825) - your "top" camera
SIDE_CAM_SERIAL = "D690DB60"  # C270 HD WEBCAM - your "side" camera
FRAME_WIDTH = 640
FRAME_HEIGHT = 480
CONSECUTIVE_FRAMES_REQUIRED = 3
TOTAL_VISIBLE_WINDOW_S = 2.0

# Upper bound only - actual measuring time shrinks dynamically so that
# debounce time + measuring time never exceeds TOTAL_VISIBLE_WINDOW_S.
MEASURING_DURATION_S = 1.00

# Margin (px) from frame border beyond which a contour is CLIPPED (partial
# view -> wrong minAreaRect size), so clipped samples are excluded from averaging.
EDGE_MARGIN_PX = 5

# Once this many CLEAN samples are collected, finalize immediately when the
# block starts clipping/exiting rather than waiting out MEASURING_DURATION_S.
MIN_CLEAN_SAMPLES_FOR_EARLY_RESULT = 3
MIN_COMPLETE_SAMPLES = 3

# Cooldown after a result so the same block's trailing edge doesn't re-trigger.
TRIGGER_COOLDOWN_S = 1.0

# -----------------------------
# CALIBRATION
# -----------------------------
PPM_TOP_X = 3.8084163672
PPM_TOP_Y = 3.8698078846
PPM_SIDE_X = 3.5967236364
PPM_SIDE_Y = 4.2571831296
THRESHOLD = 150  # kept for reference / fallback only - live path now uses Otsu (see NOTE below)
MIN_AREA = 10000

# Set True temporarily when re-calibrating: prints raw pixel box size to
# directly compute PPM = pixels / known_mm.
CALIBRATION_DEBUG = False

# NEW: minimum solidity (contour area / convex hull area) a candidate blob
# must have to be trusted. Solid rectangular blocks are close to 1.0;
# noisy / fragmented / shadow-corrupted contours score much lower. This is
# a cheap sanity filter (one convexHull call) that rejects false detections
# without adding meaningful CPU cost. ASSUMPTION - tune if your object has
# rounded/irregular edges.
MIN_SOLIDITY = 0.85

# NEW: 3x3 kernel for a single, cheap morphological opening pass to remove
# small noise specks from the binary mask before contour detection.
# Created once at import time so it isn't reallocated every frame.
_MORPH_KERNEL = np.ones((3, 3), np.uint8)

# -----------------------------
# CPU OPTIMIZATION
# -----------------------------
FRAME_SKIP = 3             # decode+process the top camera only on every Nth frame
SHOW_DISPLAY = True        # set False for headless / embedded deployment (no imshow/waitKey cost)
SHOW_CPU_TEMP = True       # overlay Pi CPU temperature on the top camera window
TEMP_UPDATE_INTERVAL = 2.0  # seconds between temperature reads (cheap, but no need every frame)

# NEW: only actually paint the debug windows every Nth loop iteration.
# Detection/measurement logic is completely unaffected - this only throttles
# imshow()/waitKey() overhead, which is pure display cost with no bearing
# on accuracy.
DISPLAY_REFRESH_EVERY_N = 2

# -----------------------------
# TIMING / STATE MACHINE
# -----------------------------
MEASURING_TIMEOUT = 2.0   # hard safety cap on top of MEASURING_DURATION_S

# Floor to guarantee a minimum number of real samples every cycle.
MIN_MEASURING_DURATION_S = 0.5

# -----------------------------
# TARGET DIMENSIONS (PASS/FAIL)
# -----------------------------
TARGET_LENGTH_MM = 50
TARGET_WIDTH_MM = 55
TARGET_HEIGHT_MM = 50
TOLERANCE_MM = 2.5          # ASSUMPTION - not specified in the doc, adjust as needed
RESULT_DISPLAY_TIME = 1.0   # ASSUMPTION - how long to show PASS/FAIL before returning to IDLE

# -----------------------------
# BUZZER INDICATOR
# -----------------------------
BUZZER_PIN = 21   # GPIO21 -> buzzer -> GND

# -----------------------------
# LOGGING
# -----------------------------
# NEW: every finished inspection (PASS or FAIL) is appended as one row.
# This happens once per object cycle (seconds apart), so the file I/O cost
# is negligible relative to the per-frame processing loop.
LOG_FILE = "inspection_log.csv"

# -----------------------------
# STATES
# -----------------------------
STATE_IDLE = "IDLE"          # waiting for the top camera to see a qualifying object
STATE_MEASURING = "MEASURING"
STATE_RESULT = "RESULT"


# Foreground polarity:
# "bright" = object is brighter than its background
# "dark"   = object is darker than its background
FOREGROUND_MODE = "bright"


CLEAR_FRAMES_REQUIRED = 3


def _list_video_device_paths():
    """Returns every /dev/videoN path via `v4l2-ctl --list-devices`,
    or None if v4l-utils isn't installed."""
    try:
        result = subprocess.run(["v4l2-ctl", "--list-devices"], capture_output=True, text=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None  # signals "v4l2-ctl not available" to the caller
    return re.findall(r"(/dev/video\d+)", result.stdout)


def _query_video_device(path):
    """Runs `v4l2-ctl --device=<path> --all` and extracts the USB serial
    and whether this node supports real video capture (vs. a metadata-only
    sub-node). Returns None if the device couldn't be queried."""
    try:
        result = subprocess.run(["v4l2-ctl", "--device", path, "--all"], capture_output=True, text=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    text = result.stdout
    serial_match = re.search(r"Serial\s*:\s*(\S+)", text)
    serial = serial_match.group(1) if serial_match else None
    # "Device Caps" reflects what this node actually supports.
    caps_section = text.split("Device Caps")[-1] if "Device Caps" in text else ""
    is_capture = "Video Capture" in caps_section
    return {"serial": serial, "is_capture": is_capture}


def discover_cameras():
    """Finds /dev/videoN for top/side cameras by matching USB serial number
    (not index/path, which aren't stable across reboots/replugs).
    Returns (top_path, side_path, missing_roles)."""
    paths = _list_video_device_paths()
    if paths is None:
        print("ERROR: v4l2-ctl not found. Install it with:\n  sudo apt install v4l-utils")
        return None, None, ["top", "side"]
    serial_to_path = {}
    for path in paths:
        info = _query_video_device(path)
        if info is None or not info["is_capture"] or not info["serial"]:
            continue
        serial_to_path.setdefault(info["serial"], path)
    top_path = serial_to_path.get(TOP_CAM_SERIAL)
    side_path = serial_to_path.get(SIDE_CAM_SERIAL)
    missing_roles = []
    if top_path is None:
        missing_roles.append("top")
    if side_path is None:
        missing_roles.append("side")
    return top_path, side_path, missing_roles



class CameraGrabber:
    """Capture frames in a background thread and retrieve them on demand."""

    def __init__(self, source, backend, width, height, fps):
        self.cap = cv2.VideoCapture(source, backend)

        self.lock = threading.Lock()
        self._stop_event = threading.Event()
        self._stopped = threading.Event()
        self.grab_ok = False
        self.last_error = None

        if not self.cap.isOpened():
            self.cap.release()
            raise RuntimeError(f"Could not open camera: {source}")

        # Request settings; the camera/driver may choose different values.
        requested = {
            cv2.CAP_PROP_FOURCC: cv2.VideoWriter_fourcc(*"MJPG"),
            cv2.CAP_PROP_FRAME_WIDTH: width,
            cv2.CAP_PROP_FRAME_HEIGHT: height,
            cv2.CAP_PROP_FPS: fps,
            cv2.CAP_PROP_BUFFERSIZE: 1,
        }

        for prop, value in requested.items():
            accepted = self.cap.set(prop, value)
            if not accepted:
                print(f"WARNING: Camera {source} did not accept setting {prop}={value}")

        actual_width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_fps = self.cap.get(cv2.CAP_PROP_FPS)
        fourcc_value = int(self.cap.get(cv2.CAP_PROP_FOURCC))
        actual_fourcc = "".join(
            chr((fourcc_value >> (8 * i)) & 0xFF) for i in range(4)
        )

        print(
            f"Camera {source}: {actual_width}x{actual_height}, "
            f"{actual_fps:.1f} FPS reported, FOURCC={actual_fourcc}"
        )

        self.min_interval = 1.0 / max(fps, 1)
        self.thread = threading.Thread(
            target=self._grab_loop,
            name=f"CameraGrabber-{source}",
            daemon=True,
        )
        self.thread.start()

    def is_opened(self):
        return self.cap.isOpened()

    def _grab_loop(self):
        try:
            while not self._stop_event.is_set():
                start_time = time.monotonic()

                with self.lock:
                    if self._stop_event.is_set():
                        break
                    ok = self.cap.grab()
                    self.grab_ok = bool(ok)

                if not ok:
                    self.last_error = "Camera grab failed"
                    self._stop_event.wait(0.05)
                    continue

                self.last_error = None
                elapsed = time.monotonic() - start_time
                self._stop_event.wait(max(0, self.min_interval - elapsed))

        except Exception as exc:
            self.last_error = f"Capture thread error: {exc}"
            print(self.last_error)
        finally:
            self._stopped.set()

    def read_latest(self):
        """Return (True, frame) or (False, None) if no valid frame is available."""
        with self.lock:
            if not self.cap.isOpened() or not self.grab_ok:
                return False, None

            ok, frame = self.cap.retrieve()

        if not ok or frame is None or frame.size == 0:
            self.grab_ok = False
            self.last_error = "Frame retrieval failed"
            return False, None

        return True, frame

    def release(self):
        """Stop capture and release the camera."""
        self._stop_event.set()
        self.thread.join(timeout=3)

        if self.thread.is_alive():
            print("WARNING: Camera capture thread did not stop within 3 seconds.")

        # Avoid releasing the camera while the capture thread is still using it.
        if not self.thread.is_alive():
            with self.lock:
                self.cap.release()



def open_camera_grabber(device_path, width, height, fps):
    """Open a camera using its device path, with an index fallback."""

    match = re.search(r"(\d+)$", device_path)
    sources = [device_path]

    if match:
        index = int(match.group(1))
        sources.append(index)

    for source in sources:
        grabber = None
        try:
            grabber = CameraGrabber(
                source, cv2.CAP_V4L2, width, height, fps
            )
            return grabber

        except (RuntimeError, cv2.error, OSError) as exc:
            print(f"WARNING: Could not initialize camera {source}: {exc}")

            if grabber is not None:
                grabber.release()

    return None




def _clean_binary_mask(gray):
    """Create a cleaned binary mask with configurable foreground polarity."""

    if FOREGROUND_MODE == "bright":
        threshold_mode = cv2.THRESH_BINARY
    elif FOREGROUND_MODE == "dark":
        threshold_mode = cv2.THRESH_BINARY_INV
    else:
        raise ValueError(
            "FOREGROUND_MODE must be 'bright' or 'dark'"
        )

    _, thresh = cv2.threshold(
        gray, 0, 255, threshold_mode + cv2.THRESH_OTSU
    )

    thresh = cv2.morphologyEx(
        thresh,
        cv2.MORPH_OPEN,
        _MORPH_KERNEL,
        iterations=1,
    )

    return thresh


def _largest_valid_contour(contours):
    """Return the largest contour that passes area and solidity checks."""

    if not contours:
        return None

    # Check candidates from largest to smallest.
    for contour in sorted(contours, key=cv2.contourArea, reverse=True):
        area = cv2.contourArea(contour)

        if area <= MIN_AREA:
            # Smaller contours cannot pass the area threshold either.
            break

        hull = cv2.convexHull(contour)
        hull_area = cv2.contourArea(hull)

        if hull_area <= 0:
            continue

        solidity = area / hull_area

        if solidity >= MIN_SOLIDITY:
            return contour

    return None




def detect_top(frame):
    """Returns:
    (found, length_mm, breadth_mm, box_points,
     raw_length_px, raw_breadth_px, clipped)

    Assumption: object length runs vertically in the top-camera image,
    and breadth runs horizontally.
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    thresh = _clean_binary_mask(gray)

    contours, _ = cv2.findContours(
        thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    c = _largest_valid_contour(contours)

    if c is None:
        return False, None, None, None, None, None, False

    frame_h, frame_w = frame.shape[:2]
    x, y, bw, bh = cv2.boundingRect(c)

    clipped = (
        x <= EDGE_MARGIN_PX
        or y <= EDGE_MARGIN_PX
        or (x + bw) >= (frame_w - EDGE_MARGIN_PX)
        or (y + bh) >= (frame_h - EDGE_MARGIN_PX)
    )

    # Use image-axis dimensions, not the rotated rectangle dimensions.
    length_px = float(bh)
    breadth_px = float(bw)

    length_mm = length_px / PPM_TOP_Y
    breadth_mm = breadth_px / PPM_TOP_X

    box = cv2.boxPoints(
        cv2.minAreaRect(c)
    ).astype(int)

    return (
        True,
        length_mm,
        breadth_mm,
        box,
        length_px,
        breadth_px,
        clipped,
    )





def detect_side(frame):
    """Returns (found, length_mm, height_mm, box_points, clipped).

    Assumption: length runs horizontally and height runs vertically
    in the side-camera image.
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    thresh = _clean_binary_mask(gray)

    contours, _ = cv2.findContours(
        thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    c = _largest_valid_contour(contours)

    if c is None:
        return False, None, None, None, False

    frame_h, frame_w = frame.shape[:2]
    x, y, bw, bh = cv2.boundingRect(c)

    clipped = (
        x <= EDGE_MARGIN_PX
        or y <= EDGE_MARGIN_PX
        or (x + bw) >= (frame_w - EDGE_MARGIN_PX)
        or (y + bh) >= (frame_h - EDGE_MARGIN_PX)
    )

    length_px = float(bw)
    height_px = float(bh)

    length_mm = length_px / PPM_SIDE_X
    height_mm = height_px / PPM_SIDE_Y

    box = cv2.boxPoints(
        cv2.minAreaRect(c)
    ).astype(int)

    return True, length_mm, height_mm, box, clipped



def draw_label(frame, text, y, color):
    cv2.putText(frame, text, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)


def get_pi_cpu_temp():
    """Reads Pi CPU temperature (Celsius) from sysfs; returns None if unavailable
    (e.g. running on a non-Pi machine)."""
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            millidegrees = int(f.read().strip())
        return millidegrees / 1000.0
    except (FileNotFoundError, ValueError, OSError):
        return None



def check_pass_fail(avg_length, avg_breadth, avg_height):
    """Return True only when all three measurements are valid and within tolerance."""

    measurements = [avg_length, avg_breadth, avg_height]

    # Reject missing measurements.
    if any(value is None for value in measurements):
        return False

    # Reject NaN, infinity, and other invalid numeric values.
    try:
        if not all(np.isfinite(value) for value in measurements):
            return False
    except (TypeError, ValueError):
        return False

    measured = sorted(measurements)
    target = sorted([
        TARGET_LENGTH_MM,
        TARGET_WIDTH_MM,
        TARGET_HEIGHT_MM,
    ])

    return all(
        abs(measured_value - target_value) <= TOLERANCE_MM
        for measured_value, target_value in zip(measured, target)
    )


def trigger_buzzer(passed):
    """NOTE (CHANGED from original): beeping used to call time.sleep()
    directly on the main thread (0.1s for PASS, 0.5s for FAIL), freezing
    frame grabbing/processing/display for that whole time. It now runs in
    a short-lived daemon thread so the main loop never stalls. Same beep
    durations, same GPIO behavior - just non-blocking."""
    duration = 0.1 if passed else 0.5

    def _beep():
        GPIO.output(BUZZER_PIN, GPIO.HIGH)
        time.sleep(duration)
        GPIO.output(BUZZER_PIN, GPIO.LOW)

    threading.Thread(target=_beep, daemon=True).start()


def _init_log_file():
    """NEW: creates the CSV log with a header row if it doesn't already
    exist / is empty. Called once at startup - no per-frame cost."""
    write_header = (not os.path.exists(LOG_FILE)) or os.path.getsize(LOG_FILE) == 0
    f = open(LOG_FILE, "a", newline="")
    writer = csv.writer(f)
    if write_header:
        writer.writerow(["timestamp", "length_mm", "breadth_mm", "height_mm", "result"])
        f.flush()
    return f, writer


def main():
    cap_top = None
    cap_side = None
    log_file_handle = None
    import signal

    def _handle_sigterm(signum, frame):
        raise KeyboardInterrupt()

    signal.signal(signal.SIGTERM, _handle_sigterm)
    try:
        print("Discovering cameras (matching by USB serial number)...")
        top_path, side_path, missing_roles = discover_cameras()
        if missing_roles:
            for role in missing_roles:
                expected_serial = TOP_CAM_SERIAL if role == "top" else SIDE_CAM_SERIAL
                print(f"ERROR: {role.upper()} camera not found (expected USB serial {expected_serial}). "
                      "Check that it's plugged in and powered.")
            return
        print(f"Found TOP camera  (serial {TOP_CAM_SERIAL})  at {top_path}")
        print(f"Found SIDE camera (serial {SIDE_CAM_SERIAL}) at {side_path}")
        cap_top = open_camera_grabber(top_path, FRAME_WIDTH, FRAME_HEIGHT, 30)
        cap_side = open_camera_grabber(side_path, FRAME_WIDTH, FRAME_HEIGHT, 30)
        top_ok = cap_top is not None
        side_ok = cap_side is not None
        print("Top :", top_ok)
        print("Side:", side_ok)
        if not top_ok or not side_ok:
            if not top_ok:
                print(f"ERROR: TOP camera was found at {top_path} but failed to open.")
            if not side_ok:
                print(f"ERROR: SIDE camera was found at {side_path} but failed to open.")
            print("If this just changed from working to failing after a previous run, the device may still be "
                  "marked busy by the kernel from an unclean shutdown. Try:\n"
                  f"  fuser -v {top_path} {side_path}\n"
                  "to see what's holding it, or unplug/replug the USB cameras.")
            return

        log_file_handle, log_writer = _init_log_file()

        # ---------------- BUZZER SETUP ----------------
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(BUZZER_PIN, GPIO.OUT)
        GPIO.output(BUZZER_PIN, GPIO.LOW)

        state = STATE_IDLE
        frame_count = 0
        display_tick = 0
        consecutive_top_found = 0
        entry_start_time = None   # timestamp of first sighting; anchors the TOTAL_VISIBLE_WINDOW_S budget
        waiting_for_clear = False
        consecutive_top_absent = 0
        measuring_start = None
        samples = []
        has_seen_clean_sample = False   # tracks whether a non-clipped sample has been seen yet this cycle
        result_text = ""
        result_color = (0, 255, 0)
        result_start = None
        last_cycle_end_time = 0.0   # for cooldown between cycles
        last_top_frame = None
        last_side_frame = None
        cpu_temp_c = get_pi_cpu_temp()
        last_temp_read_time = time.time()
        cpu_temp_label = None       # NEW: cached formatted string + color, only rebuilt when temp is re-read
        cpu_temp_color = (0, 255, 0)
        last_printed_state = None

        print("System ready. Waiting for object in top camera view...")

        # CHANGED: stderr suppression is now entered ONCE for the entire
        # capture loop instead of on every processed frame.
        with suppress_c_stderr():
            while True:
                frame_count += 1
                process_top = (frame_count % FRAME_SKIP == 0) or state == STATE_MEASURING
                # REVERTED to original behavior: side camera is processed on
                # the same cadence as the top camera (not skipped in IDLE).
                # The IDLE-skip optimization was removed because it made the
                # side camera feed look laggy / low-FPS on screen.
                process_side = process_top

                top_found = top_length = top_breadth = top_box = None
                top_raw_w = top_raw_h = None
                top_clipped = False
                side_found = side_length = side_height = side_box = None
                side_clipped = False
                top_frame_fresh = False
                side_frame_fresh = False

                if process_top:
                    ret_top, frame_top = cap_top.read_latest()
                    if ret_top and frame_top is not None and frame_top.size > 0:
                        top_frame_fresh = True
                        last_top_frame = frame_top
                        (top_found, top_length, top_breadth, top_box,
                         top_raw_w, top_raw_h, top_clipped) = detect_top(frame_top)
                        if CALIBRATION_DEBUG and top_found:
                            print(f"[CALIBRATION] raw_length_px={top_raw_w:.1f} raw_breadth_px={top_raw_h:.1f}  "
                                  f"(PPM_TOP_Y for known length: {top_raw_w:.1f}/known_mm, "
                                  f"PPM_TOP_X for known breadth: {top_raw_h:.1f}/known_mm)")
                if process_side:
                    ret_side, frame_side = cap_side.read_latest()
                    if ret_side and frame_side is not None and frame_side.size > 0:
                        side_frame_fresh = True
                        last_side_frame = frame_side
                        side_found, side_length, side_height, side_box, side_clipped = detect_side(frame_side)

                # CHANGED: this guard used to check both cameras together,
                # which would have stalled IDLE forever once the side
                # camera stopped being processed there (last_side_frame
                # would never become non-None). Each camera's readiness is
                # now checked independently, only when it was actually
                # supposed to produce a frame this iteration.
                if process_top and last_top_frame is None:
                    time.sleep(0.005)
                    continue
                if process_side and last_side_frame is None:
                    time.sleep(0.005)
                    continue

                # ---------------- STATE MACHINE (camera-only, no ultrasonic) ----------------
                
                if state == STATE_IDLE:
                    if waiting_for_clear:
                        # Do not re-trigger while the previous object remains visible.
                        if process_top:
                            if top_found:
                                consecutive_top_absent = 0
                            else:
                                consecutive_top_absent += 1

                            if consecutive_top_absent >= CLEAR_FRAMES_REQUIRED:
                                waiting_for_clear = False
                                consecutive_top_absent = 0
                                consecutive_top_found = 0
                                entry_start_time = None
                                print("Camera view clear. Ready for next object.")

                    else:
                        in_cooldown = (
                            time.time() - last_cycle_end_time
                        ) < TRIGGER_COOLDOWN_S

                        if in_cooldown:
                            consecutive_top_found = 0
                            entry_start_time = None

                        elif process_top:
                            if top_found:
                                if consecutive_top_found == 0:
                                    entry_start_time = time.time()
                                consecutive_top_found += 1
                            else:
                                consecutive_top_found = 0
                                entry_start_time = None

                        if (
                            not in_cooldown
                            and consecutive_top_found >= CONSECUTIVE_FRAMES_REQUIRED
                        ):
                            state = STATE_MEASURING
                            measuring_start = time.time()
                            samples = []
                            has_seen_clean_sample = False
                            consecutive_top_found = 0


                elif state == STATE_MEASURING:
                    
                    if top_frame_fresh and top_found and not top_clipped:
                        # Top-camera measurements are valid only when the object is fully visible.
                        length_estimate = top_length
                        height_estimate = None

                        # Use side-camera measurements only when its object is also fully visible.
                        if side_frame_fresh and side_found and not side_clipped:
                            length_estimate = (top_length + side_length) / 2.0
                            height_estimate = side_height

                        samples.append((length_estimate, top_breadth, height_estimate))
                        has_seen_clean_sample = True

                    elapsed = time.time() - measuring_start
                    # Remaining budget after subtracting debounce time already spent,
                    # so the result always lands within the block's real visible window.
                    time_spent_before_measuring = measuring_start - (entry_start_time or measuring_start)
                    remaining_budget = max(0.0, TOTAL_VISIBLE_WINDOW_S - time_spent_before_measuring)
                    effective_timeout = max(MIN_MEASURING_DURATION_S,
                                             min(MEASURING_TIMEOUT, MEASURING_DURATION_S, remaining_budget))
                    # Early exit: finalize on already-collected clean samples once the
                    # block starts clipping/leaving, instead of waiting out the full timer.
                    block_now_exiting = (
                        top_frame_fresh
                        and has_seen_clean_sample
                        and (not top_found or top_clipped)
                    )
                    enough_clean_samples = len(samples) >= MIN_CLEAN_SAMPLES_FOR_EARLY_RESULT
                    if elapsed >= effective_timeout or (block_now_exiting and enough_clean_samples):
                        
                        # Only samples containing all three dimensions can produce PASS/FAIL.
                        complete_samples = [s for s in samples if s[2] is not None]

                        if len(complete_samples) < MIN_COMPLETE_SAMPLES:
                            result_text = "INCOMPLETE: Unable to measure all dimensions"
                            result_color = (0, 165, 255)  # Orange in OpenCV's BGR format

                            log_writer.writerow([
                                time.strftime("%Y-%m-%d %H:%M:%S"),
                                "", "", "", "INCOMPLETE",
                            ])
                            log_file_handle.flush()

                        else:
                            lengths = [s[0] for s in complete_samples]
                            breadths = [s[1] for s in complete_samples]
                            heights = [s[2] for s in complete_samples]

                            avg_length = sum(lengths) / len(lengths)
                            avg_breadth = sum(breadths) / len(breadths)
                            avg_height = sum(heights) / len(heights)

                            passed = check_pass_fail(
                                avg_length, avg_breadth, avg_height
                            )

                            result_text = (
                                f"{'PASS' if passed else 'FAIL'}  "
                                f"L:{avg_length:.1f} W:{avg_breadth:.1f} H:{avg_height:.1f}"
                            )
                            result_color = (0, 255, 0) if passed else (0, 0, 255)

                            trigger_buzzer(passed)

                            log_writer.writerow([
                                time.strftime("%Y-%m-%d %H:%M:%S"),
                                f"{avg_length:.2f}",
                                f"{avg_breadth:.2f}",
                                f"{avg_height:.2f}",
                                "PASS" if passed else "FAIL",
                            ])
                            log_file_handle.flush()

                        state = STATE_RESULT
                        result_start = time.time()

                        last_cycle_end_time = time.time()
                        entry_start_time = None

                        waiting_for_clear = True
                        consecutive_top_absent = 0

                elif state == STATE_RESULT:
                    if time.time() - result_start >= RESULT_DISPLAY_TIME:
                        state = STATE_IDLE

                if state != last_printed_state:
                    if state == STATE_IDLE:
                        print("Waiting for object in top camera view...")
                    elif state == STATE_MEASURING:
                        print("Measuring...")
                    elif state == STATE_RESULT:
                        print(result_text)
                    last_printed_state = state

                # ---------------- DISPLAY (optional, throttled) ----------------
                if SHOW_DISPLAY:
                    display_tick += 1
                    # CHANGED: imshow/waitKey (real per-call overhead on a Pi)
                    # now only run every DISPLAY_REFRESH_EVERY_N iterations.
                    # Detection/measurement above is completely unaffected -
                    # this only throttles how often the debug windows repaint.
                    if display_tick % DISPLAY_REFRESH_EVERY_N == 0:
                        if last_top_frame is not None and last_side_frame is not None:
                            top_view = last_top_frame.copy() if process_top else last_top_frame
                            side_view = last_side_frame.copy() if process_side else last_side_frame
                            if top_box is not None:
                                cv2.drawContours(top_view, [top_box], 0, (0, 255, 0), 2)
                            if side_box is not None:
                                cv2.drawContours(side_view, [side_box], 0, (255, 0, 0), 2)
                            if top_found:
                                draw_label(top_view, f"Length : {top_length:.2f} mm", 30, (0, 255, 0))
                                draw_label(top_view, f"Breadth: {top_breadth:.2f} mm", 60, (0, 255, 0))
                            if side_found:
                                draw_label(side_view, f"Length : {side_length:.2f} mm", 30, (255, 0, 0))
                                draw_label(side_view, f"Height : {side_height:.2f} mm", 60, (255, 0, 0))
                            draw_label(top_view, f"STATE: {state}", 90, (255, 255, 0))
                            if SHOW_CPU_TEMP:
                                now = time.time()
                                if now - last_temp_read_time >= TEMP_UPDATE_INTERVAL or cpu_temp_label is None:
                                    cpu_temp_c = get_pi_cpu_temp()
                                    last_temp_read_time = now
                                    # NEW: format the label + pick the color only when the
                                    # temperature is actually re-read, not every displayed frame.
                                    if cpu_temp_c is not None:
                                        if cpu_temp_c >= 80:
                                            cpu_temp_color = (0, 0, 255)
                                        elif cpu_temp_c >= 70:
                                            cpu_temp_color = (0, 255, 255)
                                        else:
                                            cpu_temp_color = (0, 255, 0)
                                        cpu_temp_label = f"CPU: {cpu_temp_c:.1f} C"
                                if cpu_temp_label is not None:
                                    draw_label(top_view, cpu_temp_label, 150, cpu_temp_color)
                            if state == STATE_RESULT:
                                draw_label(top_view, result_text, 120, result_color)
                            cv2.imshow("Top Camera", top_view)
                            cv2.imshow("Side Camera", side_view)
                        if cv2.waitKey(1) & 0xFF == ord('q'):
                            break

    except KeyboardInterrupt:
        pass
    finally:
        if cap_top is not None:
            cap_top.release()
        if cap_side is not None:
            cap_side.release()
        if log_file_handle is not None:
            log_file_handle.close()
        if SHOW_DISPLAY:
            cv2.destroyAllWindows()
        GPIO.cleanup()


if __name__ == "__main__":
    main()