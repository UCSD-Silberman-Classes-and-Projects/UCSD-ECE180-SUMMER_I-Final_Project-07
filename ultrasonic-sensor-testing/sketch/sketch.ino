/*
  Ultrasonic Sensor Testing app — Uno Q MCU side.
  Grove Ultrasonic Ranger V2.0, using the Bridge/RPC system so the
  Python (Linux) side can request live distance readings.

  This file goes in your App Lab project's: sketch/sketch.ino

  Wiring:
    VCC -> 3.3V
    GND -> any GND pin
    SIG -> D10 (change ULTRASONIC_PIN below if wired elsewhere)

  Exposes one RPC function that the Python (MPU) side calls:
    get_distance()  -> returns distance in cm as a float, or -1.0 if
                        no echo was received (out of range / wiring issue)
*/

#include "Arduino_RouterBridge.h"

// Single pin does both trigger (output) and echo (input).
const int ULTRASONIC_PIN = 10;

void setup() {
  Bridge.begin();
  Bridge.provide("get_distance", get_distance);
}

void loop() {
  // Nothing needed here — get_distance() is called on-demand via RPC.
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
