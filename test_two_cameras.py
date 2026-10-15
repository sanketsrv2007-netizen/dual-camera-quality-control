
import cv2

def test_camera(index):
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)

    if not cap.isOpened():
        print(f"Camera {index}: FAILED TO OPEN")
        cap.release()
        return

    ret, frame = cap.read()

    if ret and frame is not None:
        height, width = frame.shape[:2]
        print(
            f"Camera {index}: OK | "
            f"Resolution: {width}x{height}"
        )
    else:
        print(f"Camera {index}: Opened, but frame capture failed")

    cap.release()


for camera_index in range(5):
    test_camera(camera_index)
