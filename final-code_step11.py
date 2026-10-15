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
import platform
import shutil

# GPIO is only needed when physical indicators are enabled during final
# hardware integration. Keeping this import optional allows safe code review
# and diagnostics on a development laptop without pretending GPIO is available.
try:
    import RPi.GPIO as GPIO
except ImportError:
    GPIO = None


SUPPRESS_C_STDERR = False  # Keep diagnostics visible by default; enable only after field validation.


@contextlib.contextmanager
def suppress_c_stderr():
    """Optionally silence OS-level stderr; disabled by default because fd 2
    also carries Python tracebacks and important camera/driver diagnostics.

    When explicitly enabled, this hides harmless libjpeg-turbo "Corrupt JPEG
    data" warnings from cheap USB webcam MJPG decoding.

    Suppression is opt-in. Keep it disabled while debugging so Python tracebacks
    and camera-driver errors remain visible."""
    if not SUPPRESS_C_STDERR:
        yield
        return

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

# Actual measuring time shrinks dynamically to fit the total visible-window budget.
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

# -----------------------------
# TARGET DIMENSIONS (PASS/FAIL)
# -----------------------------
TARGET_LENGTH_MM = 50
TARGET_WIDTH_MM = 55
TARGET_HEIGHT_MM = 50
TOLERANCE_MM = 2.5          # ASSUMPTION - not specified in the doc, adjust as needed
# Reject camera pairs captured too far apart in time; tune with real camera FPS.
MAX_FRAME_PAIR_DELTA_S = 0.10
# Stop inspection if a camera stops producing fresh frames for this long.
CAMERA_STALE_TIMEOUT_S = 1.5
CAMERA_STARTUP_GRACE_S = 3.0
# Side-camera length must agree with one of the two top-view dimensions.
CAMERA_LENGTH_AGREEMENT_MM = 3.0
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
        # A sequence number lets read_latest() distinguish a newly grabbed
        # frame from a frame that has already been delivered to the main loop.
        self._grab_sequence = 0
        self._last_retrieved_sequence = 0
        self.last_grab_timestamp = None
        # Health is based on frames successfully delivered to the processing loop,
        # not merely successful low-level grab() calls.
        self.last_successful_frame_timestamp = None
        self.started_timestamp = time.monotonic()
        # Timestamp belonging to the frame most recently returned by read_latest().
        self.last_retrieved_timestamp = None

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

    def health_issue(self, now=None, stale_after=CAMERA_STALE_TIMEOUT_S,
                     startup_grace=CAMERA_STARTUP_GRACE_S):
        """Return a human-readable fault if capture has stopped or gone stale.

        This deliberately fails safe instead of allowing old frames to be used
        for a PASS/FAIL decision. It reports the fault; automatic reconnection
        is deferred until device behavior can be validated on the Raspberry Pi.
        """
        if now is None:
            now = time.monotonic()
        if not self.cap.isOpened():
            return "camera device is no longer open"
        if not self.thread.is_alive():
            return self.last_error or "camera capture thread has stopped"
        last_frame = self.last_successful_frame_timestamp
        if last_frame is None:
            if now - self.started_timestamp > startup_grace:
                return self.last_error or "camera has not delivered its first frame"
            return None
        if now - last_frame > stale_after:
            return self.last_error or (
                f"no successfully delivered frame for {now - last_frame:.1f} seconds"
            )
        return None

    def _grab_loop(self):
        try:
            while not self._stop_event.is_set():
                start_time = time.monotonic()

                with self.lock:
                    if self._stop_event.is_set():
                        break
                    ok = self.cap.grab()
                    self.grab_ok = bool(ok)
                    if ok:
                        self._grab_sequence += 1
                        self.last_grab_timestamp = time.monotonic()

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
        """Return a fresh frame and the timestamp associated with that exact grab."""
        with self.lock:
            if not self.cap.isOpened() or not self.grab_ok:
                return False, None

            sequence = self._grab_sequence
            if sequence == self._last_retrieved_sequence:
                return False, None

            # Capture frame and timestamp while holding the same lock so the
            # background thread cannot update the timestamp between operations.
            frame_timestamp = self.last_grab_timestamp
            ok, frame = self.cap.retrieve()
            self._last_retrieved_sequence = sequence

            if not ok or frame is None or frame.size == 0:
                self.grab_ok = False
                self.last_error = "Frame retrieval failed"
                self.last_retrieved_timestamp = None
                return False, None

            self.last_error = None
            self.last_retrieved_timestamp = frame_timestamp
            self.last_successful_frame_timestamp = time.monotonic()
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



def update_detection_streak(current_count, object_found):
    """Update the consecutive fresh-frame detection count exactly once."""
    if object_found:
        return current_count + 1
    return 0


def make_display_views(top_frame, side_frame):
    """Return independent display copies so annotations never alter captured frames."""
    top_view = top_frame.copy() if top_frame is not None else None
    side_view = side_frame.copy() if side_frame is not None else None
    return top_view, side_view


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



