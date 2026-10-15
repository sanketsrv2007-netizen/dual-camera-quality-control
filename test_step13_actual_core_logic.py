"""Step 13: exercise the actual helper functions in final-code_step11.py.

This is not a full main-loop or hardware test. It directly tests the production
module's measurement, timing, frame-pairing, display-copy and detection-streak helpers.
Run: python test_step13_actual_core_logic.py
"""
import importlib.util
import sys
import types
from pathlib import Path

# Allow importing the Raspberry-Pi-oriented module on Windows without GPIO.
rpi = types.ModuleType("RPi")
gpio = types.ModuleType("RPi.GPIO")
rpi.GPIO = gpio
sys.modules.setdefault("RPi", rpi)
sys.modules.setdefault("RPi.GPIO", gpio)

path = Path(__file__).with_name("final-code_step11.py")
spec = importlib.util.spec_from_file_location("final_code_step11_step13", path)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

checks = []
def check(name, condition):
    ok = bool(condition)
    print(f'{"PASS" if ok else "FAIL"}: {name}')
    checks.append(ok)

# Pass/fail decisions call the real production helper.
check("nominal dimensions pass", app.check_pass_fail(50, 55, 50))
check("90-degree in-plane rotation passes", app.check_pass_fail(55, 50, 50))
check("length outside tolerance fails", not app.check_pass_fail(60, 55, 50))
check("height cannot be swapped with top-plane dimensions", not app.check_pass_fail(50, 50, 55))
check("non-finite measurement fails", not app.check_pass_fail(float("nan"), 55, 50))

# Timestamp-pairing helper.
check("frames within timestamp tolerance pair", app.camera_frames_are_paired(1.00, 1.05))
check("frames beyond timestamp tolerance are rejected", not app.camera_frames_are_paired(1.00, 1.20))
check("missing timestamp is rejected", not app.camera_frames_are_paired(None, 1.01))
check("non-finite timestamp is rejected", not app.camera_frames_are_paired(float("inf"), 1.01))

# Measurement fusion helper.
fused = app.combine_view_measurements(50, 55, 51, 50)
check("consistent views fuse into three dimensions", fused is not None and len(fused) == 3)
check("inconsistent side length is rejected", app.combine_view_measurements(50, 55, 70, 50) is None)
check("height remains independent during fusion", fused is not None and fused[2] == 50)

# Timeout budget helper uses monotonic-style timestamps.
check("measuring timeout respects configured cap", app.calculate_effective_timeout(10.0, 10.2) <= app.MEASURING_DURATION_S)
check("exhausted visible-window budget returns zero", app.calculate_effective_timeout(10.0, 12.0) == 0.0)
check("missing measuring start returns zero", app.calculate_effective_timeout(10.0, None) == 0.0)

# Detection streak and frame-copy helper.
check("found frame increments detection streak once", app.update_detection_streak(1, True) == 2)
check("missing object resets detection streak", app.update_detection_streak(2, False) == 0)
import numpy as np
original = np.zeros((4, 4, 3), dtype=np.uint8)
top_view, side_view = app.make_display_views(original, original)
top_view[0, 0] = 255
check("display annotations can use independent copies", original[0, 0, 0] == 0 and side_view[0, 0, 0] == 0)

print(f"\n{sum(checks)}/{len(checks)} Step 13 actual-helper tests passed.")
if not all(checks):
    raise SystemExit(1)
