# Latency and throughput

Where time actually goes between a board's ADC and a row in InfluxDB,
measured rather than assumed. This exists because one layer turned out
to be a thousand times more expensive than the rest, in a way that was
invisible until it throttled a sensor loop.

All figures below were measured on the machine described under
[Reproducing the measurements](#reproducing-the-measurements); treat them
as orders of magnitude and relative costs, not absolutes.

## The problem that started this

`mock-sensor` originally called `client.write()` once per reading,
straight from its sampling loop. A sensor configured for a 1 s interval
sampled noticeably slower than that, and the cause was the write call
itself blocking for about a second every time.

That was fixed in `18cb779` by putting `PointWriter` in front of the
client: `write()` only enqueues, and a background thread drains the queue
and flushes whatever accumulated in one call. `serial-sensor` inherits
the same arrangement unchanged.

## Cost of each layer

Following one reading through the stack, cheapest first.

| Layer | Cost per reading | Notes |
|---|---|---|
| `parse_reading()` | 2.8 µs | Pure string work |
| `PointWriter.write()` | 2.7 µs | A `queue.put`; never touches the network |
| `Point` construction | ~42 µs | Tags, field, timestamp, escaping |
| `raw_to_voltage()` + `Conversion.apply()` | 61 µs | pint `Quantity` arithmetic |
| `SerialRawSource.read()` | 81 µs | Dominated by pyserial's `readline()` |
| `logger.info()` per reading | 23 µs | Formatting plus the handler |
| **`client.write()` (one point)** | **~1,000,000 µs** | See below |

The last row is not a typo. Everything the acquisition loop does is in
the tens of microseconds; the InfluxDB call is a full second.

## Why the InfluxDB write costs a second

The obvious guesses — network round trip, payload size, serialization —
are all wrong. The call takes the same second regardless of how much it
carries:

| `write()` payload | Median call | Amortized per point |
|---|---|---|
| 1 point | 999.9 ms | 999.9 ms |
| 10 points | 999.3 ms | 99.9 ms |
| 100 points | 999.7 ms | 10.0 ms |
| 1000 points | 998.2 ms | 1.0 ms |

A fixed cost independent of payload points at a timer, not at work being
done, and it is InfluxDB 3's **WAL flush interval** (1 s by default). A
synchronous write returns once the data is durable in the write-ahead
log, so it waits for the next flush tick — which is why occasional calls
land much faster (a measured minimum of 53 ms) when they happen to
arrive just before a tick.

Confirming it: InfluxDB 3's v3 write API accepts a `no_sync` flag that
acknowledges the write without waiting for that flush.

| Write mode | Median call, 1 point |
|---|---|
| Default (waits for the WAL flush) | 1000.0 ms |
| `write_no_sync=True` (v3 API) | 1.2 ms |

Roughly an 800× difference, and it buys nothing here. `no_sync` trades
durability for latency — a server crash inside the flush window loses
whatever it acknowledged — and with `PointWriter` in front, the second
costs the sensor loop nothing at all. It is paid on a background thread
while the producer keeps sampling. The durable default stays.

This is also why the writer batches rather than writing point-by-point.
The per-call cost is fixed, so the same second serving 1000 points costs
1 ms each: batching is what turns a hard ceiling of one write per second
into a rate limited by nothing in particular.

## What the acquisition loop can actually sustain

Measured end to end — `serial-sensor`'s `run()` reading a virtual serial
port through real pyserial, with the writer thread running:

| Configuration | Per reading | Ceiling |
|---|---|---|
| INFO logging on (the default) | 366 µs | ~2,700 readings/s |
| Logging suppressed | 285 µs | ~3,500 readings/s |

Both are above the sum of the individual layers above, because the
producer, the writer thread, and the feeding process contend for the
GIL.

For scale, the reference sketch in
[`firmware/due_native_serial/`](../firmware/due_native_serial/due_native_serial.ino)
samples two channels once a second: **2 readings/s**. There are three
orders of magnitude of headroom, and nothing about the acquisition path
needs optimizing for a lab-rate instrument.

If a much faster board ever makes this matter, the profile says where to
look: pyserial's `readline()` and pint's `Quantity` arithmetic are the
two real costs, and per-reading `logger.info()` is a third of the budget
that could be demoted to `DEBUG` for free.

## The ceiling that arrives first

Before any of the above, timestamp resolution binds. Readings are
stamped by the host with `write_precision="ms"`, and InfluxDB treats
(measurement, tag set, timestamp) as a row's identity — two readings of
the same channel inside one millisecond are the same row, and the second
overwrites the first.

That caps **one channel at 1000 readings/s**, below the loop's own
~2,700/s. A faster channel than that needs microsecond precision, not a
faster loop.

## Where a backlog can still build up

The queue absorbs latency; it does not absorb a sustained outage. While
InfluxDB is unreachable, `PointWriter`'s thread sits in
`_write_with_retry` with a backoff capped at 30 s and stops draining, so
the queue grows at whatever rate the board is streaming.

`PointWriter`'s queue holds 10,000 points by default:

| Board rate | Outage the queue absorbs |
|---|---|
| 2 readings/s (reference sketch) | ~83 min |
| 10 readings/s | ~17 min |
| 50 readings/s | ~3 min |

Past that, `queue.put()` blocks, and with it the read loop. Backpressure
then propagates back down the serial link: a measurement against a
virtual serial port showed roughly 20 KB buffering in the tty layer
(~2,560 short lines) before the *sender* blocked in turn. Nothing is
corrupted or silently dropped, but a board whose firmware blocks in
`SerialUSB.print()` stops sampling on its own schedule until the host
catches up.

The difference from `mock-sensor` is worth stating plainly: a mock sensor
paces itself, so a stalled loop simply samples less often. A real board
sets its own pace and cannot be told to wait, so the consequence lands on
the firmware instead of on the script.

At the rates this project actually runs, the queue is oversized by orders
of magnitude and none of this is reachable. It matters only for a
high-rate board combined with a long outage.

## Reproducing the measurements

Environment: AMD Ryzen 3 5300U, Python 3.14.5, `influxdb:3-core`
(`sha256:2a50afa7…`) in Docker on the same host, so the network
contribution is negligible — a real LAN adds its own round trip on top.

- **Per-layer costs**: `timeit` around each function with a canned line
  (`b"A0,2048\n"`) and a `LinearConversion`, 20,000 iterations each.
- **Serial read**: a `pty` pair from the stdlib `pty` module, opened with
  pyserial at one end and fed lines from the other — the real
  `readline()` path, no hardware.
- **InfluxDB write**: 20 timed `client.write()` calls per payload size
  against a local container, reporting the median. Scratch tables were
  deleted afterwards.
- **End to end**: `serial_sensor.run()` against a pty with the InfluxDB
  client replaced by a counting stub, so the figure is the producer's
  ceiling rather than the server's.

The same `socat` setup described in
[`docs/serial-sensor.md`](serial-sensor.md#testing-without-hardware)
serves for ad-hoc checks without writing any benchmark code.