def calculate_effective_timeout(entry_start_time, measuring_start_time):
    """Return measuring time left inside the total visible-window budget.

    Both inputs must use time.monotonic() timestamps. If debounce consumed the
    full window, return zero rather than forcing a minimum duration that
    violates TOTAL_VISIBLE_WINDOW_S.
    """
    if measuring_start_time is None:
        return 0.0
    if entry_start_time is None:
        entry_start_time = measuring_start_time

    time_spent_before_measuring = max(
        0.0, measuring_start_time - entry_start_time
    )
    remaining_budget = max(
        0.0, TOTAL_VISIBLE_WINDOW_S - time_spent_before_measuring
    )
    return min(
        MEASURING_TIMEOUT,
        MEASURING_DURATION_S,
        remaining_budget,
    )


def camera_frames_are_paired(top_timestamp, side_timestamp):
    """Return True only when both frame timestamps are available and close enough."""
    if top_timestamp is None or side_timestamp is None:
        return False
    try:
        if not np.isfinite(top_timestamp) or not np.isfinite(side_timestamp):
            return False
    except (TypeError, ValueError):
        return False
    return abs(top_timestamp - side_timestamp) <= MAX_FRAME_PAIR_DELTA_S


def combine_view_measurements(top_length, top_breadth, side_length, side_height):
    """Combine views when the object may be rotated 90 degrees in the top plane.

    Returns canonical (length, width, height), where length is the dimension
    expected to be near TARGET_LENGTH_MM and width near TARGET_WIDTH_MM.
    Returns None when the side length does not corroborate either top dimension.
    """
    values = [top_length, top_breadth, side_length, side_height]
    if any(value is None for value in values):
        return None
    try:
        if not all(np.isfinite(value) for value in values):
            return None
    except (TypeError, ValueError):
        return None

    # The side camera's horizontal axis may correspond to either top-view axis
    # after a 90-degree rotation. Match it to the closest top-view dimension.
    top_dimensions = [float(top_length), float(top_breadth)]
    matched_index = min(
        range(2), key=lambda index: abs(top_dimensions[index] - float(side_length))
    )
    disagreement = abs(top_dimensions[matched_index] - float(side_length))
    if disagreement > CAMERA_LENGTH_AGREEMENT_MM:
        return None

    # Fuse the side length with the corresponding top dimension only. Do not
    # average it with an unrelated dimension when the object is rotated.
    top_dimensions[matched_index] = (top_dimensions[matched_index] + float(side_length)) / 2.0

    # Canonicalize in-plane dimensions so 90-degree rotation does not change
    # the reported L/W labels. Height always remains the side camera's vertical
    # measurement and is never swapped with a top-view dimension.
    length_mm, width_mm = sorted(top_dimensions)
    return length_mm, width_mm, float(side_height)


def check_pass_fail(avg_length, avg_breadth, avg_height):
    """Validate length/width (allowing in-plane rotation) and height separately."""

    measurements = [avg_length, avg_breadth, avg_height]

    # Reject missing measurements.
    if any(value is None for value in measurements):
        return False

    # Reject NaN, infinity, and non-numeric values.
    try:
        if not all(np.isfinite(value) for value in measurements):
            return False
    except (TypeError, ValueError):
        return False

    # Height is tied to the side-camera vertical measurement; never swap it
    # with a top-plane dimension just because the numeric sizes are similar.
    height_ok = abs(avg_height - TARGET_HEIGHT_MM) <= TOLERANCE_MM
    if not height_ok:
        return False

    # The object may be rotated 90 degrees on the conveyor, so allow the
    # top-plane length and breadth labels to be exchanged.
    normal_orientation = (
        abs(avg_length - TARGET_LENGTH_MM) <= TOLERANCE_MM
        and abs(avg_breadth - TARGET_WIDTH_MM) <= TOLERANCE_MM
    )
    rotated_orientation = (
        abs(avg_length - TARGET_WIDTH_MM) <= TOLERANCE_MM
        and abs(avg_breadth - TARGET_LENGTH_MM) <= TOLERANCE_MM
    )

    return normal_orientation or rotated_orientation


def trigger_buzzer(passed):
    if GPIO is None:
        raise RuntimeError(
            "RPi.GPIO is unavailable. Buzzer output requires Raspberry Pi hardware "
            "and the RPi.GPIO package."
        )
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


def print_runtime_preflight():
    """Print deployment diagnostics without opening cameras or GPIO pins."""
    print("\n=== Runtime preflight ===")
    print(f"OS: {platform.system()} {platform.release()}")
    print(f"Python: {sys.version.split()[0]}")
    print(f"OpenCV: {cv2.__version__}")
    print(f"NumPy: {np.__version__}")
    print(f"v4l2-ctl: {shutil.which('v4l2-ctl') or 'NOT FOUND'}")
    print(f"RPi.GPIO: {'available' if GPIO is not None else 'not installed / not available'}")
    if platform.system() != "Linux":
        print("WARNING: This program's camera discovery expects Raspberry Pi/Linux video devices.")
    if shutil.which("v4l2-ctl") is None:
        print("WARNING: Install v4l-utils on the Raspberry Pi: sudo apt install v4l-utils")
    print("Note: camera serial matching, real frame capture, calibration, and GPIO are not tested by this preflight.")
    print("=========================\n")


