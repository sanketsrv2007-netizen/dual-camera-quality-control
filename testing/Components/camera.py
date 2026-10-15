import cv2

cam0 = cv2.VideoCapture(0)
cam1 = cv2.VideoCapture(1)

while True:
    _, frame0 = cam0.read()
    _, frame1 = cam1.read()

    cv2.imshow("Camera 0", frame0)
    cv2.imshow("Camera 1", frame1)

    if cv2.waitKey(1) == ord('q'):
        break

cam0.release()
cam1.release()
cv2.destroyAllWindows()