
import cv2

CAMERA_INDICES = (1, 2)

cap_top = cv2.VideoCapture(CAMERA_INDICES[0], cv2.CAP_DSHOW)
cap_side = cv2.VideoCapture(CAMERA_INDICES[1], cv2.CAP_DSHOW)

if not cap_top.isOpened() or not cap_side.isOpened():
    print("Could not open both cameras.")
    cap_top.release()
    cap_side.release()
    raise SystemExit(1)

print("Both cameras opened. Press Q to quit.")

try:
    while True:
        ret_top, frame_top = cap_top.read()
        ret_side, frame_side = cap_side.read()

        if ret_top and frame_top is not None:
            cv2.putText(
                frame_top, "CAMERA 0 - TOP",
                (10, 30), cv2.FONT_HERSHEY_SIMPLEX,
                0.7, (0, 255, 0), 2
            )
            cv2.imshow("Top Camera", frame_top)

        if ret_side and frame_side is not None:
            cv2.putText(
                frame_side, "CAMERA 1 - SIDE",
                (10, 30), cv2.FONT_HERSHEY_SIMPLEX,
                0.7, (0, 255, 0), 2
            )
            cv2.imshow("Side Camera", frame_side)

        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

finally:
    cap_top.release()
    cap_side.release()
    cv2.destroyAllWindows()
