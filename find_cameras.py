
import cv2

for i in range(6):
    cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)

    if cap.isOpened():
        ret, frame = cap.read()

        if ret:
            print(f"Camera index {i}: WORKING")
            cv2.imshow(f"Camera index {i}", frame)
            print("Press any key in the camera window to continue.")
            cv2.waitKey(0)
            cv2.destroyAllWindows()
        else:
            print(f"Camera index {i}: No frame")

    else:
        print(f"Camera index {i}: Unavailable")

    cap.release()

cv2.destroyAllWindows()
