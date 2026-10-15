# Step 11 — Final static audit (software-only)

## Changes made in `final-code_step11.py`

1. **Camera watchdog now checks delivered frames.** Previously, health monitoring used the timestamp of a successful low-level `VideoCapture.grab()`. A driver could keep returning successful grabs while `retrieve()` failed, making the watchdog think the camera was healthy. The watchdog now tracks `last_successful_frame_timestamp`, updated only after a valid frame is retrieved, and uses that timestamp for stale-frame checks.
2. **C-stderr suppression is disabled by default.** Redirecting file descriptor 2 hides more than noisy JPEG warnings: it can also hide Python tracebacks and camera-driver diagnostics. The context manager now leaves stderr untouched unless `SUPPRESS_C_STDERR` is explicitly enabled.

## Verification performed

- `python -m py_compile final-code_step11.py` — passed.
- `test_step11_static_audit.py` — 4/4 passed.
- All 10 existing `test_*.py` test files — passed.
- Total offline checks across the current test set: 82 passed.

These are unit, synthetic-image, and simulation checks. They do **not** represent a full end-to-end execution of the production `main()` loop with physical cameras.

## Remaining known limitations / hardware-dependent checks

- The two USB camera serial numbers and `/dev/videoN` discovery must be verified on the actual Raspberry Pi.
- Pixel-per-millimetre calibration constants have not been physically validated.
- Detection currently measures axis-aligned bounding boxes; objects rotated at arbitrary angles may produce inaccurate dimensions.
- Background polarity, minimum contour area, solidity, lighting, and edge clipping thresholds need real camera images.
- There is no automatic camera reconnect; a detected camera fault stops the inspection loop.
- Arduino serial communication, L298N motor control, and rejection LED/buzzer activation are not integrated in the current main program.
- The program has not been run continuously on the Raspberry Pi, and CPU/USB bandwidth/thermal behavior remains unverified.

## Deployment recommendation

Use `final-code_step11.py` as the current software-review candidate. Keep earlier versions and the original `final-code.py` untouched. Do not treat the project as hardware-ready until camera discovery, physical calibration, complete inspection behavior, and the missing actuator interface have been validated on the actual equipment.
