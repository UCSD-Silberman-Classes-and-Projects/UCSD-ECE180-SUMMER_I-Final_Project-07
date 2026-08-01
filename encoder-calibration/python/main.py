"""
Encoder Calibration app — Uno Q MPU (Linux) side.

Manually calibrate PULSES_PER_REV_LEFT / PULSES_PER_REV_RIGHT by hand-
spinning each wheel a known number of full revolutions and reading the
raw pulse count before/after.

This file goes in your App Lab project's: python/main.py

HOW TO USE:
  1. Run this app. It resets both counters to 0 and starts printing
     live pulse counts once per second.
  2. Mark a fixed point on the LEFT wheel.
  3. Hand-spin the left wheel exactly REVOLUTIONS_TO_SPIN full turns,
     watching your mark. Ignore the right count for now.
  4. Note the left pulse count printed once you stop.
  5. Press Enter (or Ctrl+C the print loop / just note the value) —
     then reset counts again before doing the right wheel, OR just
     subtract the left wheel's final count from the right wheel's
     final count reading if you did them back to back without
     resetting in between (the script prints both continuously so
     you always have both raw values available).
  6. Repeat for the RIGHT wheel.
  7. PULSES_PER_REV = pulses_counted / REVOLUTIONS_TO_SPIN

  Recommended: do one wheel at a time, and reset (restart this app,
  or use the reset_counts() call below) between wheels so you don't
  have to do subtraction by hand.
"""

from arduino.app_utils import *
import time

REVOLUTIONS_TO_SPIN = 10  # how many full turns you'll hand-spin each wheel
PRINT_INTERVAL = 1.0      # seconds between printed readings


def get_counts():
    result = Bridge.call("get_encoder_counts")
    left_str, right_str = result.split(",")
    return int(left_str), int(right_str)


def reset_counts():
    Bridge.call("reset_encoder_counts")


def main():
    print("=== Encoder Calibration ===")
    print(f"Plan: hand-spin one wheel at a time exactly {REVOLUTIONS_TO_SPIN} full revolutions.")
    print("Counts reset to 0 now. Do the LEFT wheel first, then restart")
    print("this app (or wait for a reset prompt) before doing the RIGHT wheel.")
    print()
    reset_counts()

    last_left, last_right = 0, 0

    try:
        while True:
            left_count, right_count = get_counts()

            # Only print when something changed, so the log isn't just
            # repeated identical lines while you're getting positioned.
            if left_count != last_left or right_count != last_right:
                print(f"left_pulses={left_count}  right_pulses={right_count}")
                last_left, last_right = left_count, right_count

            time.sleep(PRINT_INTERVAL)
    except KeyboardInterrupt:
        pass

    left_count, right_count = get_counts()
    print()
    print("=== Final counts ===")
    print(f"left_pulses={left_count}  right_pulses={right_count}")
    print()
    print(f"If this run was only the LEFT wheel spun {REVOLUTIONS_TO_SPIN} times:")
    print(f"  PULSES_PER_REV_LEFT = {left_count} / {REVOLUTIONS_TO_SPIN} = {left_count / REVOLUTIONS_TO_SPIN:.2f}")
    print(f"If this run was only the RIGHT wheel spun {REVOLUTIONS_TO_SPIN} times:")
    print(f"  PULSES_PER_REV_RIGHT = {right_count} / {REVOLUTIONS_TO_SPIN} = {right_count / REVOLUTIONS_TO_SPIN:.2f}")


main()
