import serial
import time

arduino = serial.Serial('/dev/ttyACM0', 9600)

time.sleep(2)

while True:

    cmd = input("Enter 1 or 0 : ")

    if cmd == "1":
        arduino.write(b'1')

    elif cmd == "0":
        arduino.write(b'0')

    print(arduino.readline().decode().strip())