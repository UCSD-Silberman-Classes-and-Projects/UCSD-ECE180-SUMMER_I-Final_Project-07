"""
Autonomous movement primitives — Uno Q MPU (Linux) side.
Uses encoder feedback for closed-loop, distance/angle-accurate movement
instead of open-loop timing. Reuses the same Bridge RPC functions as
manual teleop (set_motors, stop_motors, get_encoder_counts,
reset_encoder_counts, get_distance).

This file goes in your App Lab project's: python/main.py

TESTING THIS FILE:
  Start with TEST_MODE = "drive" to test drive_forward_cm() alone,
  then TEST_MODE = "turn" to test turn_degrees() alone, before trying
  the full sweep. Each prints its progress so you can verify accuracy
  against a tape measure / protractor before trusting it.
"""

from arduino.app_utils import *
from arduino.app_bricks.web_ui import WebUI
from arduino.app_bricks.video_objectdetection import VideoObjectDetection
from datetime import datetime, UTC
import time
import math
import pygame

# ---- Camera / AI detection ----
# Matches your trained model's actual class labels.
CATEGORY_PICKUP = "screw"
AVOID_CATEGORIES = {"adapter", "baby hazard"}

# Only treat a detection as "close enough to act on" if its bounding
# box's bottom edge (y2) is at or below this many pixels from the top
# of the frame (assumes ~640x480 camera resolution). Two separate
# thresholds since pickup and avoid need different reaction distances:
# pickup only needs to trigger once actually close (brush range), but
# avoid needs to trigger further away to leave room/time for the
# sidestep-bypass-return maneuver.
PICKUP_Y_THRESHOLD = 240  # calibrated in Manual Movement: object placed where the brush could reach it measured y2=240
AVOID_Y_THRESHOLD = 120   # further away (smaller y2) than pickup, to react before the robot is right on top of it

# ---- Camera viewer web interface ----
# This is what actually serves the browser page showing the camera
# feed/detections -- without it, the AI model still runs and
# detections still fire, but there's nothing to view in a browser.
# NOTE: created BEFORE VideoObjectDetection, matching Manual Movement's
# ordering -- VideoObjectDetection can take real time to initialize
# (loading the AI model, starting the camera stream), and creating it
# first was suspected to delay the WebUI past whatever window App
# Lab's auto-popup logic watches for.
ui = WebUI()
detection_stream = VideoObjectDetection(confidence=0.5, debounce_sec=0.0)
ui.on_message("override_th", lambda sid, threshold: detection_stream.override_threshold(threshold))

# Set by on_detections() whenever something close enough is seen and
# nothing is currently pending. Cleared once the creep-and-check loop
# (or sweep) has acted on it. This mirrors the awaiting_decision/
# detected_category pattern from the Manual Movement app so both apps
# behave consistently.
awaiting_decision = False
detected_category = None

# Total number of items successfully picked up this run. Sent to the
# WebUI's "Item Count" badge each time it changes.
item_count = 0

# Creeping only stops for a pickup once this many items have been
# collected -- below this, a pickup sweeps the brush and continues
# through the rest of the row. Set to 1 for now (current test setup:
# stop after the first item), but this can be raised once you're
# ready to test collecting multiple items per row.
MAX_ITEMS_TO_PICKUP = 1


def increment_item_count():
    global item_count
    item_count += 1
    ui.send_message("item_count", message=item_count)
    print(f"Item count: {item_count}")


def on_detections(detections: dict):
    """Callback fired by the Brick whenever objects are detected.
    detections is shaped like:
      {category_name: [ {"confidence": ..., "bounding_box_xyxy": (x1,y1,x2,y2)}, ... ]}

    Only acts on a detection if its bounding box's bottom edge (y2) is
    past that category's threshold -- i.e. close enough to actually
    act on, not just visible somewhere in frame. Pickup and avoid use
    different thresholds (see PICKUP_Y_THRESHOLD / AVOID_Y_THRESHOLD
    above), since avoid needs more reaction distance than pickup.
    """
    global awaiting_decision, detected_category

    for category, values in detections.items():
        for value in values:
            entry = {
                "content": category,
                "confidence": value.get("confidence"),
                "timestamp": datetime.now(UTC).isoformat()
            }
            ui.send_message("detection", message=entry)

        if awaiting_decision:
            continue  # already handling one -- keep sending to WebUI, but don't act on more

        if category == CATEGORY_PICKUP:
            threshold = PICKUP_Y_THRESHOLD
        elif category in AVOID_CATEGORIES:
            threshold = AVOID_Y_THRESHOLD
        else:
            continue  # unrecognized category -- shown in WebUI above, but no action

        for detection in values:
            bbox = detection.get("bounding_box_xyxy")
            if bbox is None:
                continue
            _, _, _, y2 = bbox

            if y2 >= threshold:
                awaiting_decision = True
                detected_category = category
                confidence = detection.get("confidence")
                print(f"DETECTED (close enough): {category} "
                      f"(confidence {confidence}, y2={y2}, threshold={threshold})")
                break
            # else: detected, but still too far away -- ignore for now,
            # keep creeping closer and check again next frame.


