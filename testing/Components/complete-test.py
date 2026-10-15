import cv2
import serial
import time

cap = cv2.VideoCapture(0)

arduino = serial.Serial('/dev/ttyACM0',9600)
time.sleep(2)

while True:

    ret, frame = cap.read()

    if not ret:
        break

    cv2.imshow("Camera", frame)

    key = cv2.waitKey(1)

    if key == ord('f'):
        arduino.write(b'1')

    elif key == ord('s'):
        arduino.write(b'0')

    elif key == ord('q'):
        break

cap.release()
arduino.close()
cv2.destroyAllWindows()