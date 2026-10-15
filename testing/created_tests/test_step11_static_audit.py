"""Regression tests for Step 11 static-audit fixes; no physical hardware required."""
import importlib.util
import io
import sys
import types
from pathlib import Path

source = Path(__file__).with_name("final-code_step11.py")
spec = importlib.util.spec_from_file_location("final_code_step11", source)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class FakeCap:
    def isOpened(self):
        return True


class FakeThread:
    def is_alive(self):
        return True


def make_grabber(*, started=10.0, last_grab=19.9, last_frame=None, error=None):
    obj = module.CameraGrabber.__new__(module.CameraGrabber)
    obj.cap = FakeCap()
    obj.thread = FakeThread()
    obj.started_timestamp = started
    obj.last_grab_timestamp = last_grab
    obj.last_successful_frame_timestamp = last_frame
    obj.last_error = error
    return obj


def test_health_requires_successfully_delivered_frame():
    obj = make_grabber(started=10.0, last_grab=19.9, last_frame=None)
    issue = obj.health_issue(now=14.0, startup_grace=3.0)
    assert issue is not None and "first frame" in issue


def test_health_uses_successful_delivery_time():
    obj = make_grabber(started=0.0, last_grab=10.0, last_frame=19.8)
    assert obj.health_issue(now=20.0, stale_after=1.5) is None
    issue = obj.health_issue(now=21.5, stale_after=1.5)
    assert issue is not None and "successfully delivered frame" in issue


def test_stderr_suppression_is_disabled_by_default():
    assert module.SUPPRESS_C_STDERR is False
    old_stderr = sys.stderr
    capture = io.StringIO()
    try:
        sys.stderr = capture
        with module.suppress_c_stderr():
            print("traceback-like diagnostic", file=sys.stderr)
        assert "traceback-like diagnostic" in capture.getvalue()
    finally:
        sys.stderr = old_stderr


def test_source_compiles():
    compile(source.read_text(encoding="utf-8"), str(source), "exec")


def run():
    tests = [
        ("camera health requires a successfully delivered frame", test_health_requires_successfully_delivered_frame),
        ("camera health uses successful delivery timestamp", test_health_uses_successful_delivery_time),
        ("stderr remains visible by default", test_stderr_suppression_is_disabled_by_default),
        ("Step 11 source compiles", test_source_compiles),
    ]
    failures = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS: {name}")
        except Exception as exc:
            failures += 1
            print(f"FAIL: {name}: {exc!r}")
    print(f"\n{len(tests) - failures}/{len(tests)} Step 11 static-audit tests passed")
    return int(failures != 0)


if __name__ == "__main__":
    raise SystemExit(run())
