/*
  Autonomous Movement app — Uno Q MCU side.
  Combines L298N motor control, quadrature-style encoder pulse counting
  (single Yellow signal wire per motor), the Grove Ultrasonic Ranger
  V2.0 distance sensor, and the sweeper brush servo, all exposed via
  Bridge/RPC so the Python (Linux) side can do closed-loop,
  encoder-accurate movement with obstacle detection and pickup.

  This file goes in your App Lab project's: sketch/sketch.ino

  Wiring:
    Left motor:  ENA=9, IN1=4, IN2=5
    Right motor: ENB=3, IN3=6, IN4=7
    Left encoder:  D8  (Yellow signal wire; Blue=3.3V, Black=GND)
    Right encoder: D2  (Yellow signal wire; Blue=3.3V, Black=GND)
    Ultrasonic:    D10 (SIG, single pin trigger+echo; VCC=3.3V, GND=GND)
    Sweeper servo: D11 (SG90)

  Exposes RPC functions that the Python (MPU) side calls:
    set_motors(left, right)    -> left/right values from -255 to 255
    stop_motors()              -> immediate stop
    get_encoder_counts()       -> returns "left,right" pulse counts as a string
    reset_encoder_counts()     -> zeros both counters
    get_distance()             -> returns distance in cm from the ultrasonic
                                   sensor (-1 if no echo received)
    sweep_brush()               -> sweeps the brush servo in and back out
*/

#include "Arduino_RouterBridge.h"
#include <Servo.h>

// ---- Left motor ----
const int ENA = 9;
const int IN1 = 4;
const int IN2 = 5;

// ---- Right motor ----
const int ENB = 3;
const int IN3 = 6;
const int IN4 = 7;

// ---- Encoders (Yellow signal wire only, one channel each for now) ----
const int LEFT_ENCODER_PIN = 8;   // left motor's Yellow wire
const int RIGHT_ENCODER_PIN = 2;  // right motor's Yellow wire

volatile unsigned long leftPulseCount = 0;
volatile unsigned long rightPulseCount = 0;

// Filters out electrical noise/contact bounce without missing real pulses.
const unsigned long DEBOUNCE_MICROS = 300;
volatile unsigned long lastLeftPulseMicros = 0;
volatile unsigned long lastRightPulseMicros = 0;

void leftEncoderISR() {
  unsigned long now = micros();
  if (now - lastLeftPulseMicros > DEBOUNCE_MICROS) {
    leftPulseCount++;
    lastLeftPulseMicros = now;
  }
}

void rightEncoderISR() {
  unsigned long now = micros();
  if (now - lastRightPulseMicros > DEBOUNCE_MICROS) {
    rightPulseCount++;
    lastRightPulseMicros = now;
  }
}

// ---- Ultrasonic sensor (Grove Ultrasonic Ranger V2.0) ----
// Single pin does both trigger (output) and echo (input).
const int ULTRASONIC_PIN = 10;

// ---- Sweeper brush servo (SG90) ----
const int SERVO_PIN = 11;
Servo sweeperServo;
const int SERVO_REST_ANGLE = 180;   // resting/retracted position
const int SERVO_SWEEP_ANGLE = 90;   // extended/sweeping position

// Failsafe: stop if no command received within this window
const unsigned long FAILSAFE_TIMEOUT_MS = 500;
unsigned long lastCallTime = 0;