detection_stream.on_detect_all(on_detections)

# ---- Set this to test one piece at a time ----
# "drive"   -> tests driving forward a fixed distance
# "turn"    -> tests turning a fixed angle
# "creep"   -> tests creep_forward_cm() alone (stop-scan-move + camera check)
# "creep2"  -> tests one row, turn, second row (creep_two_rows())
# "sweep2"  -> two rows, full speed, no creep increments (drive_two_rows_full())
# "sweep"   -> runs the full lawnmower sweep
TEST_MODE = "creep2"
TEST_DRIVE_DISTANCE_CM = 100
TEST_TURN_DEGREES = 90

# Confirm this via jstest — button number that changes when you press X.
START_BUTTON = 1  # X — confirmed via jstest

# ---- Robot geometry (measured) ----
WHEEL_DIAMETER_CM = 6.5
WHEEL_CIRCUMFERENCE_CM = math.pi * WHEEL_DIAMETER_CM

# NOTE: this is an *effective* wheelbase, not the physically-measured
# one (that measured ~19.5cm). It's been empirically corrected to
# account for wheel scrub during point turns on vinyl flooring --
# using the true measured wheelbase caused turns to overshoot
# (commanded 90 degrees actually turned ~115 degrees). This corrected
# value was confirmed to produce ~90 degree actual turns (85-95 degree
# repeatable range) on a 90 degree command.
WHEELBASE_CM = 15.3

ROBOT_WIDTH_CM = 24

# Effective row spacing for the lawnmower sweep. Deliberately tighter
# than ROBOT_WIDTH_CM to build in overlap margin -- this accounts for
# (a) measured straight-line drift of up to ~10cm over a 300cm run,
# and (b) general good-practice overlap so objects sitting near a row
# boundary aren't missed even in the best case.
ROW_SPACING_CM = 18

PLAY_AREA_WIDTH_CM = 300
PLAY_AREA_LENGTH_CM = 300

# ---- Calibrated encoder constants ----
# Re-derived via hand-spin calibration (10 full revolutions per wheel,
# raw pulse counts read via get_encoder_counts()) after the board
# rebuild -- confirmed different from the old board's values,
# especially on the right wheel (257 -> 249).
PULSES_PER_REV_LEFT = 248
PULSES_PER_REV_RIGHT = 249

# Empirical correction factor — commanded 50cm actually traveled ~60cm,
# meaning our distance-per-pulse estimate was too low by this ratio.
# Multiplying corrects for it. Adjust further based on repeated testing.
DISTANCE_CORRECTION_FACTOR = 60 / 50

# ---- Speed settings (reuse your tuned manual-driving values) ----
MIN_PWM = 65           # matches your tuned manual-driving MIN_PWM
MAX_PWM = 95           # matches your tuned manual-driving MAX_PWM
DRIVE_PWM = 95        # base speed for forward/backward moves
TURN_PWM = 95         # speed used while turning in place

# Re-derived at DRIVE_PWM=95/MIN_PWM=65 via careful PID-off comparison
# across trim=9/10/11 -- all three averaged within noise of each other
# (~3 unit average |error| over 100cm), so 10 was picked as a
# reasonable middle value rather than continuing to chase run-to-run
# noise. This ~3-unit residual wobble is what Kp now needs to handle,
# not the much larger sustained bias seen at the old trim=15.
LEFT_TRIM = 10
RIGHT_TRIM = 0

# Set False to test raw, uncorrected driving (fixed trim only) for
# comparison against the PID-corrected version.
USE_PID_CORRECTION = True

# ---- Obstacle avoidance ----
# 30cm gives buffer beyond the ~18cm brush-tip distance to absorb
# sensor/polling lag and motor coast distance before actually stopping.
OBSTACLE_THRESHOLD_CM = 30
AVOID_BACKUP_CM = 10
AVOID_TURN_DEGREES = 45

# ---- Polling ----
POLL_INTERVAL = 0.05


