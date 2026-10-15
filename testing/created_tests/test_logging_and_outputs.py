"""
Offline tests for inspection CSV logging and indicator deferral.

Run:
    python test_logging_and_outputs.py

This checks CSV header/row behavior and verifies that the main inspection flow
does not invoke the buzzer. It does not simulate the entire camera state machine.
"""
import csv
import importlib.util
import sys
import tempfile
import types
from pathlib import Path

rpi = types.ModuleType("RPi")
gpio = types.ModuleType("RPi.GPIO")
rpi.GPIO = gpio
sys.modules.setdefault("RPi", rpi)
sys.modules.setdefault("RPi.GPIO", gpio)

main_path = Path(__file__).with_name("final-code_step11.py")
source = main_path.read_text(encoding="utf-8")
spec = importlib.util.spec_from_file_location("final_code_step6", main_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

checks = []

# Test header written once for a new CSV and not duplicated on reopening.
with tempfile.TemporaryDirectory() as tmp:
    log_path = Path(tmp) / "inspection_log.csv"
    module.LOG_FILE = str(log_path)

    f, writer = module._init_log_file()
    writer.writerow(["2026-10-15 12:00:00", "50.00", "55.00", "50.00", "PASS"])
    f.flush()
    f.close()

    f, writer = module._init_log_file()
    writer.writerow(["2026-10-15 12:00:01", "", "", "", "INCOMPLETE"])
    f.flush()
    f.close()

    with log_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))

    header_ok = rows[0] == ["timestamp", "length_mm", "breadth_mm", "height_mm", "result"]
    print(f'{"PASS" if header_ok else "FAIL"}: CSV header contains the five expected columns')
    checks.append(header_ok)

    row_count_ok = len(rows) == 3
    print(f'{"PASS" if row_count_ok else "FAIL"}: reopening log does not duplicate the header')
    checks.append(row_count_ok)

    incomplete_ok = rows[2][-1] == "INCOMPLETE" and rows[2][1:4] == ["", "", ""]
    print(f'{"PASS" if incomplete_ok else "FAIL"}: incomplete inspection can be logged without fake measurements')
    checks.append(incomplete_ok)

# The active main inspection path must not call trigger_buzzer().
active_calls = [line.strip() for line in source.splitlines()
                if line.strip() == "trigger_buzzer(passed)"]
buzzer_deferred = len(active_calls) == 0
print(f'{"PASS" if buzzer_deferred else "FAIL"}: buzzer activation deferred from inspection path')
checks.append(buzzer_deferred)

print(f"\\n{sum(checks)}/{len(checks)} logging/output checks passed.")
if not all(checks):
    raise SystemExit(1)
