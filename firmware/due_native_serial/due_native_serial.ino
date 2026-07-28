/*
 * Minimal reference sketch: stream raw ADC counts over the Arduino Due's
 * Native USB Port, in the line format `serial-sensor` expects.
 *
 *   <channel>,<raw_count>\r\n      e.g.  A0,2048.31
 *
 * The host side does all interpretation: this sketch deliberately sends
 * raw counts, never volts or physical units. What a count means belongs
 * in the host's calibration.toml, where it can be changed without
 * reflashing the board (see docs/serial-sensor.md).
 *
 * Each reported count is the mean of a burst of conversions rather than a
 * single snapshot — see AVERAGING_WINDOW_US below for why, and why the
 * count is fractional.
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
// them itself, so this alone sets the sample rate. The time spent
// averaging (below) adds to it, so the true period is slightly longer —
// harmless, since nothing downstream assumes an exact cadence.
const unsigned long SAMPLE_INTERVAL_MS = 1000;

// Mains frequency: 50 Hz across most of the world, 60 Hz in North America
// and parts of Asia. Set it to match the local grid.
const unsigned long MAINS_FREQUENCY_HZ = 50;

// How long to average each channel over. Averaging N conversions cuts
// uncorrelated noise by sqrt(N), but the bigger win is that integrating
// over a whole number of mains periods cancels 50/60 Hz pickup outright:
// the interference sums to zero over a full cycle. That only works if the
// window is an exact multiple of the mains period, which is why this is
// derived from MAINS_FREQUENCY_HZ rather than being a round number of
// milliseconds. Use 2 * or 3 * this for more averaging; a value that is
// not a whole number of periods loses the hum rejection.
const unsigned long AVERAGING_WINDOW_US = 1000000UL / MAINS_FREQUENCY_HZ;

// The Due's analog inputs share one ADC behind a multiplexer. Switching
// channels leaves the sample-and-hold capacitor still charged from the
// previous input, so the first conversion after a switch is pulled toward
// the previous channel's voltage. Discarding one conversion and pausing
// lets the capacitor settle on the new input.
//
// This has to happen *before* the averaging burst, not during it: a
// settling error biases every sample the same way, and averaging cannot
// remove a constant offset. It costs one wasted conversion plus this
// delay, once per channel per burst — well under 1% of the window.
//
// The error is largest between channels sitting at very different
// voltages, and vanishes when only one channel is sampled — so a
// single-channel setup will not show it, and adding a second channel
// later would silently degrade both.
const unsigned long ADC_SETTLING_US = 50;

void setup() {
  // The baud rate is ignored on the native port — USB CDC always runs at
  // full USB speed — but the API still requires an argument.
  SerialUSB.begin(115200);

  // 12-bit: counts run 0..4095 across the 0..3.3V input range.
  analogReadResolution(12);
}

// Average every conversion that fits in AVERAGING_WINDOW_US on one pin.
//
// The loop is bounded by elapsed time, not by a sample count, because the
// mains rejection above depends on the window's *duration*. A fixed count
// would stretch or shrink the window with whatever analogRead() actually
// costs on a given core version, silently breaking that property.
static double average_channel(int pin) {
  // Let the multiplexer settle on this pin before accumulating.
  analogRead(pin);
  delayMicroseconds(ADC_SETTLING_US);

  unsigned long sum = 0;
  unsigned long samples = 0;
  // micros() wraps roughly every 71 minutes; unsigned subtraction still
  // gives the correct elapsed time across the wrap.
  const unsigned long started_at = micros();
  do {
    sum += analogRead(pin);
    samples++;
  } while (micros() - started_at < AVERAGING_WINDOW_US);

  // A 12-bit count peaks at 4095, so sum stays far inside 32 bits for any
  // plausible window. do/while guarantees samples >= 1.
  return (double)sum / (double)samples;
}

void loop() {
  for (int i = 0; i < CHANNEL_COUNT; i++) {
    SerialUSB.print(CHANNEL_NAMES[i]);
    SerialUSB.print(',');
    // Two decimals: rounding the mean back to a whole count would throw
    // away exactly the sub-LSB resolution the averaging just bought.
    // println() emits CRLF; the host tolerates both CRLF and LF.
    SerialUSB.println(average_channel(CHANNEL_PINS[i]), 2);
  }

  delay(SAMPLE_INTERVAL_MS);
}