def get_counts():
    result = Bridge.call("get_encoder_counts")
    left_str, right_str = result.split(",")
    return int(left_str), int(right_str)


def pulses_to_distance(pulses, pulses_per_rev):
    return (pulses / pulses_per_rev) * WHEEL_CIRCUMFERENCE_CM * DISTANCE_CORRECTION_FACTOR


def check_obstacle():
    """Returns True if something is closer than OBSTACLE_THRESHOLD_CM."""
    distance = Bridge.call("get_distance")
    return 0 < distance < OBSTACLE_THRESHOLD_CM


def drive_forward_cm(distance_cm, speed=DRIVE_PWM):
    """Drives forward (or backward if distance_cm is negative) using
    encoder feedback to stop at the target distance. Returns "done" if
    it completed the distance, or "obstacle" if it stopped early due
    to something being detected in range."""
    direction = 1 if distance_cm >= 0 else -1
    target_cm = abs(distance_cm)

    Bridge.call("reset_encoder_counts")
    left_start, right_start = 0, 0

    final_left_pwm = int(direction * (speed + LEFT_TRIM))
    final_right_pwm = int(direction * (speed + RIGHT_TRIM))

    # Brief ramp-up instead of an instant full-power start — reduces
    # inconsistent curving caused by an abrupt step command.
    RAMP_STEPS = 5
    RAMP_DURATION = 0.25  # seconds
    for step in range(1, RAMP_STEPS + 1):
        fraction = step / RAMP_STEPS
        ramp_left = int(final_left_pwm * fraction)
        ramp_right = int(final_right_pwm * fraction)
        Bridge.call("set_motors", ramp_right, ramp_left)
        time.sleep(RAMP_DURATION / RAMP_STEPS)

    result = "done"
    # PID gains for real-time left/right balancing.
    # Kp=3.5 (tuned to fight the old trim=15 sustained bias) was found
    # to make things WORSE than PID-off once the real cause turned out
    # to be trim, not a lack of correction authority -- correction was
    # pinning at MAX_CORRECTION and pushing error higher, not lower.
    # Resetting to a low starting point and re-tuning carefully now
    # that trim=10 leaves only a small ~3-unit residual wobble to
    # correct, rather than a large sustained bias.
    Kp = 1.0
    Ki = 0.0
    Kd = 0.0
    MAX_CORRECTION = 25  # loosened — the separate MIN_PWM floor already prevents stalling
    print(f"DEBUG: USE_PID_CORRECTION={USE_PID_CORRECTION} Kp={Kp} Ki={Ki} Kd={Kd} MAX_CORRECTION={MAX_CORRECTION} PULSES_PER_REV_LEFT={PULSES_PER_REV_LEFT} PULSES_PER_REV_RIGHT={PULSES_PER_REV_RIGHT}")

    integral_error = 0.0
    MAX_INTEGRAL = MAX_CORRECTION / max(Ki, 0.0001)  # clamp so Ki*integral alone can't exceed MAX_CORRECTION

    # Prime the derivative term with a real reading first, so the loop's
    # first iteration doesn't see a fake jump from 0 (the "derivative
    # kick" that caused a large spurious correction spike at startup).
    left_count, right_count = get_counts()
    left_dist = pulses_to_distance(left_count - left_start, PULSES_PER_REV_LEFT)
    right_dist = pulses_to_distance(right_count - right_start, PULSES_PER_REV_RIGHT)
    previous_error = left_dist - right_dist
    previous_time = time.time()

    try:
        while True:
            left_count, right_count = get_counts()
            left_dist = pulses_to_distance(left_count - left_start, PULSES_PER_REV_LEFT)
            right_dist = pulses_to_distance(right_count - right_start, PULSES_PER_REV_RIGHT)

            now = time.time()
            dt = max(now - previous_time, 0.001)  # avoid divide-by-zero

            error = left_dist - right_dist  # positive = left ahead of right

            if USE_PID_CORRECTION:
                integral_error += error * dt
                integral_error = max(-MAX_INTEGRAL, min(MAX_INTEGRAL, integral_error))
                derivative = (error - previous_error) / dt

                correction = (Kp * error) + (Ki * integral_error) + (Kd * derivative)
                correction = max(-MAX_CORRECTION, min(MAX_CORRECTION, correction))
            else:
                correction = 0  # no dynamic correction — fixed trim only

            previous_error = error
            previous_time = now

            corrected_left_mag = max(MIN_PWM, abs(final_left_pwm) - correction)
            corrected_right_mag = max(MIN_PWM, abs(final_right_pwm) + correction)

            corrected_left_pwm = int(direction * corrected_left_mag)
            corrected_right_pwm = int(direction * corrected_right_mag)

            Bridge.call("set_motors", corrected_right_pwm, corrected_left_pwm)

            print(f"  error={error:.1f} correction={correction:.1f} L_pwm={corrected_left_pwm} R_pwm={corrected_right_pwm} avg_dist={((left_dist+right_dist)/2.0):.1f}")

            if check_obstacle():
                result = "obstacle"
                break

            avg_dist = (left_dist + right_dist) / 2.0
            if avg_dist >= target_cm:
                break

            time.sleep(POLL_INTERVAL)
    finally:
        Bridge.call("stop_motors")

    return result


