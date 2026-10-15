"""
Offline tests for timestamp pairing and per-frame timestamp association.

Run:
    python test_capture_timestamp_pairing.py

These are simulated tests. They do not validate timing on physical webcams.
"""
import importlib.util
import sys
import threading
import types
from pathlib import Path

import numpy as np

# Stub Raspberry Pi GPIO so the program can be imported on Windows.
rpi = types.ModuleType("RPi")
gpio = types.ModuleType("RPi.GPIO")
rpi.GPIO = gpio
sys.modules.setdefault("RPi", rpi)
sys.modules.setdefault("RPi.GPIO", gpio)

main_path = Path(__file__).with_name("final-code_step11.py")
spec = importlib.util.spec_from_file_location("final_code_step4", main_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_pair(name, a, b, expected):
    actual = module.camera_frames_are_paired(a, b)
    ok = actual is expected
    print(f'{"PASS" if ok else "FAIL"}: {name} (got {actual}, expected {expected})')
    return ok


class FakeCapture:
    def __init__(self, frame, opened=True, retrieve_ok=True):
        self.frame = frame
        self.opened = opened
        self.retrieve_ok = retrieve_ok

    def isOpened(self):
        return self.opened

    def retrieve(self):
        return self.retrieve_ok, self.frame


def make_grabber(timestamp, frame=None, retrieve_ok=True):
    grabber = module.CameraGrabber.__new__(module.CameraGrabber)
    grabber.cap = FakeCapture(
        np.zeros((2, 2, 3), dtype=np.uint8) if frame is None else frame,
        retrieve_ok=retrieve_ok,
    )
    grabber.lock = threading.Lock()
    grabber.grab_ok = True
    grabber._grab_sequence = 4
    grabber._last_retrieved_sequence = 3
    grabber.last_grab_timestamp = timestamp
    grabber.last_retrieved_timestamp = None
    grabber.last_error = None
    return grabber


checks = [
    test_pair("timestamps within 100 ms", 10.00, 10.08, True),
    test_pair("timestamps exactly 100 ms apart", 10.00, 10.10, True),
    test_pair("timestamps beyond 100 ms", 10.00, 10.11, False),
    test_pair("missing timestamp", None, 10.00, False),
    test_pair("non-finite timestamp", float("nan"), 10.00, False),
]

grabber = make_grabber(12.345)
ok, frame = grabber.read_latest()
case_ok = ok and frame is not None and grabber.last_retrieved_timestamp == 12.345
print(f'{"PASS" if case_ok else "FAIL"}: returned frame keeps its own timestamp')
checks.append(case_ok)

grabber = make_grabber(12.345, retrieve_ok=False)
ok, frame = grabber.read_latest()
case_ok = (not ok) and frame is None and grabber.last_retrieved_timestamp is None
print(f'{"PASS" if case_ok else "FAIL"}: failed retrieval does not retain a usable timestamp')
checks.append(case_ok)

print(f"\\n{sum(checks)}/{len(checks)} timestamp checks passed.")
if not all(checks):
    raise SystemExit(1)
