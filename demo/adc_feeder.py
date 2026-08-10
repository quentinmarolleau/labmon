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
from typing import cast, override

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


class Channel:
    """One analog input, as a voltage the board would see.

    Each channel is a slow drift plus noise, shaped so a few minutes of
    data has something to look at rather than a flat line.
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
        self._phase: float = random.uniform(0, 2 * math.pi)

    def volts_at(self, elapsed: float) -> float:
        drift = self._swing * math.sin(
            2 * math.pi * elapsed / self._period + self._phase
        )
        return self._centre + drift + random.gauss(0, self._noise)


# Wired to the channels in demo/calibration.demo.toml. The voltages here
# are what the *sensor* puts out; what they mean is the calibration
# file's business, which is the whole point of the split.
CHANNELS = (
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
    # wander. The periods are deliberately incommensurate, so the beam
    # traces a slowly drifting loop rather than a straight line.
    Channel("A4", centre=1.65, swing=0.45, period_seconds=137, noise=0.008),
    Channel("A5", centre=1.65, swing=0.38, period_seconds=89, noise=0.008),
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