def turn_degrees(degrees, speed=TURN_PWM):
    """Turns in place by the given angle. Positive = turns right,
    negative = turns left (confirmed via testing). Uses encoder
    feedback on both wheels spinning in opposite directions to
    measure actual rotation achieved."""
    direction = 1 if degrees >= 0 else -1
    target_rad = math.radians(abs(degrees))
    target_arc_cm = (WHEELBASE_CM / 2.0) * target_rad

    Bridge.call("reset_encoder_counts")

    # Point turn: wheels spin in opposite directions at equal speed.
    left_pwm = int(direction * speed)
    right_pwm = int(-direction * speed)

    Bridge.call("set_motors", right_pwm, left_pwm)  # swapped, see teleop notes

    try:
        while True:
            Bridge.call("set_motors", right_pwm, left_pwm)  # keep failsafe from tripping

            left_count, right_count = get_counts()
            left_dist = pulses_to_distance(left_count, PULSES_PER_REV_LEFT)
            right_dist = pulses_to_distance(right_count, PULSES_PER_REV_RIGHT)
            avg_arc = (left_dist + right_dist) / 2.0

            if avg_arc >= target_arc_cm:
                break

            time.sleep(POLL_INTERVAL)
    finally:
        Bridge.call("stop_motors")


def check_for_target():
    """Returns the detected category (e.g. "sharp wire", "baby hazard")
    if something is currently pending a decision, or None if nothing
    has been detected since the last time this was cleared.

    Does NOT clear awaiting_decision itself -- the caller (creep loop /
    sweep) is responsible for deciding what to do and then calling
    clear_detection() once it's been handled.
    """
    if awaiting_decision:
        return detected_category
    return None


def clear_detection():
    """Marks the current pending detection as handled, allowing new
    detections to be picked up again."""
    global awaiting_decision, detected_category
    awaiting_decision = False
    detected_category = None


# ---- Creep-and-check ----
# The camera/AI pipeline has real processing latency -- testing in the
# Manual Movement app showed it can miss objects even at normal driving
# speed, not just fast passes. Rather than continuous driving, we creep
# forward in short increments and fully stop+pause after each one, so
# the camera gets a real chance to process a still (or near-still)
# frame every time, instead of trying to catch a moving target.
CREEP_INCREMENT_CM = 5
CREEP_PAUSE_SECONDS = 4.0

# ---- Avoid maneuver ----
# When an AVOID_CATEGORIES object is detected close enough, the robot
# sidesteps into the adjacent row, drives past the object, then
# returns to the original row -- a rectangular detour rather than a
# diagonal cut, so it stays aligned with the row/turn geometry already
# validated for the sweep.
AVOID_SIDESTEP_CM = ROW_SPACING_CM  # lateral distance to the adjacent row
AVOID_BYPASS_CM = 40  # how far to drive forward while bypassing the object -- adjust based on real object/obstacle size



