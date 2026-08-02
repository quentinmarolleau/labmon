"""Read an instrument on a fixed interval and write every reading.

Copy this file, replace `read_value()` with a call to your instrument's
API, and set the identifiers at the bottom. Everything else — batching,
retries, shutdown, logging — is handled by `labmon.sensors.polling.poll`.

As shipped, `read_value()` returns a simulated value, so the template runs
before you have touched any vendor code. Start it, confirm the readings
appear in Grafana, and only then swap in the real instrument: if something
goes wrong afterwards, you know it is the instrument and not the plumbing.

Use this file when the instrument can be read as often as you like. If its
API is rate-limited, billed per call, or takes minutes to answer, use
`sensor_triggered.py` instead.
"""

import logging
import random

from labmon.sensors.polling import poll

# --------------------------------------------------------------------------
# Replace everything in this block with your instrument.
# --------------------------------------------------------------------------

# import vendor_sdk
# device = vendor_sdk.Device("192.0.2.10")


def read_value() -> float | None:
    """Return one reading in physical units, or None if there isn't one.

    Returning None skips this tick without logging an error — the right
    answer for an instrument that is still warming up, or that has nothing
    new since the last call.

    Raising is also fine, and is the expected way to report a failure: the
    exception is logged with its traceback and the read is retried with
    backoff. It will not stop the process. Do not catch and swallow errors
    here to "keep things running" — that is already handled, and swallowing
    them turns a broken instrument into a flat line nobody investigates.
    """
    # return device.read_temperature()
    return random.normalvariate(21.0, 0.1)


# --------------------------------------------------------------------------

if __name__ == "__main__":
    # INFO so the periodic "still writing" summary is visible; the
    # per-reading line is DEBUG.
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    poll(
        read_value,
        sensor_id="CHANGE-ME",
        measurement="temperature",
        unit="degC",
        interval=5.0,
    )
