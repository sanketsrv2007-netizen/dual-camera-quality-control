"""Offline tests for the rotation-aware camera measurement fusion.
Run from the project folder with: python test_measurement_fusion.py
These tests validate software logic, not physical camera calibration or synchronization.
"""
import importlib.util
import math
import sys
import types
from pathlib import Path

# Minimal import stub for Raspberry Pi GPIO so the logic can be tested on Windows.
rpi = types.ModuleType("RPi")
gpio = types.ModuleType("RPi.GPIO")
rpi.GPIO = gpio
sys.modules.setdefault("RPi", rpi)
sys.modules.setdefault("RPi.GPIO", gpio)

code_path = Path(__file__).with_name("final-code_step11.py")
spec = importlib.util.spec_from_file_location("final_code_step3", code_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

cases = [
    ("side length matches top length", (50.0, 55.0, 50.5, 50.0), (50.25, 55.0, 50.0)),
    ("90-degree rotation: side matches top breadth", (55.0, 50.0, 55.5, 50.0), (50.0, 55.25, 50.0)),
    ("rotated with side matching the other top axis", (55.0, 50.0, 49.5, 50.0), (49.75, 55.0, 50.0)),
    ("inconsistent side length rejected", (50.0, 55.0, 62.0, 50.0), None),
    ("missing side measurement rejected", (50.0, 55.0, None, 50.0), None),
    ("missing height rejected", (50.0, 55.0, 50.0, None), None),
    ("NaN rejected", (50.0, math.nan, 50.0, 50.0), None),
    ("infinite measurement rejected", (50.0, 55.0, math.inf, 50.0), None),
]

# Timestamp-pairing tests: timestamps are illustrative monotonic seconds.
timestamp_cases = [
    ("near-simultaneous frames accepted", 10.00, 10.05, True),
    ("frames at threshold accepted", 10.00, 10.10, True),
    ("frames too far apart rejected", 10.00, 10.15, False),
    ("missing timestamp rejected", None, 10.05, False),
]

failed = []
for name, top_ts, side_ts, expected in timestamp_cases:
    actual = module.camera_frames_are_paired(top_ts, side_ts)
    ok = actual is expected
    print(f'{"PASS" if ok else "FAIL"}: {name}; got {actual}, expected {expected}')
    if not ok:
        failed.append(name)

for name, args, expected in cases:
    actual = module.combine_view_measurements(*args)
    if expected is None:
        ok = actual is None
    else:
        ok = actual is not None and all(abs(a - b) < 1e-6 for a, b in zip(actual, expected))
    print(f'{"PASS" if ok else "FAIL"}: {name}; got {actual}, expected {expected}')
    if not ok:
        failed.append(name)

# Test the resulting canonical dimensions against the pass/fail function.
normal = module.combine_view_measurements(50.0, 55.0, 50.2, 50.0)
rotated = module.combine_view_measurements(55.0, 50.0, 54.8, 50.0)
for name, dims in [("normal orientation passes", normal), ("rotated orientation passes", rotated)]:
    actual = dims is not None and module.check_pass_fail(*dims)
    print(f'{"PASS" if actual else "FAIL"}: {name}; dimensions={dims}')
    if not actual:
        failed.append(name)

total_tests = len(cases) + 2 + len(timestamp_cases)
print(f"\n{total_tests - len(failed)}/{total_tests} tests passed.")
if failed:
    raise SystemExit(1)