def avoid_obstacle():
    """Executes a rectangular sidestep maneuver to get around a
    detected avoid-category object (e.g. "adapter", "baby hazard"),
    then returns to the original row facing the original direction.
    Turns are relative to the robot's current heading, so this works
    regardless of which row/turn-direction the sweep is currently on.

    Sequence: turn right -> check clearance -> sidestep into adjacent
    row -> turn left (now facing original direction again) -> drive
    forward past the object -> turn left again -> return sidestep back
    to the original row -> turn right (facing original direction,
    back on the original row, having advanced AVOID_BYPASS_CM).

    Returns True if the maneuver completed and it's safe to keep
    creeping, or False if the ultrasonic sensor found the sidestep
    path blocked too -- in which case it stops immediately without
    attempting to undo the first turn (no further recovery logic
    implemented yet).
    """
    print("Avoid maneuver: turning right 90 degrees to check clearance...")
    turn_degrees(90)

    distance = Bridge.call("get_distance")
    if 0 < distance < OBSTACLE_THRESHOLD_CM:
        print(f"Path blocked on the right too (distance={distance:.1f}cm) -- "
              f"aborting avoid maneuver.")
        return False

    print(f"Clear. Sidestepping {AVOID_SIDESTEP_CM}cm to the adjacent row...")
    drive_forward_cm(AVOID_SIDESTEP_CM)

    print("Turning left 90 degrees to resume original heading...")
    turn_degrees(-90)

    print(f"Driving forward {AVOID_BYPASS_CM}cm to pass the object...")
    drive_forward_cm(AVOID_BYPASS_CM)

    print("Turning left 90 degrees to head back toward the original row...")
    turn_degrees(-90)

    print(f"Returning {AVOID_SIDESTEP_CM}cm to the original row...")
    drive_forward_cm(AVOID_SIDESTEP_CM)

    print("Turning right 90 degrees to resume original heading...")
    turn_degrees(90)

    print("Avoid maneuver complete -- resuming row.")
    return True


def creep_forward_cm(total_distance_cm, increment_cm=CREEP_INCREMENT_CM, pause_sec=CREEP_PAUSE_SECONDS, speed=DRIVE_PWM):
    """Drives forward as ONE continuous PID-controlled motion, pausing
    briefly at each increment_cm checkpoint to let the camera check for
    targets, then resuming -- rather than calling drive_forward_cm()
    fresh for every step.

    This matters because drive_forward_cm() resets encoder counts and
    restarts PID's internal state (integral/derivative memory) from
    scratch on every call. Doing that 20 times over 100cm (for 5cm
    increments) meant 20 separate ramp-up transients and 20 fresh PID
    "cold starts" with almost no settled distance in between to
    correct anything -- which was showing up as a lean after only a
    few increments, much sooner than in long single-shot drives.

    Here, encoders are reset ONCE at the start of the whole distance,
    and PID's integral/previous-error memory persists across every
    pause -- only the derivative timing reference resets after a
    pause (so the multi-second stop isn't misread as a huge dt spike).
    Each resume still does a brief ramp (avoids jumping straight from
    a dead stop to full PWM, which caused its own asymmetry), but this
    ramp no longer coincides with an encoder/PID reset.

    Returns one of:
      "done"               -- completed the full distance
      "obstacle"           -- ultrasonic sensor stopped it early
      "max_items_reached"  -- a pickup (CATEGORY_PICKUP) brought
                              item_count up to MAX_ITEMS_TO_PICKUP;
                              creeping stops here. Pickups below that
                              limit sweep the brush and continue
                              through the rest of the row instead.
      "avoid_blocked"      -- avoid maneuver's sidestep was also blocked
      "target"             -- unrecognized category detected, stopped
    """
    direction = 1 if total_distance_cm >= 0 else -1
    target_cm = abs(total_distance_cm)

    Bridge.call("reset_encoder_counts")

    final_left_pwm = int(direction * (speed + LEFT_TRIM))
    final_right_pwm = int(direction * (speed + RIGHT_TRIM))

    def ramp_up():
        RAMP_STEPS = 5
        RAMP_DURATION = 0.25
        for step in range(1, RAMP_STEPS + 1):
            fraction = step / RAMP_STEPS
            ramp_left = int(final_left_pwm * fraction)
            ramp_right = int(final_right_pwm * fraction)
            Bridge.call("set_motors", ramp_right, ramp_left)
            time.sleep(RAMP_DURATION / RAMP_STEPS)

    ramp_up()  # single ramp-up for the whole distance, not per-increment

    # See drive_forward_cm() for tuning history -- Kp=3.5 made error
    # grow rather than shrink once trim was corrected to 10. Restarting
    # low and re-tuning carefully against the smaller residual wobble.
    Kp = 1.0
    Ki = 0.0
    Kd = 0.0
    MAX_CORRECTION = 25
    integral_error = 0.0
    MAX_INTEGRAL = MAX_CORRECTION / max(Ki, 0.0001)

    left_count, right_count = get_counts()
    left_dist = pulses_to_distance(left_count, PULSES_PER_REV_LEFT)
    right_dist = pulses_to_distance(right_count, PULSES_PER_REV_RIGHT)
    previous_error = left_dist - right_dist
    previous_time = time.time()

    next_checkpoint_cm = increment_cm
    result = "done"

    try:
        while True:
            left_count, right_count = get_counts()
            left_dist = pulses_to_distance(left_count, PULSES_PER_REV_LEFT)
            right_dist = pulses_to_distance(right_count, PULSES_PER_REV_RIGHT)

            now = time.time()
            dt = max(now - previous_time, 0.001)
            error = left_dist - right_dist

            if USE_PID_CORRECTION:
                integral_error += error * dt
                integral_error = max(-MAX_INTEGRAL, min(MAX_INTEGRAL, integral_error))
                derivative = (error - previous_error) / dt
                correction = (Kp * error) + (Ki * integral_error) + (Kd * derivative)
                correction = max(-MAX_CORRECTION, min(MAX_CORRECTION, correction))
            else:
                correction = 0

            previous_error = error
            previous_time = now

            corrected_left_mag = max(MIN_PWM, abs(final_left_pwm) - correction)
            corrected_right_mag = max(MIN_PWM, abs(final_right_pwm) + correction)
            corrected_left_pwm = int(direction * corrected_left_mag)
            corrected_right_pwm = int(direction * corrected_right_mag)

            Bridge.call("set_motors", corrected_right_pwm, corrected_left_pwm)

            avg_dist = (left_dist + right_dist) / 2.0
            print(f"  error={error:.1f} correction={correction:.1f} L_pwm={corrected_left_pwm} R_pwm={corrected_right_pwm} avg_dist={avg_dist:.1f}")

            if check_obstacle():
                result = "obstacle"
                break

            if avg_dist >= target_cm:
                result = "done"
                break

            if avg_dist >= next_checkpoint_cm:
                Bridge.call("stop_motors")
                time.sleep(pause_sec)

                category = check_for_target()
                if category is not None:
                    if category == CATEGORY_PICKUP:
                        print("Pickup target -- stopping and sweeping brush...")
                        Bridge.call("sweep_brush")
                        increment_item_count()
                        print("Brush sweep complete.")
                        clear_detection()
                        if item_count >= MAX_ITEMS_TO_PICKUP:
                            print(f"Reached MAX_ITEMS_TO_PICKUP ({MAX_ITEMS_TO_PICKUP}) -- stopping.")
                            return "max_items_reached"
                        # else: under the limit -- keep creeping through
                        # the rest of this row.
                    elif category in AVOID_CATEGORIES:
                        print(f"Avoid target detected: {category}")
                        success = avoid_obstacle()
                        clear_detection()
                        if not success:
                            return "avoid_blocked"
                        # avoid_obstacle() drove/turned via
                        # drive_forward_cm()/turn_degrees(), which reset
                        # encoders internally -- our progress tracking
                        # here is now stale. Simplest correct approach:
                        # treat whatever's left as a fresh creep call.
                        remaining_cm = target_cm - avg_dist
                        if remaining_cm > 0:
                            return creep_forward_cm(direction * remaining_cm, increment_cm, pause_sec, speed)
                        return "done"
                    else:
                        return "target"

                # Resume: brief ramp (avoids an abrupt full-power start
                # from a dead stop) but NOT a reset of encoder counts
                # or PID's integral/error memory -- only the timing
                # reference resets so the pause itself isn't misread
                # as a huge derivative spike.
                ramp_up()
                previous_time = time.time()
                next_checkpoint_cm += increment_cm

            time.sleep(POLL_INTERVAL)
    finally:
        Bridge.call("stop_motors")

    return result