def main():
    print_runtime_preflight()
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

        # Indicator/buzzer GPIO setup is deferred until final hardware integration.

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
        last_cycle_end_time = 0.0   # monotonic timestamp for cooldown between cycles
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
                # Watchdog: never keep inspecting with stale frames or a dead
                # capture thread. This is a fail-safe stop, not auto-reconnect.
                now_monotonic = time.monotonic()
                top_fault = cap_top.health_issue(now=now_monotonic)
                side_fault = cap_side.health_issue(now=now_monotonic)
                if top_fault or side_fault:
                    faults = []
                    if top_fault:
                        faults.append(f"TOP camera: {top_fault}")
                    if side_fault:
                        faults.append(f"SIDE camera: {side_fault}")
                    print("CAMERA ERROR — stopping inspection safely: " + "; ".join(faults))
                    if state == STATE_MEASURING and log_writer is not None:
                        log_writer.writerow([
                            time.strftime("%Y-%m-%d %H:%M:%S"),
                            "", "", "", "INCOMPLETE",
                        ])
                        log_file_handle.flush()
                    break

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
                # These flags are true only when read_latest() returns a new
                # camera grab, not merely a previously retrieved image.
                top_frame_fresh = False
                side_frame_fresh = False
                top_frame_timestamp = None
                side_frame_timestamp = None

                if process_top:
                    ret_top, frame_top = cap_top.read_latest()
                    if ret_top and frame_top is not None and frame_top.size > 0:
                        top_frame_fresh = True
                        top_frame_timestamp = cap_top.last_retrieved_timestamp
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
                        side_frame_timestamp = cap_side.last_retrieved_timestamp
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
                        if top_frame_fresh:
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
                            time.monotonic() - last_cycle_end_time
                        ) < TRIGGER_COOLDOWN_S

                        if in_cooldown:
                            consecutive_top_found = 0
                            entry_start_time = None

                        elif top_frame_fresh:
                            if top_found:
                                if consecutive_top_found == 0:
                                    entry_start_time = time.monotonic()
                                consecutive_top_found += 1
                            else:
                                consecutive_top_found = 0
                                entry_start_time = None

                        if (
                            not in_cooldown
                            and consecutive_top_found >= CONSECUTIVE_FRAMES_REQUIRED
                        ):
                            state = STATE_MEASURING
                            measuring_start = time.monotonic()
                            samples = []
                            has_seen_clean_sample = False
                            consecutive_top_found = 0


                elif state == STATE_MEASURING:
                    
                    if (
                        top_frame_fresh and top_found and not top_clipped
                        and side_frame_fresh and side_found and not side_clipped
                        and top_frame_timestamp is not None
                        and side_frame_timestamp is not None
                        and camera_frames_are_paired(top_frame_timestamp, side_frame_timestamp)
                    ):
                        # Require a close-in-time pair and a side length that
                        # corroborates one of the top-view dimensions. Because
                        # 90-degree rotation is allowed, side length may match
                        # either top dimension; height is never swapped.
                        combined = combine_view_measurements(
                            top_length, top_breadth, side_length, side_height
                        )
                        if combined is not None:
                            samples.append(combined)
                            has_seen_clean_sample = True

                    elapsed = time.monotonic() - measuring_start
                    # Respect the overall visible-window deadline. If debounce
                    # consumed the budget, finalize immediately instead of
                    # forcing a minimum measuring period beyond the deadline.
                    effective_timeout = calculate_effective_timeout(
                        entry_start_time, measuring_start
                    )
                    # Early exit: finalize on already-collected clean samples once the
                    # block starts clipping/leaving, instead of waiting out the full timer.
                    block_now_exiting = (
                        top_frame_fresh
                        and has_seen_clean_sample
                        and (not top_found or top_clipped)
                    )
                    enough_clean_samples = len(samples) >= MIN_CLEAN_SAMPLES_FOR_EARLY_RESULT
                    if elapsed >= effective_timeout or (block_now_exiting and enough_clean_samples):
                        
                        # Samples are added only when both views are fresh, close
                        # in time, fully visible, and mutually consistent.
                        complete_samples = samples

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

                            # Indicator/buzzer activation is intentionally deferred until final hardware integration.

                            log_writer.writerow([
                                time.strftime("%Y-%m-%d %H:%M:%S"),
                                f"{avg_length:.2f}",
                                f"{avg_breadth:.2f}",
                                f"{avg_height:.2f}",
                                "PASS" if passed else "FAIL",
                            ])
                            log_file_handle.flush()

                        state = STATE_RESULT
                        result_start = time.monotonic()

                        last_cycle_end_time = time.monotonic()
                        entry_start_time = None

                        waiting_for_clear = True
                        consecutive_top_absent = 0

                elif state == STATE_RESULT:
                    if time.monotonic() - result_start >= RESULT_DISPLAY_TIME:
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
                            # Always annotate copies. The stored frames are the
                            # unmodified camera images used for later processing.
                            top_view, side_view = make_display_views(
                                last_top_frame, last_side_frame
                            )
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
        # No GPIO peripherals are initialized in this software-only stage.


if __name__ == "__main__":
    main()