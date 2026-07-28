/*
 * Minimal reference sketch: stream raw ADC counts over the Arduino Due's
 * Native USB Port, in the line format `serial-sensor` expects.
 *
 *   <channel>,<raw_count>\r\n      e.g.  A0,2048
 *
 * The host side does all interpretation: this sketch deliberately sends
 * raw counts, never volts or physical units. What a count means belongs
 * in the host's calibration.toml, where it can be changed without
 * reflashing the board (see docs/serial-sensor.md).
 *
 * Wiring/usage notes:
 *  - Use the *Native* USB port (the one nearer the RESET button), not the
 *    Programming port. SerialUSB is the native port; Serial is the other.
 *  - The Due's analog inputs are 3.3V max. Do NOT feed them 5V.
 *  - analogReadResolution(12) matches serial-sensor's default of
 *    --resolution-bits 12; change both together or neither.
 *
 * NOT YET TESTED ON HARDWARE — written against the Due's documented
 * behaviour while no board was available. Verify readings against a known
 * voltage before trusting it.
 */

// Channels to sample. Each name must match a [channels.<name>] entry in
// the host's calibration.toml, or the host will log it as uncalibrated
// once and ignore it.
const char *CHANNEL_NAMES[] = {"A0", "A1"};
const int CHANNEL_PINS[] = {A0, A1};
const int CHANNEL_COUNT = sizeof(CHANNEL_PINS) / sizeof(CHANNEL_PINS[0]);

// Sampling period. The host stamps readings on arrival and does not pace
// them itself, so this alone sets the sample rate.
const unsigned long SAMPLE_INTERVAL_MS = 1000;

void setup() {
  // The baud rate is ignored on the native port — USB CDC always runs at
  // full USB speed — but the API still requires an argument.
  SerialUSB.begin(115200);

  // 12-bit: counts run 0..4095 across the 0..3.3V input range.
  analogReadResolution(12);
}

void loop() {
  for (int i = 0; i < CHANNEL_COUNT; i++) {
    SerialUSB.print(CHANNEL_NAMES[i]);
    SerialUSB.print(',');
    // println() emits CRLF; the host tolerates both CRLF and LF.
    SerialUSB.println(analogRead(CHANNEL_PINS[i]));
  }

  delay(SAMPLE_INTERVAL_MS);
}
