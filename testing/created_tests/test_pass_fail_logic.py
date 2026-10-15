"""
Offline tests for check_pass_fail() in final-code_step11.py.
Run from the project folder with:
    python test_pass_fail_logic.py

These tests validate software decision logic only. They do not validate camera
calibration or real-world measurement accuracy.
"""
import importlib.util
import math
import sys
import types
from pathlib import Path

# The main program imports Raspberry Pi GPIO, which is unavailable on Windows.
# Provide a minimal import stub; these tests do not use GPIO.
rpi = types.ModuleType("RPi")
gpio = types.ModuleType("RPi.GPIO")
rpi.GPIO = gpio
sys.modules.setdefault("RPi", rpi)
sys.modules.setdefault("RPi.GPIO", gpio)

code_path = Path(__file__).with_name("final-code_step11.py")
spec = importlib.util.spec_from_file_location("final_code_step2", code_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

cases = [
    ("nominal dimensions", 50, 55, 50, True),
    ("valid lower tolerance limits", 47.5, 52.5, 47.5, True),
    ("valid upper tolerance limits", 52.5, 57.5, 52.5, True),
    ("90-degree in-plane rotation", 55, 50, 50, True),
    ("length too large", 60, 55, 50, False),
    ("width too large", 50, 65, 50, False),
    ("height too large", 50, 55, 55, False),
    ("height/width swapped", 50, 50, 55, False),
    ("missing length", None, 55, 50, False),
    ("NaN height", 50, 55, math.nan, False),
    ("infinite width", 50, math.inf, 50, False),
    ("non-numeric value", "bad", 55, 50, False),
]

failures = []
for name, length, breadth, height, expected in cases:
    try:
        actual = module.check_pass_fail(length, breadth, height)
        passed = actual is expected
    except Exception as exc:
        actual = f"{type(exc).__name__}: {exc}"
        passed = False

    print(f'{"PASS" if passed else "FAIL"}: {name} (got {actual}, expected {expected})')
    if not passed:
        failures.append(name)

print(f"\\n{len(cases) - len(failures)}/{len(cases)} tests passed.")
if failures:
    raise SystemExit(1)
