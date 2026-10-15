from gpiozero import Motor
from time import sleep

motor = Motor(forward=17, backward=27)

while True:
    print("Forward")
    motor.forward()
    sleep(3)

    print("Stop")
    motor.stop()
    sleep(2)

    print("Reverse")
    motor.backward()
    sleep(3)

    print("Stop")
    motor.stop()
    sleep(2)