"""Offline tests for the current camera health watchdog in final-code_step11.py.

These tests use fake camera/thread objects; they do not test physical webcams.
Run: python test_camera_health_watchdog.py
"""
import importlib.util
import sys
import types
from pathlib import Path

# Stub Raspberry Pi GPIO so the program can be imported on a laptop.
rpi_module = types.ModuleType("RPi")
gpio_module = types.ModuleType("RPi.GPIO")
rpi_module.GPIO = gpio_module
sys.modules.setdefault("RPi", rpi_module)
sys.modules.setdefault("RPi.GPIO", gpio_module)

source = Path(__file__).with_name("final-code_step11.py")
if not source.is_file():
    raise FileNotFoundError(
        f"Missing {source.name}. Keep it in the same folder as this test."
    )
spec = importlib.util.spec_from_file_location("final_code_step11", source)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class FakeCap:
    def __init__(self, opened=True):
        self.opened = opened

    def isOpened(self):
        return self.opened


class FakeThread:
    def __init__(self, alive=True):
        self.alive = alive

    def is_alive(self):
        return self.alive


def fake_grabber(*, opened=True, alive=True, started=10.0,
                 last_frame=10.0, error=None):
    obj = module.CameraGrabber.__new__(module.CameraGrabber)
    obj.cap = FakeCap(opened)
    obj.thread = FakeThread(alive)
    obj.started_timestamp = started
    obj.last_successful_frame_timestamp = last_frame
    obj.last_error = error
    return obj


def run_tests():
    tests = [
        ("healthy camera", lambda: fake_grabber().health_issue(now=10.5) is None),
        ("closed device detected", lambda: fake_grabber(opened=False).health_issue(now=10.5) == "camera device is no longer open"),
        ("dead thread detected", lambda: fake_grabber(alive=False, error="Capture thread error: test").health_issue(now=10.5) == "Capture thread error: test"),
        ("stale successfully delivered frame detected", lambda: "no successfully delivered frame" in fake_grabber(last_frame=10.0).health_issue(now=12.0)),
        ("startup grace respected", lambda: fake_grabber(last_frame=None, started=10.0).health_issue(now=12.0) is None),
        ("missing first frame after grace detected", lambda: "first frame" in fake_grabber(last_frame=None, started=10.0).health_issue(now=14.0)),
        ("capture error reported for stale stream", lambda: fake_grabber(last_frame=10.0, error="Camera retrieve failed").health_issue(now=12.0) == "Camera retrieve failed"),
    ]
    failed = 0
    for name, test in tests:
        try:
            passed = bool(test())
        except Exception as exc:
            passed = False
            print(f"FAIL: {name}: raised {exc!r}")
        else:
            print(f"{'PASS' if passed else 'FAIL'}: {name}")
        failed += not passed
    print(f"\n{len(tests) - failed}/{len(tests)} camera watchdog tests passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(run_tests())
