"""Offline regression tests for Step 9 main-loop fixes; no physical cameras used."""
import importlib.util
import sys
import types
from pathlib import Path
import numpy as np

rpi_module = types.ModuleType('RPi')
gpio_module = types.ModuleType('RPi.GPIO')
rpi_module.GPIO = gpio_module
sys.modules.setdefault('RPi', rpi_module)
sys.modules.setdefault('RPi.GPIO', gpio_module)

source = Path(__file__).with_name('final-code_step11.py')
spec = importlib.util.spec_from_file_location('final_code_step9', source)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_detection_streak():
    assert module.update_detection_streak(0, True) == 1
    assert module.update_detection_streak(1, True) == 2
    assert module.update_detection_streak(2, True) == 3
    assert module.update_detection_streak(2, False) == 0


def test_display_copies_do_not_alias_capture_frames():
    top = np.zeros((8, 8, 3), dtype=np.uint8)
    side = np.ones((8, 8, 3), dtype=np.uint8)
    top_view, side_view = module.make_display_views(top, side)
    assert top_view is not top and side_view is not side
    assert not np.shares_memory(top_view, top)
    assert not np.shares_memory(side_view, side)
    top_view[0, 0] = 255
    side_view[0, 0] = 255
    assert np.all(top[0, 0] == 0)
    assert np.all(side[0, 0] == 1)


def test_none_display_frames():
    assert module.make_display_views(None, None) == (None, None)


def run():
    tests = [
        ('detection streak increments once per call', test_detection_streak),
        ('display views are independent copies', test_display_copies_do_not_alias_capture_frames),
        ('missing display frames are handled', test_none_display_frames),
    ]
    failures = 0
    for name, fn in tests:
        try:
            fn()
            print(f'PASS: {name}')
        except Exception as exc:
            failures += 1
            print(f'FAIL: {name}: {exc!r}')
    print(f'\n{len(tests)-failures}/{len(tests)} Step 9 tests passed')
    return int(failures != 0)

if __name__ == '__main__':
    raise SystemExit(run())