def wait_for_start():
    """Blocks until the X button is pressed on the controller — lets
    you position/reset the robot and only start the test when ready,
    rather than it firing immediately on app startup (important since
    the app may restart once on its own before settling)."""
    pygame.init()
    pygame.joystick.init()

    if pygame.joystick.get_count() == 0:
        print("No joystick found — starting immediately instead.")
        return

    joy = pygame.joystick.Joystick(0)
    joy.init()
    print(f"Connected to: {joy.get_name()}")
    print("Press X to start the test...")

    while True:
        pygame.event.pump()
        if joy.get_button(START_BUTTON):
            print("Starting!")
            time.sleep(0.3)  # brief pause so you can release the button
            return
        time.sleep(0.05)


def creep_two_rows(row_length_cm=None):
    """Test helper: creeps down one row, turns onto the next row, and
    creeps down a second row -- same row-transition logic as
    lawnmower_sweep(), but using a short test row length (defaults to
    TEST_DRIVE_DISTANCE_CM) instead of the full PLAY_AREA_LENGTH_CM, so
    you can validate creep + turn + creep on a small scale before
    trusting the full multi-row sweep.

    Stops early (does not attempt row 2) if row 1 hits an obstacle,
    reaches MAX_ITEMS_TO_PICKUP, has an avoid maneuver that fails
    (adjacent row also blocked), or detects an unrecognized category.
    A pickup below the item limit, or a successful avoid maneuver,
    does NOT stop here -- those already resume within
    creep_forward_cm() itself and row 1 simply finishes normally.
    """
    if row_length_cm is None:
        row_length_cm = TEST_DRIVE_DISTANCE_CM

    print(f"--- Row 1: creeping forward {row_length_cm}cm ---")
    result = creep_forward_cm(row_length_cm)
    print(f"Row 1 result: {result}")

    if result == "obstacle":
        print("Obstacle detected on row 1 -- stopping, not attempting row 2.")
        return
    if result == "max_items_reached":
        print(f"Reached MAX_ITEMS_TO_PICKUP ({MAX_ITEMS_TO_PICKUP}) on row 1 -- "
              f"stopping, not attempting row 2.")
        return
    if result == "avoid_blocked":
        print("Avoid maneuver failed on row 1 -- stopping, not attempting row 2.")
        return
    if result == "target":
        category = check_for_target()
        print(f"Unrecognized target detected on row 1 ({category}) -- "
              f"stopping, not attempting row 2.")
        clear_detection()
        return

    print("--- Turning onto row 2 ---")
    turn_degrees(90)
    drive_forward_cm(ROW_SPACING_CM)
    turn_degrees(90)

    print(f"--- Row 2: creeping forward {row_length_cm}cm ---")
    result = creep_forward_cm(row_length_cm)
    print(f"Row 2 result: {result}")
    print("Two-row test complete.")


