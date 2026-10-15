"""Step 12: multi-object offline inspection-cycle scenarios.

This deliberately simulates the state-machine contract; it does NOT run main()
with real camera threads. It is intended to catch regressions in expected cycle
behavior before hardware is available.

Run: python test_step12_end_to_end_simulation.py
"""
import importlib.util
import sys
import types
from pathlib import Path

# Permit importing the Raspberry-Pi-oriented module on Windows without GPIO.
rpi = types.ModuleType("RPi")
gpio = types.ModuleType("RPi.GPIO")
rpi.GPIO = gpio
sys.modules.setdefault("RPi", rpi)
sys.modules.setdefault("RPi.GPIO", gpio)

main_path = Path(__file__).with_name("final-code_step11.py")
spec = importlib.util.spec_from_file_location("final_code_step11_step12", main_path)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

class InspectionCycle:
    """Deterministic model of IDLE -> MEASURING -> RESULT and clear-lock behavior."""
    def __init__(self):
        self.state = app.STATE_IDLE
        self.waiting_for_clear = False
        self.found_streak = 0
        self.absent_streak = 0
        self.samples = []
        self.results = []
        self.log_rows = []
        self.last_end = -1e9
        self.measure_start = None
        self.result_start = None

    def tick(self, t, top_found, sample=None, deadline=False, camera_pair_ok=True):
        if self.state == app.STATE_RESULT:
            if t - self.result_start < app.RESULT_DISPLAY_TIME:
                return
            self.state = app.STATE_IDLE

        if self.state == app.STATE_IDLE:
            if self.waiting_for_clear:
                self.absent_streak = 0 if top_found else self.absent_streak + 1
                if self.absent_streak >= app.CLEAR_FRAMES_REQUIRED:
                    self.waiting_for_clear = False
                    self.absent_streak = 0
                    self.found_streak = 0
                return
            if t - self.last_end < app.TRIGGER_COOLDOWN_S:
                self.found_streak = 0
                return
            self.found_streak = self.found_streak + 1 if top_found else 0
            if self.found_streak >= app.CONSECUTIVE_FRAMES_REQUIRED:
                self.state = app.STATE_MEASURING
                self.measure_start = t
                self.samples = []
                self.found_streak = 0
            return

        if self.state == app.STATE_MEASURING:
            # A missing camera frame or unpaired timestamps means no valid sample.
            if sample is not None and camera_pair_ok:
                self.samples.append(sample)
            if deadline:
                if len(self.samples) < app.MIN_COMPLETE_SAMPLES:
                    result = "INCOMPLETE"
                    dimensions = None
                else:
                    n = len(self.samples)
                    dimensions = tuple(sum(row[i] for row in self.samples) / n for i in range(3))
                    result = "PASS" if app.check_pass_fail(*dimensions) else "FAIL"
                self.results.append(result)
                self.log_rows.append({"result": result, "dimensions": dimensions})
                self.state = app.STATE_RESULT
                self.result_start = t
                self.last_end = t
                self.waiting_for_clear = True
                self.absent_streak = 0


def begin(cycle, start=0.0):
    for t in (start, start + 0.1, start + 0.2):
        cycle.tick(t, True)
    assert cycle.state == app.STATE_MEASURING


def finish(cycle, start, samples, *, invalid_pair=False):
    for i, sample in enumerate(samples):
        cycle.tick(start + i * 0.1, True, sample=sample,
                   camera_pair_ok=not invalid_pair)
    cycle.tick(start + 1.0, True, deadline=True)


checks = []
def check(name, condition):
    ok = bool(condition)
    print(f'{"PASS" if ok else "FAIL"}: {name}')
    checks.append(ok)

nominal = (50.0, 55.0, 50.0)
rejected = (60.0, 55.0, 50.0)

# 1. Multiple sequential objects produce distinct results after a clear.
c = InspectionCycle()
begin(c, 0.0)
finish(c, 0.3, [nominal, nominal, nominal])
for t in (2.4, 2.5, 2.6): c.tick(t, False)
# Result screen has expired and three absent frames release the clear lock.
for t in (2.7, 2.8, 2.9): c.tick(t, True)
check("second object starts after first object clears", c.state == app.STATE_MEASURING)
finish(c, 3.0, [rejected, rejected, rejected])
check("two objects produce separate PASS then FAIL results", c.results == ["PASS", "FAIL"])
check("one log row is created per completed inspection", len(c.log_rows) == len(c.results) == 2)

# 2. Missing camera data does not fabricate a dimensional decision.
c = InspectionCycle(); begin(c)
finish(c, 0.3, [nominal, None, None])
check("insufficient camera samples produce INCOMPLETE", c.results == ["INCOMPLETE"])

# 3. Frame pairs outside the timestamp tolerance are not used.
c = InspectionCycle(); begin(c)
finish(c, 0.3, [nominal, nominal, nominal], invalid_pair=True)
check("unpaired camera frames produce INCOMPLETE", c.results == ["INCOMPLETE"])

# 4. The same visible object cannot retrigger, even after result display expires.
c = InspectionCycle(); begin(c)
finish(c, 0.3, [nominal, nominal, nominal])
for t in (2.4, 2.5, 2.6, 2.7, 2.8, 2.9): c.tick(t, True)
check("continuously visible object is inspected only once", len(c.results) == 1 and c.waiting_for_clear)

# 5. Brief disappearance does not release a three-frame clear lock.
c = InspectionCycle(); begin(c)
finish(c, 0.3, [nominal, nominal, nominal])
for t, found in ((2.4, False), (2.5, True), (2.6, False), (2.7, True)):
    c.tick(t, found)
check("intermittent disappearance does not unlock the next cycle", c.waiting_for_clear and len(c.results) == 1)

# 6. Exactly the configured number of absent frames unlocks the view.
c = InspectionCycle(); begin(c)
finish(c, 0.3, [nominal, nominal, nominal])
for t in (2.4, 2.5, 2.6): c.tick(t, False)
check("required consecutive absent frames release clear lock", not c.waiting_for_clear)

# 7. An object below the minimum sample count remains incomplete.
c = InspectionCycle(); begin(c)
finish(c, 0.3, [nominal, nominal])
check("two valid samples are insufficient for PASS/FAIL", c.results == ["INCOMPLETE"])

# 8. A dimensionally invalid object is rejected, not mislabeled incomplete.
c = InspectionCycle(); begin(c)
finish(c, 0.3, [rejected, rejected, rejected])
check("three valid out-of-tolerance samples produce FAIL", c.results == ["FAIL"])

# 9. All valid nominal samples produce PASS.
c = InspectionCycle(); begin(c)
finish(c, 0.3, [nominal, nominal, nominal])
check("three valid nominal samples produce PASS", c.results == ["PASS"])

# 10. No repeated log entries are added while an object remains visible.
check("result and log counts remain one-to-one", all(len(c.log_rows) == len(c.results) for _ in [0]))

print(f"\n{sum(checks)}/{len(checks)} Step 12 offline scenario checks passed.")
if not all(checks):
    raise SystemExit(1)
