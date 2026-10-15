"""Deterministic offline inspection-cycle simulation for final-code_step11.py.

This is a state-logic simulation, not a full execution of main() and not a
substitute for real camera/hardware testing. It exercises the intended cycle
rules using the same state names and key thresholds as the application.

Run: python test_inspection_cycle_simulation.py
"""
import importlib.util
import sys
import types
from pathlib import Path

# Allow importing the Pi-oriented application on Windows without GPIO hardware.
rpi = types.ModuleType("RPi")
gpio = types.ModuleType("RPi.GPIO")
rpi.GPIO = gpio
sys.modules.setdefault("RPi", rpi)
sys.modules.setdefault("RPi.GPIO", gpio)

main_path = Path(__file__).with_name("final-code_step11.py")
spec = importlib.util.spec_from_file_location("final_code_step7", main_path)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

class CycleSim:
    """Small deterministic model of main()'s IDLE/MEASURING/RESULT cycle."""
    def __init__(self):
        self.state = app.STATE_IDLE
        self.waiting_for_clear = False
        self.top_found_streak = 0
        self.top_absent_streak = 0
        self.samples = []
        self.results = []
        self.last_result_time = -100.0
        self.result_started = None
        self.measure_started = None
        self.entry_started = None

    def tick(self, t, top_found, sample=None, measurement_deadline=False):
        # RESULT is displayed for the configured duration, then returns to IDLE.
        if self.state == app.STATE_RESULT:
            if t - self.result_started >= app.RESULT_DISPLAY_TIME:
                self.state = app.STATE_IDLE
            else:
                return

        if self.state == app.STATE_IDLE:
            if self.waiting_for_clear:
                if top_found:
                    self.top_absent_streak = 0
                else:
                    self.top_absent_streak += 1
                if self.top_absent_streak >= app.CLEAR_FRAMES_REQUIRED:
                    self.waiting_for_clear = False
                    self.top_absent_streak = 0
                    self.top_found_streak = 0
                    self.entry_started = None
                return

            if t - self.last_result_time < app.TRIGGER_COOLDOWN_S:
                self.top_found_streak = 0
                self.entry_started = None
                return

            if top_found:
                if self.top_found_streak == 0:
                    self.entry_started = t
                self.top_found_streak += 1
            else:
                self.top_found_streak = 0
                self.entry_started = None

            if self.top_found_streak >= app.CONSECUTIVE_FRAMES_REQUIRED:
                self.state = app.STATE_MEASURING
                self.measure_started = t
                self.samples = []
                self.top_found_streak = 0
            return

        if self.state == app.STATE_MEASURING:
            if sample is not None:
                self.samples.append(sample)
            if measurement_deadline:
                if len(self.samples) < app.MIN_COMPLETE_SAMPLES:
                    result = "INCOMPLETE"
                else:
                    avgs = tuple(sum(s[i] for s in self.samples) / len(self.samples)
                                 for i in range(3))
                    result = "PASS" if app.check_pass_fail(*avgs) else "FAIL"
                self.results.append(result)
                self.state = app.STATE_RESULT
                self.result_started = t
                self.last_result_time = t
                self.entry_started = None
                self.waiting_for_clear = True
                self.top_absent_streak = 0

checks = []
def check(name, condition):
    print(f'{"PASS" if condition else "FAIL"}: {name}')
    checks.append(bool(condition))

# 1. A qualifying object starts measuring only after required consecutive sightings.
s = CycleSim()
for t in (0.0, 0.1): s.tick(t, True)
check("object needs consecutive sightings before measuring", s.state == app.STATE_IDLE)
s.tick(0.2, True)
check("third consecutive sighting starts measuring", s.state == app.STATE_MEASURING)

# 2. Three valid nominal samples result in PASS.
s = CycleSim()
for t in (0.0, 0.1, 0.2): s.tick(t, True)
nominal = (50.0, 55.0, 50.0)
for i in range(app.MIN_COMPLETE_SAMPLES): s.tick(0.3 + i * 0.1, True, sample=nominal)
s.tick(1.0, True, measurement_deadline=True)
check("three consistent nominal samples produce PASS", s.results == ["PASS"])

# 3. No complete measurements produces INCOMPLETE rather than a fabricated FAIL.
s = CycleSim()
for t in (0.0, 0.1, 0.2): s.tick(t, True)
s.tick(1.0, True, measurement_deadline=True)
check("zero complete samples produce INCOMPLETE", s.results == ["INCOMPLETE"])

# 4. A rejected dimension set produces FAIL.
s = CycleSim()
for t in (0.0, 0.1, 0.2): s.tick(t, True)
for i in range(app.MIN_COMPLETE_SAMPLES): s.tick(0.3 + i * 0.1, True, sample=(60.0, 55.0, 50.0))
s.tick(1.0, True, measurement_deadline=True)
check("out-of-tolerance dimensions produce FAIL", s.results == ["FAIL"])

# 5. The same continuously visible object must not trigger another cycle.
s = CycleSim()
for t in (0.0, 0.1, 0.2): s.tick(t, True)
for i in range(app.MIN_COMPLETE_SAMPLES): s.tick(0.3 + i * 0.1, True, sample=nominal)
s.tick(1.0, True, measurement_deadline=True)
s.tick(2.1, True)  # result display expires; still waiting for clear
for t in (2.2, 2.3, 2.4, 2.5, 2.6): s.tick(t, True)
check("same visible object does not create a duplicate result", len(s.results) == 1 and s.waiting_for_clear)

# 6. After the view clears, a new object can trigger a new cycle.
for t in (2.7, 2.8, 2.9): s.tick(t, False)
check("three absent frames release the clear lock", not s.waiting_for_clear)
for t in (3.0, 3.1, 3.2): s.tick(t, True)
check("new object after clear starts a new measuring cycle", s.state == app.STATE_MEASURING)

print(f"\n{sum(checks)}/{len(checks)} offline inspection-cycle simulation checks passed.")
if not all(checks):
    raise SystemExit(1)
