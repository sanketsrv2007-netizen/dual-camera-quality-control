# Dual-Camera QCR — Step 10 deployment preparation

## Current scope

`final-code_step10.py` is a development candidate, not yet hardware-validated. It prints a startup preflight summary and can be imported on a laptop where `RPi.GPIO` is unavailable. Camera discovery still requires Raspberry Pi/Linux `/dev/video*` devices and `v4l2-ctl`.

## Raspberry Pi preparation

1. Copy `final-code_step10.py` and `requirements-pi.txt` to the project folder on the Pi.
2. Install OS tools:

   ```bash
   sudo apt update
   sudo apt install -y v4l-utils python3-venv python3-pip
   ```

3. Create a virtual environment and install Python dependencies:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   python -m pip install --upgrade pip
   python -m pip install -r requirements-pi.txt
   ```

   If OpenCV or RPi.GPIO installation fails on the Pi's OS/Python combination, stop and resolve that package compatibility issue rather than mixing apt and pip OpenCV installations blindly.

4. Confirm camera nodes and serials:

   ```bash
   v4l2-ctl --list-devices
   v4l2-ctl --device=/dev/video0 --all
   v4l2-ctl --device=/dev/video2 --all
   ```

   Replace `/dev/video0` and `/dev/video2` with the nodes actually listed. Verify the reported USB serials match `TOP_CAM_SERIAL` and `SIDE_CAM_SERIAL` in the code.

5. Run the program with the display enabled first if the Pi has a desktop session:

   ```bash
   python final-code_step10.py
   ```

   If running headless, set `SHOW_DISPLAY = False` in the code before starting it. Do not assume headless/display behavior is validated until tested on the actual Pi.

## Before using the conveyor

- Keep motor power disconnected during the first camera startup and detection tests.
- Verify both cameras open simultaneously and produce fresh frames.
- Calibrate each camera with a known-size reference object at the actual inspection position.
- Confirm detection polarity/background and thresholds with actual frames.
- Verify CSV output and fault behavior.
- Arduino serial communication, L298N motor control, rejection LED, and buzzer are not integrated in this version.

## Important

The startup preflight only reports environment details. It does not prove camera serial matching, camera capture, physical measurement accuracy, or safe hardware operation. Do not treat a clean preflight as hardware acceptance.
