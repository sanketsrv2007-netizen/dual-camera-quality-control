"""
Offline tests for inspection timing budget and state-machine timeouts.

Run:
    python test_state_machine_timing.py

These tests validate the timing helper, not the physical conveyor.
"""
import importlib.util
import sys
import types
from pathlib import Path

rpi = types.ModuleType("RPi")
gpio = types.ModuleType("RPi.GPIO")
rpi.GPIO = gpio
sys.modules.setdefault("RPi", rpi)
sys.modules.setdefault("RPi.GPIO", gpio)

main_path = Path(__file__).with_name("final-code_step11.py")
spec = importlib.util.spec_from_file_location("final_code_step5", main_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

cases = [
    ("full budget remains", 10.0, 10.0, 1.0),
    ("debounce used half the window", 10.0, 11.0, 1.0),
    ("debounce leaves 0.2 seconds", 10.0, 11.8, 0.2),
    ("debounce used the entire window", 10.0, 12.0, 0.0),
    ("debounce exceeded the window", 10.0, 12.4, 0.0),
    ("missing entry time falls back safely", None, 12.0, 1.0),
    ("measuring start missing", 10.0, None, 0.0),
]

failures = []
for name, entry, measuring, expected in cases:
    actual = module.calculate_effective_timeout(entry, measuring)
    ok = abs(actual - expected) < 1e-9
    print(f'{"PASS" if ok else "FAIL"}: {name} (got {actual:.3f}, expected {expected:.3f})')
    if not ok:
        failures.append(name)

# Check that timeout never exceeds the remaining total window for several
# representative debounce delays.
for delay in [0.0, 0.25, 0.75, 1.5, 1.99, 2.0, 2.2]:
    actual = module.calculate_effective_timeout(100.0, 100.0 + delay)
    remaining = max(0.0, module.TOTAL_VISIBLE_WINDOW_S - delay)
    ok = actual <= remaining + 1e-9
    print(f'{"PASS" if ok else "FAIL"}: deadline invariant for debounce={delay:.2f}s')
    if not ok:
        failures.append(f"deadline invariant {delay}")

print(f"\\n{14 - len(failures)}/14 timing checks passed.")
if failures:
    raise SystemExit(1)
