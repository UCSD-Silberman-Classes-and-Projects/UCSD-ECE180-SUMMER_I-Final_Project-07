"""
Ultrasonic Sensor Testing app — Uno Q MPU (Linux) side.
Calls the MCU's get_distance() RPC function repeatedly and prints
live readings, to confirm the sensor is wired correctly and giving
sensible values before integrating it into the full autonomous app.

"""

from arduino.app_utils import *
import time

print("Reading distance. Press Ctrl+C to stop.")

try:
    while True:
        distance = Bridge.call("get_distance")
        if distance < 0:
            print("No echo received (out of range or check wiring)")
        else:
            print(f"Distance: {distance:.1f} cm")
        time.sleep(0.3)
except KeyboardInterrupt:
    print("\nDone.")