def drive_two_rows_full(row_length_cm=None):
    """Test helper: drives two rows full-speed, continuous
    drive_forward_cm() -- NOT creep_forward_cm(). No stop-scan-move
    increments, no camera pause/check between steps. Just row 1 ->
    turn -> row spacing -> turn -> row 2, straight through.

    Since there's no camera checking here, pickup/avoid categories are
    NOT acted on during this test -- the camera Brick's background
    callback still runs and will still flag detections internally, but
    nothing in this function ever calls check_for_target(), so they're
    simply ignored. This is a pure movement/turn-geometry test, not a
    detection test.

    Stops early (does not attempt row 2) if row 1 hits an obstacle.
    """
    if row_length_cm is None:
        row_length_cm = TEST_DRIVE_DISTANCE_CM

    print(f"--- Row 1: driving forward {row_length_cm}cm (full speed, no creep) ---")
    result = drive_forward_cm(row_length_cm)
    print(f"Row 1 result: {result}")

    if result == "obstacle":
        print("Obstacle detected on row 1 -- stopping, not attempting row 2.")
        return

    # Brief settle pause between each step -- without this, residual
    # momentum from one motion can bleed into the start of the next
    # before the robot's truly at rest, throwing off the encoder-based
    # turn measurement (this was causing the second turn to land off
    # from the commanded 90 degrees).
    ROW_TRANSITION_SETTLE_SECONDS = 0.5

    print("--- Turning onto row 2 ---")
    time.sleep(ROW_TRANSITION_SETTLE_SECONDS)
    turn_degrees(90)
    time.sleep(ROW_TRANSITION_SETTLE_SECONDS)
    drive_forward_cm(ROW_SPACING_CM)
    time.sleep(ROW_TRANSITION_SETTLE_SECONDS)
    turn_degrees(90)
    time.sleep(ROW_TRANSITION_SETTLE_SECONDS)

    print(f"--- Row 2: driving forward {row_length_cm}cm (full speed, no creep) ---")
    result = drive_forward_cm(row_length_cm)
    print(f"Row 2 result: {result}")
    print("Two-row full-speed test complete.")


