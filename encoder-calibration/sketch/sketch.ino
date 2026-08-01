/*
  Encoder Calibration app — Uno Q MCU side.
  Standalone app for manually calibrating PULSES_PER_REV_LEFT/RIGHT by
  hand-spinning each wheel a known number of revolutions and reading
  raw pulse counts before/after.

  This file goes in your App Lab project's: sketch/sketch.ino

  Wiring:
    Left encoder:  D8  (Yellow signal wire; Blue=3.3V, Black=GND)
    Right encoder: D2  (Yellow signal wire; Blue=3.3V, Black=GND)

  No motor pins needed — this app only counts pulses while you hand-spin
  each wheel. Motors are left disconnected/unused here.

  Exposes RPC functions that the Python (MPU) side calls:
    get_encoder_counts()    -> returns "left,right" pulse counts as a string
    reset_encoder_counts()  -> zeros both counters
*/

#include "Arduino_RouterBridge.h"

const int LEFT_ENCODER_PIN = 8;
const int RIGHT_ENCODER_PIN = 2;

volatile unsigned long leftPulseCount = 0;
volatile unsigned long rightPulseCount = 0;

// Same debounce filtering used in the main autonomous sketch.
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

void setup() {
  pinMode(LEFT_ENCODER_PIN, INPUT);
  pinMode(RIGHT_ENCODER_PIN, INPUT);
  attachInterrupt(digitalPinToInterrupt(LEFT_ENCODER_PIN), leftEncoderISR, RISING);
  attachInterrupt(digitalPinToInterrupt(RIGHT_ENCODER_PIN), rightEncoderISR, RISING);

  Bridge.begin();
  Bridge.provide("get_encoder_counts", get_encoder_counts);
  Bridge.provide("reset_encoder_counts", reset_encoder_counts);
}

void loop() {
  // Nothing to do here — everything happens via RPC calls from Python.
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
