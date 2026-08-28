"""Stream plausible ADC counts over TCP, for the demo stack only.

`serial-sensor` opens this with a `socket://` URL instead of a device
path, so the demo exercises the real acquisition path — parsing,
calibration, `input_volts`, `calibration_id` — with no board attached.
Nothing here ships in the labmon package or runs in production; it
stands in for the firmware, not for any part of the host code.

The wire format is the one `firmware/due_native_serial` emits:

    <channel>,<raw_count>\r\n

Counts are fractional because the reference sketch averages a burst of
conversions per reading rather than sending one snapshot.

Stdlib only, so it runs in the labmon image as-is.
"""

import logging
import math
import os
import random
import socket
import sys
import time
from datetime import UTC, datetime
from typing import Protocol, cast, override

# Loopback by default, so running this directly on a workstation cannot
# expose the feeder to the network. The demo container overrides it with
# ADC_FEEDER_HOST=0.0.0.0, where binding every interface is the point:
# demo-serial-sensor connects from another container, and the port is
# deliberately never published to the host.
HOST = os.environ.get("ADC_FEEDER_HOST", "127.0.0.1")
PORT = 5555

# Matches the sketch's defaults, and serial-sensor's.
RESOLUTION_BITS = 12
FULL_SCALE = (1 << RESOLUTION_BITS) - 1
VREF = 3.3

SAMPLE_INTERVAL_SECONDS = 1.0

logger: logging.Logger = logging.getLogger("adc-feeder")


def _counts(volts: float) -> float:
    """Volts at the ADC input to the count the board would report."""
    clamped = min(max(volts, 0.0), VREF)
    return round(clamped / VREF * FULL_SCALE, 2)


class Signal(Protocol):
    """One analog input, as a voltage the board would see."""

    name: str

    def volts_at(self, elapsed: float, /) -> float:
        """The voltage for this tick.

        Called exactly once per channel per tick, which is what lets an
        implementation carry state from one call to the next.
        """
        ...


class Channel:
    """A slow periodic drift plus noise.

    Shaped so a few minutes of data has something to look at rather than
    a flat line, for a quantity that really does come back to where it
    was — a room warming and cooling, a chamber pumped down and vented.
    """

    def __init__(
        self,
        name: str,
        centre: float,
        swing: float,
        period_seconds: float,
        noise: float,
    ) -> None:
        self.name: str = name
        self._centre: float = centre
        self._swing: float = swing
        self._period: float = period_seconds
        self._noise: float = noise
        # Stagger the channels so they don't all peak together.
        # Jitter for a simulated sensor, nothing cryptographic.
        self._phase: float = random.uniform(0, 2 * math.pi)  # noqa: S311

    def volts_at(self, elapsed: float, /) -> float:
        drift = self._swing * math.sin(
            2 * math.pi * elapsed / self._period + self._phase
        )
        return self._centre + drift + random.gauss(0, self._noise)


class Wander:
    """A mean-reverting random walk, for a signal with no period to it.

    Each tick kicks the voltage by a Gaussian step and pulls it a little
    of the way back towards `centre`. Without the pull it is a plain
    random walk, which has no stationary spread and eventually leaves
    the ADC's range for good; with it, excursions decay over roughly
    `1 / pull` ticks and the walk keeps a stationary standard deviation
    of about `noise / sqrt(2 * pull)`.

    The same shape `labmon.sensors.mock_sensor.RandomWalk` uses, spelled
    out again because this file is stdlib-only and imports nothing from
    the package.

    `elapsed` is ignored: where the walk goes depends on where it has
    been, not on the clock.
    """

    def __init__(self, name: str, centre: float, noise: float, pull: float) -> None:
        self.name: str = name
        self._centre: float = centre
        self._noise: float = noise
        self._pull: float = pull
        self._volts: float = centre

    def volts_at(self, _elapsed: float, /) -> float:
        self._volts += (self._centre - self._volts) * self._pull
        self._volts += random.gauss(0, self._noise)
        return self._volts


