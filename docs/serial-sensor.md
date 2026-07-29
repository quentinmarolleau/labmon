# serial-sensor

Reads a real board over a serial port and writes calibrated readings to
InfluxDB. This is the non-mock counterpart to
[`docs/mock-sensor.md`](mock-sensor.md), which simulates readings instead.

```bash
uv run serial-sensor --port /dev/labmon-due --calibration calibration.toml
```

## How a reading becomes a data point

The board sends **raw ADC counts**, never physical units — what a count
means lives on the host, where it can be changed without reflashing:

1. The board streams one line per reading: `<channel>,<raw_count>`.
   The count may be fractional (see [Averaging on the
   board](#averaging-on-the-board) below).
2. The count is scaled to the voltage the ADC saw, using
   `--resolution-bits` and `--vref`.
3. That voltage is converted to a physical quantity by the channel's
   entry in the calibration file (see the modes below).
4. The result is written as a point, tagged with the channel's
   `sensor_id` and its `unit`, in a `value` field — alongside an
   `input_volts` field holding the voltage from step 2 (see
   [Keeping the conversion's input](#keeping-the-conversions-input)).

A malformed line, or a channel with no calibration entry, is logged and
skipped rather than being fatal — one bad line shouldn't stop a
long-running sensor.

Writing is decoupled from reading by `labmon.writer.PointWriter`, so a
slow or briefly unreachable InfluxDB never stalls the read loop. The
sustainable sample rate, and the point at which a long outage does start
to push back on the board, are measured in
[`docs/latency.md`](latency.md).

## The calibration file

See [`calibration.example.toml`](../calibration.example.toml), which
covers every mode. One section per channel, each naming a `sensor_id`, a
`measurement`, and how to convert:

| `mode` | Converts by | Needs |
|---|---|---|
| `linear` (default) | one dimensioned factor | `conversion_factor` |
| `affine` | a factor plus an offset | `conversion_factor`, `offset` |
| `spline` | a cubic through measured points | `voltages`, `values`, `value_unit` |
| `piecewise_linear` | straight segments between the same points | `voltages`, `values`, `value_unit` |
| `expression` | a formula in `v` | `expression`, `value_unit` |

```toml
[channels.A0]
sensor_id = "cryo-77k"
measurement = "temperature"
conversion_factor = "42.5 kelvin / volt"
```

Where the unit can be **derived** it is, rather than declared: volts
times a factor in `kelvin / volt` gives kelvin, so `linear` and `affine`
have no unit to state (or get wrong). The interpolation and expression
modes can't derive one, so they name a `value_unit` explicitly.

Every conversion is built **and trial-applied at startup**, so a typo
fails immediately rather than part-way through a run. For `affine` that
includes checking the offset is dimensionally consistent with
factor × volts — adding millibars to kelvin is caught here.

Voltages are always in **volts**, the ADC's own output. A datasheet
quoted in millivolts gets scaled once, visibly, in the config.

### Keeping the conversion's input

Each reading is stored twice: the converted `value`, and the voltage it
came from in an `input_volts` field.

The second one is what makes a calibration correctable after the fact.
Conversions are not generally invertible — `piecewise_linear` clamps
outside its measured range, so every out-of-range reading collapsed onto
the same endpoint, and `expression` is arbitrary — so a `value` alone
cannot be recomputed under a corrected calibration. It also distinguishes
a fault in the instrument from one in the config: if `value` jumps while
`input_volts` is flat, the sensor is fine and the calibration isn't. A
floating or saturated input shows up as `input_volts` pinned at 0 or at
`--vref`, which a clamping conversion would otherwise hide behind a
plausible-looking physical value.

It costs one float per reading. Turn it off per channel, or file-wide:

```toml
store_input = false          # file-wide default

[channels.A0]
sensor_id = "cryo-77k"
measurement = "temperature"
conversion_factor = "42.5 kelvin / volt"
store_input = true           # ... overridden for this channel
```

Existing Grafana panels are unaffected: they select `value` explicitly
and won't pick up the new field unless asked.

Note that `--vref` and `--resolution-bits` are *not* recorded. They don't
need to be — both scale the stored voltage linearly, so getting either
wrong stays correctable with a query.

### Choosing between `spline` and `piecewise_linear`

Both take the same measured `voltages`/`values` points, which must be
monotonic but may fall as well as rise — an NTC thermistor's voltage
drops as it warms, and such a series is used as-is rather than needing
to be written backwards. They differ in two ways worth knowing:

- `spline` fits a smooth cubic (good for a genuinely curved response)
  and **extrapolates** beyond the measured range. `piecewise_linear`
  joins the points with straight lines and **clamps** to the end values
  instead — safer when a reading strays outside what was characterised.
- `spline` needs scipy: `uv sync --extra spline`. `piecewise_linear` needs
  nothing beyond the base install, which matters on a small client.
  Note that the `Dockerfile` installs the base dependencies only, so a
  containerized `serial-sensor` cannot use `spline` until that extra is
  added to the image.

### Offset units don't work

`degC` and `degF` are offset units, and pint refuses to scale them
(0°C isn't "no temperature", so multiplying by it is ambiguous).
`serial-sensor` rejects such a file at startup with a message pointing
here. Use `kelvin` for an absolute temperature, or `delta_degC` for a
relative span:

```toml
conversion_factor = "10 delta_degC / volt"   # a 10°C change per volt
```

### The `expression` mode

`v` is the voltage in volts, and common maths functions are available
(`sqrt`, `log`, `log10`, `exp`, `sin`, …):

```toml
mode = "expression"
expression = "10**(1.667*v - 11.33)"
value_unit = "mbar"
```

Expressions are evaluated by [asteval](https://github.com/lmfit/asteval)
in a restricted interpreter — arithmetic only, with no ability to import
modules or reach the filesystem. An expression that references an
unknown name, or returns something that isn't a number, is rejected at
startup.

## Options

| Flag | Default | Purpose |
|---|---|---|
| `--port` | *(required)* | Serial device to read |
| `--calibration` | *(required)* | Path to the TOML calibration file |
| `--baudrate` | `115200` | See the note below — ignored on native USB |
| `--resolution-bits` | `12` | ADC resolution (the Due's default) |
| `--vref` | `3.3` | ADC reference voltage (the Due's default) |

Connection settings (`INFLUXDB_HOST`, `INFLUXDB_DATABASE`,
`INFLUXDB3_AUTH_TOKEN`) come from the environment, exactly as for
[`mock-sensor`](mock-sensor.md#configuration).

## Arduino Due specifics

The reference firmware is
[`firmware/due_native_serial/due_native_serial.ino`](../firmware/due_native_serial/due_native_serial.ino).
It is written against the Due's documented behaviour but **has not been
run on hardware yet** — verify against a known voltage before trusting
readings.

- **Use the Native USB port** (nearer the RESET button), not the
  Programming port. In the sketch that's `SerialUSB`, not `Serial`.
- **Baud rate is meaningless there.** The native port is a USB CDC device
  running at full USB speed regardless of `--baudrate`; pyserial simply
  requires a value. Don't spend time tuning it.
- **Opening the port doesn't reset the board.** The Programming port
  resets the sketch via DTR; the Native port doesn't, so `serial-sensor`
  can reconnect without disturbing a running sketch.
- **Analog inputs are 3.3V max** — feeding them 5V damages the board.

### Averaging on the board

The reference sketch reports the mean of a burst of conversions rather
than a single snapshot, sent as a fractional count (`A0,2048.31` — the
decimals carry the sub-step resolution the averaging buys).

The burst length is derived from `MAINS_FREQUENCY_HZ`, not set as a round
number of milliseconds: integrating over a whole number of mains periods
cancels 50/60 Hz pickup. **On a 60 Hz grid, change that constant** — at
50 the rejection is lost. Widen the window to `2 *` or `3 *` a period for
more averaging on a noisy channel; at one reading per second, 20 ms costs
2% of the interval.

The sketch also discards one conversion and pauses `ADC_SETTLING_US`
after switching channels, before the burst. The Due's inputs share one
ADC behind a multiplexer, so the first conversion after a switch is still
pulled toward the previous channel's voltage — a constant offset, which
averaging cannot remove. It is invisible on a single-channel setup and
appears as soon as a second channel is added.

### A stable device path

`/dev/ttyACM0` numbering depends on enumeration order, so pin the board
to a fixed name with a udev rule. Check the actual IDs first, since some
Due boards report vendor `2a03` rather than `2341`:

```bash
lsusb | grep -i arduino
```

Then, in `/etc/udev/rules.d/99-labmon.rules` (adjusting the IDs to match):

```
SUBSYSTEM=="tty", ATTRS{idVendor}=="2341", ATTRS{idProduct}=="003e", SYMLINK+="labmon-due"
```

```bash
sudo udevadm control --reload-rules && sudo udevadm trigger
```

`003e` is the Native port; `003d` is the Programming port.

Reading the device also needs permission — on Debian and Raspberry Pi OS
that means being in the `dialout` group:

```bash
sudo usermod -aG dialout "$USER"   # log out and back in afterwards
```

## Running it as a service

Either deployment path from [`docs/client-setup.md`](client-setup.md)
works; the board can be plugged into the server itself or into a separate
client machine — nothing about the code cares which.

**Docker**: uncomment the `serial-sensor` service in
[`docker-compose.client.yml`](../docker-compose.client.yml). It overrides
the image's default entrypoint, passes the device through, and mounts the
calibration file read-only.

**Bare install**: use
[`deploy/labmon-serial-sensor.service`](../deploy/labmon-serial-sensor.service)
as a systemd unit so it starts on boot and restarts on failure.

## Testing without hardware

The full path can be exercised against a virtual serial port, which is
useful for checking calibration values before a board arrives:

```bash
# Terminal 1 — create a pty pair and note the two device names
socat -d -d pty,raw,echo=0 pty,raw,echo=0

# Terminal 2 — read one end
uv run serial-sensor --port /dev/pts/N --calibration calibration.toml

# Terminal 3 — feed the other end
printf 'A0,2048\r\n' > /dev/pts/M
```
