"""Read an instrument once, write the reading, and exit.

For an API that is rate-limited, billed per call, or slow enough that a
process sleeping between calls is the wrong shape. Something external
decides when a reading happens — a systemd timer, cron, or a run triggered
by hand.

Copy this file, replace `read_value()`, and drive it with
`labmon-sensor-triggered.timer` (see this directory's README) or:

    docker compose run --rm my-sensor

`write_reading` blocks for about a second while InfluxDB flushes its
write-ahead log, and that is deliberate: the reading is stored by the time
this process exits. Handing the point to a background thread would be
faster and would lose it, because the interpreter exits before the thread
gets to send anything.
"""

import logging
import random
import sys

from labmon import logs
from labmon.sensors.polling import write_reading

# --------------------------------------------------------------------------
# Replace everything in this block with your instrument.
# --------------------------------------------------------------------------

# import vendor_sdk


def read_value() -> float | None:
    """Return one reading in physical units, or None if there isn't one."""
    # with vendor_sdk.Device("192.0.2.10") as device:
    #     return device.read_temperature()
    return random.normalvariate(21.0, 0.1)


# --------------------------------------------------------------------------

# The instrument this script speaks for. Named once, so the log line
# below and the reading itself cannot drift apart.
SENSOR_ID = "CHANGE-ME"


if __name__ == "__main__":
    # labmon's own logfmt setup, not a hand-rolled format. Every query
    # in docs/logging.md and every panel on the Logs dashboard parses
    # `| logfmt` and filters on named fields; a sensor that emits prose
    # is collected into Loki and then matches none of them, which reads
    # as an instrument with nothing to say.
    logs.configure()

    value = read_value()
    if value is None:
        # Nothing to record this time. Exit 0: a timer treats a non-zero
        # status as a failure and will report the unit as failed, which is
        # wrong for "the instrument had nothing to say".
        #
        # A constant message with the data in `extra=`, the shape the rest
        # of labmon uses: the message groups every occurrence together and
        # the fields are what a query filters on.
        logging.getLogger(__name__).info(
            "no reading available; nothing written",
            extra={"sensor_id": SENSOR_ID},
        )
        sys.exit(0)

    write_reading(
        value,
        sensor_id=SENSOR_ID,
        measurement="temperature",
        unit="degC",
    )
