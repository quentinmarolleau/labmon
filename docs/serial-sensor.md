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
2. The count is scaled to the voltage the ADC saw, using
   `--resolution-bits` and `--vref`.
3. That voltage is multiplied by the channel's `conversion_factor` from
   the calibration file. The factor carries its own units, so the
   resulting physical unit is **derived** rather than declared.
4. The result is written as a point, tagged with the channel's
   `sensor_id` and the derived `unit`.

A malformed line, or a channel with no calibration entry, is logged and
skipped rather than being fatal — one bad line shouldn't stop a
long-running sensor.

## The calibration file

See [`calibration.example.toml`](../calibration.example.toml). One
section per channel:

```toml
[channels.A0]
sensor_id = "cryo-77k"
measurement = "temperature"
conversion_factor = "42.5 kelvin / volt"
```

Factors are parsed **and trial-applied at startup**, so a typo fails
immediately rather than part-way through a run.

### Offset units don't work

`degC` and `degF` are offset units, and pint refuses to multiply by them
(0°C isn't "no temperature", so scaling one is ambiguous). `serial-sensor`
rejects such a file at startup with a message pointing here. Use `kelvin`
for an absolute temperature, or `delta_degC` for a relative span:

```toml
conversion_factor = "10 delta_degC / volt"   # a 10°C change per volt
```

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