def lawnmower_sweep():
    """Full lawnmower-pattern coverage of the play area. Drives the
    length of the play area, turns 90 degrees, moves over one row's
    width, turns 90 degrees again (facing back the way it came, or
    onward -- alternating each row), and repeats until the full width
    has been covered.

    Alternates turn direction each row so the robot "boustrophedons"
    back and forth (like an ox plowing a field) rather than always
    returning to the same side.
    """
    row_length_cm = PLAY_AREA_LENGTH_CM
    num_rows = math.ceil(PLAY_AREA_WIDTH_CM / ROW_SPACING_CM)

    print(f"Starting lawnmower sweep: {num_rows} rows, "
          f"{row_length_cm}cm per row, {ROW_SPACING_CM}cm row spacing.")

    # Alternate turn direction each row: right-right (turn onto next
    # row, same direction both times) so the robot always turns the
    # same way rather than needing separate left/right logic.
    for row in range(num_rows):
        print(f"--- Row {row + 1}/{num_rows}: creeping forward {row_length_cm}cm ---")
        result = creep_forward_cm(row_length_cm)

        if result == "obstacle":
            print("Obstacle detected mid-row! Stopping sweep for now.")
            print("(Obstacle avoidance maneuver not yet implemented --")
            print(" this is a placeholder stop, not a real avoid-and-continue.)")
            return

        if result == "max_items_reached":
            print(f"Reached MAX_ITEMS_TO_PICKUP ({MAX_ITEMS_TO_PICKUP}). Stopping sweep here for now")
            print("(per current test setup -- not continuing to next row).")
            return

        if result == "avoid_blocked":
            print("Avoid maneuver failed -- adjacent row was blocked too.")
            print("Stopping sweep for now.")
            return

        if result == "target":
            # creep_forward_cm() now handles pickup and avoid categories
            # internally -- landing here means a genuinely unrecognized
            # category was detected close enough to act on.
            category = check_for_target()
            print(f"Unrecognized target detected mid-row: {category}")
            print("(No behavior defined for this category -- placeholder stop.)")
            clear_detection()
            print("Stopping sweep for now.")
            return

        # Last row doesn't need to turn to move to a "next" row.
        if row == num_rows - 1:
            print("Final row complete. Sweep done.")
            break

        print(f"--- Row {row + 1}/{num_rows}: turning to next row ---")
        # Turn 90 degrees toward the next row, move over one row's
        # width, then turn 90 degrees again to face back down the
        # play area length -- alternating which way we now face each
        # time (boustrophedon pattern).
        turn_direction = 1 if row % 2 == 0 else -1
        turn_degrees(90 * turn_direction)
        drive_forward_cm(ROW_SPACING_CM)
        turn_degrees(90 * turn_direction)

    print("Lawnmower sweep complete!")


# ---- Test harness ----
# NOTE: App.run() is what actually starts the Bricks' background
# services (WebUI's web server, the camera detection stream) -- just
# calling drive_forward_cm()/turn_degrees()/etc directly at module
# level configures the Bricks but never starts them, which is why the
# WebUI wasn't coming up. Matching Manual Movement's pattern: run the
# chosen test mode once inside a loop() callback passed to App.run().

STARTUP_DELAY_SECONDS = 5
_test_started = False


def loop():
    global _test_started

    if _test_started:
        time.sleep(LOOP_DELAY)
        return
    _test_started = True

    print(f"Waiting {STARTUP_DELAY_SECONDS}s before starting movement "
          f"(gives the camera stream time to come up)...")
    for remaining in range(STARTUP_DELAY_SECONDS, 0, -1):
        print(f"  starting in {remaining}...")
        time.sleep(1)
    print("Starting now.")

    if TEST_MODE == "drive":
        print(f"Testing drive_forward_cm({TEST_DRIVE_DISTANCE_CM})...")
        result = drive_forward_cm(TEST_DRIVE_DISTANCE_CM)
        print(f"Result: {result}. Measure actual distance traveled and compare!")
        print(f"(Target was {TEST_DRIVE_DISTANCE_CM}cm — the code stopped once its calculated distance reached this.)")

    elif TEST_MODE == "turn":
        print(f"Testing turn_degrees({TEST_TURN_DEGREES})...")
        turn_degrees(TEST_TURN_DEGREES)
        print("Turn complete. Measure actual angle turned and compare!")

    elif TEST_MODE == "creep":
        print(f"Testing creep_forward_cm({TEST_DRIVE_DISTANCE_CM}), "
              f"{CREEP_INCREMENT_CM}cm increments, {CREEP_PAUSE_SECONDS}s pause...")
        result = creep_forward_cm(TEST_DRIVE_DISTANCE_CM)
        print(f"Result: {result}")
        if result == "target":
            print(f"Detected category: {check_for_target()}")
            clear_detection()

    elif TEST_MODE == "creep2":
        print(f"Testing creep_two_rows({TEST_DRIVE_DISTANCE_CM})...")
        creep_two_rows(TEST_DRIVE_DISTANCE_CM)

    elif TEST_MODE == "sweep2":
        print(f"Testing drive_two_rows_full({TEST_DRIVE_DISTANCE_CM})...")
        drive_two_rows_full(TEST_DRIVE_DISTANCE_CM)

    elif TEST_MODE == "sweep":
        lawnmower_sweep()

    print("Test mode complete. App staying alive so the WebUI/camera stream stays up.")


LOOP_DELAY = 0.1
App.run(user_loop=loop)
