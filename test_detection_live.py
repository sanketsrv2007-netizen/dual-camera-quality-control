
import sys
import types
import importlib.util
import cv2

# Allow importing Raspberry Pi GPIO code on Windows.
# The GPIO hardware is not used in this camera-only test.
rpi_stub = types.ModuleType("RPi")
gpio_stub = types.ModuleType("RPi.GPIO")

rpi_stub.GPIO = gpio_stub

sys.modules["RPi"] = rpi_stub
sys.modules["RPi.GPIO"] = gpio_stub

spec = importlib.util.spec_from_file_location(
    "inspection", "final-code.py"
)
inspection = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inspection)

# CHANGE THESE to the indices of your two USB webcams.
TOP_INDEX = 1
SIDE_INDEX = 2
top_cap = cv2.VideoCapture(TOP_INDEX, cv2.CAP_DSHOW)
side_cap = cv2.VideoCapture(SIDE_INDEX, cv2.CAP_DSHOW)

if not top_cap.isOpened() or not side_cap.isOpened():
    print("ERROR: Could not open both selected cameras.")
    top_cap.release()
    side_cap.release()
    raise SystemExit(1)

print("Live detection test started. Press Q to quit.")
print("Place the same stationary object in both camera views.")

try:
    while True:
        ret_top, top_frame = top_cap.read()
        ret_side, side_frame = side_cap.read()

        if ret_top and top_frame is not None:
            result = inspection.detect_top(top_frame)
            found, length, breadth, box, _, _, clipped = result

            if found:
                cv2.drawContours(
                    top_frame, [box], 0, (0, 255, 0), 2
                )
                label = (
                    f"L:{length:.1f}mm B:{breadth:.1f}mm"
                )
                if clipped:
                    label += " CLIPPED"
                cv2.putText(
                    top_frame, label, (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    (0, 255, 0), 2
                )
            else:
                cv2.putText(
                    top_frame, "NO OBJECT DETECTED", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                    (0, 0, 255), 2
                )

            cv2.imshow("Top - Actual Detection", top_frame)

        if ret_side and side_frame is not None:
            result = inspection.detect_side(side_frame)
            found, length, height, box, clipped = result

            if found:
                cv2.drawContours(
                    side_frame, [box], 0, (255, 0, 0), 2
                )
                label = (
                    f"L:{length:.1f}mm H:{height:.1f}mm"
                )
                if clipped:
                    label += " CLIPPED"
                cv2.putText(
                    side_frame, label, (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    (255, 0, 0), 2
                )
            else:
                cv2.putText(
                    side_frame, "NO OBJECT DETECTED", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                    (0, 0, 255), 2
                )

            cv2.imshow("Side - Actual Detection", side_frame)

        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

finally:
    top_cap.release()
    side_cap.release()
    cv2.destroyAllWindows()