# Wired to the channels in demo/calibration.demo.toml. The voltages here
# are what the *sensor* puts out; what they mean is the calibration
# file's business, which is the whole point of the split.
CHANNELS: tuple[Signal, ...] = (
    # A silicon diode on a cryostat cold finger: high voltage when cold.
    Channel("A0", centre=1.20, swing=0.06, period_seconds=420, noise=0.0015),
    # A bipolar supply rail monitor, scaled onto the board's 0-3.3V range.
    Channel("A1", centre=1.65, swing=0.35, period_seconds=180, noise=0.004),
    # A Pirani gauge pumping down and creeping back up: its log response
    # turns this small voltage swing into decades of pressure.
    Channel("A2", centre=2.00, swing=0.60, period_seconds=600, noise=0.003),
    # A photodiode watching a laser that drifts as the room warms.
    Channel("A3", centre=1.90, swing=0.25, period_seconds=300, noise=0.006),
    # The two difference signals of a quadrant photodiode watching a beam
    # wander. Random rather than periodic: a beam drifts with whatever
    # the mounts and the air are doing, and two sine waves would trace a
    # tidy Lissajous figure that no real detector has ever seen. The pull
    # stands for the alignment the beam was set up on, which it wanders
    # around without ever leaving.
    #
    # 1.65 V is mid-scale, which the calibration's -99 um offset puts at
    # the origin, so the pull drags the beam back towards zero position.
    #
    # The noise terms are solved backwards from the spread wanted rather
    # than picked: 0.183 V and 0.217 V stationary, which through
    # 60 um/V is 11 um in x and 13 um in y. The +/-40 um the calibration
    # calls linear is then 3.6 and 3.1 standard deviations out, so the
    # beam stays inside it except for the occasional brief excursion.
    #
    # A pull of 0.05 decays an excursion to 1/e in 20 readings, 20
    # seconds here. Weaker recall lets the walk sit far from centre for
    # minutes at a time, which is what pushes the tails past +/-40 um.
    Wander("A4", centre=1.65, noise=0.0572, pull=0.05),
    Wander("A5", centre=1.65, noise=0.0677, pull=0.05),
)


def serve(host: str = HOST, port: int = PORT) -> None:
    """Accept one client at a time and stream readings until it leaves."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((host, port))
    listener.listen(1)
    logger.info("msg=listening host=%s port=%d", host, port)

    started = time.monotonic()
    while True:
        # Indexed rather than unpacked. `accept()` returns the peer address
        # as `Any` in typeshed, because its shape depends on the address
        # family, and unpacking binds that `Any` to a name — which is what
        # a type checker objects to. Indexing keeps it unnamed until the
        # cast says what an AF_INET address actually is.
        accepted = listener.accept()
        client = accepted[0]
        peer, _port = cast(tuple[str, int], accepted[1])
        logger.info('msg="client connected" peer=%s', peer)
        try:
            with client:
                while True:
                    elapsed = time.monotonic() - started
                    for channel in CHANNELS:
                        count = _counts(channel.volts_at(elapsed))
                        client.sendall(f"{channel.name},{count}\r\n".encode())
                    time.sleep(SAMPLE_INTERVAL_SECONDS)
        except (BrokenPipeError, ConnectionResetError):
            # serial-sensor restarted; wait for it to come back.
            logger.info('msg="client gone, waiting"')


class _UtcMilliseconds(logging.Formatter):
    """Stamps a record the way labmon.logs.LogfmtFormatter does.

    `datefmt` cannot express it: %(asctime)s is local time and has no
    sub-second field, so the feeder's lines would sort against the
    sensors' by a different clock and a coarser one — in a query that
    reads both, which is the whole point of collecting them together.
    """

    @override
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        stamped = datetime.fromtimestamp(record.created, UTC)
        return stamped.isoformat(timespec="milliseconds")


if __name__ == "__main__":
    # The same logfmt shape the sensors emit, spelled out rather than
    # imported: this file is stdlib-only so it can run in the labmon image
    # without being part of the package.
    # Lower-case level names, matching what labmon.logs emits so both
    # halves of the demo read the same way in Grafana.
    for level in (logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR):
        logging.addLevelName(level, logging.getLevelName(level).lower())
    handler = logging.StreamHandler()
    handler.setFormatter(
        _UtcMilliseconds(
            "ts=%(asctime)s level=%(levelname)s logger=%(name)s %(message)s"
        )
    )
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    try:
        serve()
    except KeyboardInterrupt:
        sys.exit(0)
