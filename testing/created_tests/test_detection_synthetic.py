"""
Synthetic tests for the current top/side contour detector.

Run:
    python test_detection_synthetic.py

These tests use generated rectangles, not webcam footage. They help reveal
threshold-polarity and clipping behavior, but cannot validate real camera
calibration, lighting, or background segmentation.
"""
import importlib.util
import sys
import types
from pathlib import Path

import cv2
import numpy as np

# Stub Raspberry Pi GPIO so the main file can be imported on Windows.
rpi = types.ModuleType("RPi")
gpio = types.ModuleType("RPi.GPIO")
rpi.GPIO = gpio
sys.modules.setdefault("RPi", rpi)
sys.modules.setdefault("RPi.GPIO", gpio)

main_path = Path(__file__).with_name("final-code_step11.py")
spec = importlib.util.spec_from_file_location("final_code_step3", main_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def make_frame(background, object_value, rectangle=(220, 140, 419, 339)):
    frame = np.full((480, 640, 3), background, dtype=np.uint8)
    x1, y1, x2, y2 = rectangle
    cv2.rectangle(frame, (x1, y1), (x2, y2),
                  (object_value, object_value, object_value), -1)
    return frame


tests = []

# Bright object on dark background with bright-foreground threshold.
module.FOREGROUND_MODE = "bright"
result = module.detect_top(make_frame(20, 230))
tests.append((
    "bright object / dark background",
    bool(result[0] and not result[6]),
    f"found={result[0]}, clipped={result[6]}, L={result[1]}, W={result[2]}"
))

# Dark object on bright background with bright-foreground threshold:
# the detector can select the background region and report it as clipped.
result = module.detect_top(make_frame(230, 20))
tests.append((
    "dark object / bright background using bright mode",
    bool(result[0] and result[6]),
    f"found={result[0]}, clipped={result[6]}, L={result[1]}, W={result[2]}"
))

# Same dark object, correct polarity.
module.FOREGROUND_MODE = "dark"
result = module.detect_top(make_frame(230, 20))
tests.append((
    "dark object / bright background using dark mode",
    bool(result[0] and not result[6]),
    f"found={result[0]}, clipped={result[6]}, L={result[1]}, W={result[2]}"
))

# Object touches image boundary; it must be marked clipped.
module.FOREGROUND_MODE = "bright"
result = module.detect_top(make_frame(20, 230, rectangle=(0, 100, 180, 300)))
tests.append((
    "object touching image boundary",
    bool(result[0] and result[6]),
    f"found={result[0]}, clipped={result[6]}"
))

failed = 0
for name, expected_behavior, details in tests:
    ok = expected_behavior
    print(f'{"PASS" if ok else "FAIL"}: {name} — {details}')
    if not ok:
        failed += 1

print(f"\n{len(tests) - failed}/{len(tests)} synthetic behavior checks passed.")
print("Reminder: synthetic checks are not a substitute for saved frames from both webcams.")
if failed:
    raise SystemExit(1)