void setup() {
  // NOTE: deliberately NOT calling pinMode() on ENA/ENB (the PWM pins).
  // On the Uno Q's Zephyr/STM32 core, calling pinMode(OUTPUT) on a PWM
  // pin before analogWrite() can break proper PWM scaling. analogWrite()
  // configures the pin itself, so we skip pinMode() for ENA/ENB only.
  pinMode(IN1, OUTPUT);
  pinMode(IN2, OUTPUT);
  pinMode(IN3, OUTPUT);
  pinMode(IN4, OUTPUT);

  stopMotors();

  pinMode(LEFT_ENCODER_PIN, INPUT);
  pinMode(RIGHT_ENCODER_PIN, INPUT);
  attachInterrupt(digitalPinToInterrupt(LEFT_ENCODER_PIN), leftEncoderISR, RISING);
  attachInterrupt(digitalPinToInterrupt(RIGHT_ENCODER_PIN), rightEncoderISR, RISING);

  Bridge.begin();
  Bridge.provide("set_motors", set_motors);
  Bridge.provide("stop_motors", stop_motors_rpc);
  Bridge.provide("get_encoder_counts", get_encoder_counts);
  Bridge.provide("reset_encoder_counts", reset_encoder_counts);
  Bridge.provide("get_distance", get_distance);
  Bridge.provide("sweep_brush", sweep_brush);

  // Reach rest position, then detach so the servo isn't continuously
  // holding/fighting position while idle (fixes idle jitter -- same
  // fix confirmed working in the Manual Movement app).
  sweeperServo.attach(SERVO_PIN);
  sweeperServo.write(SERVO_REST_ANGLE);
  delay(300);  // give it time to actually reach rest position
  sweeperServo.detach();
}

void loop() {
  // Failsafe: if Python hasn't called us recently, stop the motors.
  if (lastCallTime != 0 && millis() - lastCallTime > FAILSAFE_TIMEOUT_MS) {
    stopMotors();
  }
}

// value: -255..255. Negative = reverse, positive = forward.
void setMotor(int value, int enPin, int in1Pin, int in2Pin) {
  value = constrain(value, -255, 255);

  if (value > 0) {
    digitalWrite(in1Pin, HIGH);
    digitalWrite(in2Pin, LOW);
  } else if (value < 0) {
    digitalWrite(in1Pin, LOW);
    digitalWrite(in2Pin, HIGH);
  } else {
    digitalWrite(in1Pin, LOW);
    digitalWrite(in2Pin, LOW);
  }

  analogWrite(enPin, abs(value));
}

void stopMotors() {
  setMotor(0, ENA, IN1, IN2);
  setMotor(0, ENB, IN3, IN4);
}

// ---- RPC-exposed functions (called from Python via Bridge.call) ----

bool set_motors(int left, int right) {
  setMotor(left, ENA, IN1, IN2);
  setMotor(right, ENB, IN3, IN4);
  lastCallTime = millis();
  return true;
}

bool stop_motors_rpc() {
  stopMotors();
  lastCallTime = millis();
  return true;
}

String get_encoder_counts() {
  String result = String(leftPulseCount) + "," + String(rightPulseCount);
  return result;
}

bool reset_encoder_counts() {
  leftPulseCount = 0;
  rightPulseCount = 0;
  return true;
}

// Returns distance in centimeters as a float. Returns -1 if no echo
// was received within the timeout (e.g. nothing in range, or a
// wiring issue).
float get_distance() {
  // Send a >10us trigger pulse on the shared SIG pin.
  pinMode(ULTRASONIC_PIN, OUTPUT);
  digitalWrite(ULTRASONIC_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(ULTRASONIC_PIN, HIGH);
  delayMicroseconds(12);
  digitalWrite(ULTRASONIC_PIN, LOW);

  // Switch to input to read the echo pulse width.
  pinMode(ULTRASONIC_PIN, INPUT);
  unsigned long duration = pulseIn(ULTRASONIC_PIN, HIGH, 30000UL);  // 30ms timeout (~5m range)

  if (duration == 0) {
    return -1.0;  // no echo received (out of range or nothing detected)
  }

  // Distance = echo high time * speed of sound (340 m/s) / 2
  // 340 m/s = 0.034 cm/us, divided by 2 for round-trip.
  float distanceCm = duration * 0.017;
  return distanceCm;
}

// Sweeps the brush servo in and back out. Note: this uses blocking
// delay() calls, so it will briefly pause the failsafe check in
// loop() for a little over a second while sweeping -- an acceptable
// tradeoff for a discrete pickup action where the robot should
// already be stopped anyway.
bool sweep_brush() {
  sweeperServo.attach(SERVO_PIN);
  sweeperServo.write(SERVO_SWEEP_ANGLE);  // sweep in
  delay(1000);
  sweeperServo.write(SERVO_REST_ANGLE);   // sweep back out
  delay(300);  // give it time to actually reach rest position
  sweeperServo.detach();  // stop holding position to avoid idle jitter
  return true;
}
