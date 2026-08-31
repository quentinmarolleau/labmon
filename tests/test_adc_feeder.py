"""The demo's stand-in for an ADC board.

`demo/adc_feeder.py` is not part of the package and never runs in a real
lab, but it is what the demo stack acquires from: every reading anyone
sees on a first run of the quickstart came out of this file, through the
same parsing and calibration path a real board's would. A channel that
stops matching `demo/calibration.demo.toml`, or a voltage that leaves
the board's range, is a demo that shows wrong physics convincingly.
"""

import logging
import math
import socket
import threading
import time
import tomllib
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import cast

import pytest

from tests.loader import ADC_FEEDER, adc_feeder, path_to, run_as_main

_CALIBRATION = "demo/calibration.demo.toml"

feeder = adc_feeder()

# Bound once rather than suppressed at each of the five call sites, the
# way tests/test_influx.py takes `_setting`.
_counts = feeder._counts  # pyright: ignore[reportPrivateUsage]
_UtcMilliseconds = feeder._UtcMilliseconds  # pyright: ignore[reportPrivateUsage]


def _free_port() -> int:
    """A port the kernel has just confirmed is free.

    Racy in principle and not in practice: nothing else on the machine is
    handing out ports in the microseconds between the close and the bind,
    and the alternative — a fixed port — collides with a developer who
    happens to have the demo stack up.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return cast(tuple[str, int], probe.getsockname())[1]


@pytest.fixture
def _restored_level_names() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    """Undo the feeder's global rename of every logging level.

    `addLevelName` is process-wide and permanent, so without this the
    first test to run the main block leaves every later assertion on a
    level name reading `warning` where the stdlib says `WARNING`.
    """
    names = {value: logging.getLevelName(value) for value in (10, 20, 30, 40)}
    yield
    for value, name in names.items():
        logging.addLevelName(value, name)


class TestCounts:
    """Volts in, the count the board would report out."""

    def test_zero_volts_is_zero_counts(self) -> None:
        assert _counts(0.0) == 0.0

    def test_full_scale_is_the_top_code(self) -> None:
        """3.3 V reads 4095, the largest value 12 bits can hold."""
        assert _counts(feeder.VREF) == float(feeder.FULL_SCALE)
        assert feeder.FULL_SCALE == 4095

    def test_midscale_is_half_the_range(self) -> None:
        assert _counts(feeder.VREF / 2) == pytest.approx(2047.5, abs=0.01)

    @pytest.mark.parametrize(
        ("volts", "expected"), [(-1.0, 0.0), (99.0, 4095.0)], ids=["under", "over"]
    )
    def test_out_of_range_volts_clamp(self, volts: float, expected: float) -> None:
        """A real ADC saturates; it does not report a negative code.

        Without the clamp a noisy excursion past the rail would send a
        count the parser accepts and the calibration then maps to a
        temperature no thermometer has ever read.
        """
        assert _counts(volts) == expected

    def test_counts_carry_two_decimals(self) -> None:
        """The sketch averages a burst, so counts are fractional.

        Rounding to two places is what the firmware sends, and the
        parser on the other end is written against that.
        """
        rendered = f"{_counts(1.23456):.10f}".rstrip("0")
        assert len(rendered.split(".")[1]) <= 2


class TestSignals:
    """The two shapes a channel's voltage can take."""

    def test_channel_oscillates_around_its_centre(self) -> None:
        """A full period comes back to where it started, within noise."""
        channel = feeder.Channel(
            "T", centre=1.2, swing=0.5, period_seconds=100.0, noise=0.0
        )
        samples = [channel.volts_at(t) for t in range(100)]

        assert max(samples) == pytest.approx(1.2 + 0.5, abs=0.02)
        assert min(samples) == pytest.approx(1.2 - 0.5, abs=0.02)
        assert sum(samples) / len(samples) == pytest.approx(1.2, abs=0.02)

    def test_channels_do_not_all_peak_together(self) -> None:
        """The random phase is what stops six synchronised sine waves.

        Six channels rising and falling as one is the tell that a demo is
        simulated, and the panels are read side by side.
        """
        phases = {
            feeder.Channel("T", 1.0, 0.1, 60.0, 0.0)._phase  # pyright: ignore[reportPrivateUsage]
            for _ in range(20)
        }
        assert len(phases) == 20
        assert all(0 <= phase <= 2 * math.pi for phase in phases)

    def test_wander_is_pulled_back_towards_centre(self) -> None:
        """Displaced and left alone, it decays rather than walking off.

        This is the property that keeps the beam inside the calibration's
        linear range: a plain random walk has no stationary spread and
        leaves the ADC's range for good.
        """
        wander = feeder.Wander("Q", centre=1.65, noise=0.0, pull=0.05)
        wander._volts = 2.65  # pyright: ignore[reportPrivateUsage]

        after_one = wander.volts_at(0.0)
        assert after_one == pytest.approx(2.65 - 1.0 * 0.05)

        for _ in range(200):
            _ = wander.volts_at(0.0)
        assert wander._volts == pytest.approx(  # pyright: ignore[reportPrivateUsage]
            1.65, abs=0.01
        )

    def test_wander_ignores_the_clock(self) -> None:
        """Where it goes depends on where it has been, not on `elapsed`."""
        first = feeder.Wander("Q", centre=1.0, noise=0.0, pull=0.1)
        second = feeder.Wander("Q", centre=1.0, noise=0.0, pull=0.1)
        first._volts = 2.0  # pyright: ignore[reportPrivateUsage]
        second._volts = 2.0  # pyright: ignore[reportPrivateUsage]

        assert first.volts_at(0.0) == second.volts_at(99999.0)

    def test_wander_holds_a_stationary_spread(self) -> None:
        """The noise terms are solved backwards from the spread wanted.

        `noise / sqrt(2 * pull)` is the figure the channel comments quote
        when they justify 0.0572 and 0.0677; if the update rule changed,
        that arithmetic would stop describing this code.
        """
        wander = feeder.Wander("Q", centre=1.65, noise=0.0572, pull=0.05)
        for _ in range(2000):
            _ = wander.volts_at(0.0)
        samples = [wander.volts_at(0.0) for _ in range(20000)]

        mean = sum(samples) / len(samples)
        spread = math.sqrt(sum((value - mean) ** 2 for value in samples) / len(samples))
        predicted = 0.0572 / math.sqrt(2 * 0.05)
        assert spread == pytest.approx(predicted, rel=0.1)


class TestChannelWiring:
    """The feeder and the calibration file have to agree."""

    def test_every_channel_has_a_calibration(self) -> None:
        """A channel with no entry is acquired and then never converted.

        It reaches InfluxDB as raw counts under a name that looks like
        every other sensor, which is the failure worth catching here:
        the demo keeps working and starts lying.
        """
        with path_to(_CALIBRATION).open("rb") as handle:
            calibration = tomllib.load(handle)

        fed = {channel.name for channel in feeder.CHANNELS}
        calibrated = set(cast(dict[str, object], calibration["channels"]))
        assert fed == calibrated

    def test_every_channel_stays_inside_the_board_range(self) -> None:
        """Nothing should sit near a rail, where the clamp would flatten it.

        Checked over an hour of simulated time rather than at t=0, since
        the slow drift is what takes a channel towards a rail.
        """
        for channel in feeder.CHANNELS:
            samples = [channel.volts_at(float(t)) for t in range(0, 3600, 7)]
            assert min(samples) > 0.05, channel.name
            assert max(samples) < feeder.VREF - 0.05, channel.name


class TestServe:
    """One client at a time, streaming until it leaves."""

    @staticmethod
    def _serve_in_background(port: int) -> threading.Thread:
        """Run `serve` on a daemon thread, swallowing the teardown error.

        `serve` has no stop: it is a `while True` around `accept()`, which
        is right for a container whose lifetime is the demo's. The thread
        is therefore left blocked in `accept()` when the test ends, and
        dies with the interpreter.
        """

        def run() -> None:
            try:
                feeder.serve(host="127.0.0.1", port=port)
            except OSError:  # pragma: no cover - only on interpreter teardown
                pass

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread

    @staticmethod
    def _connect(port: int) -> socket.socket:
        """Connect once the background thread has actually bound the port.

        `serve` binds and listens on the thread, so connecting straight
        after `start()` races it and is refused perhaps one run in eight.
        Retrying is the fix rather than sleeping: a sleep long enough to
        be safe on a loaded runner is a sleep every run pays.
        """
        deadline = time.monotonic() + 10.0
        while True:
            try:
                return socket.create_connection(("127.0.0.1", port), timeout=10)
            except ConnectionRefusedError:
                if time.monotonic() >= deadline:  # pragma: no cover - wedged host
                    raise
                time.sleep(0.01)

    @staticmethod
    def _read_lines(client: socket.socket, wanted: int) -> list[str]:
        """Read until `wanted` complete CRLF-terminated lines have arrived."""
        buffer = b""
        while buffer.count(b"\r\n") < wanted:
            chunk = client.recv(4096)
            assert chunk, "the feeder closed the connection early"
            buffer += chunk
        return buffer.decode().split("\r\n")[:wanted]

    def test_streams_every_channel_in_the_firmware_format(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`<channel>,<count>\\r\\n`, one line per channel per tick.

        The format is the contract with `serial-sensor`'s parser, and the
        one-line-per-channel part is what makes a tick a complete set of
        readings rather than six that drift apart.
        """
        monkeypatch.setattr(feeder, "SAMPLE_INTERVAL_SECONDS", 0.01)
        port = _free_port()
        _ = self._serve_in_background(port)

        with self._connect(port) as client:
            lines = self._read_lines(client, len(feeder.CHANNELS))

        names = [line.split(",")[0] for line in lines]
        assert names == [channel.name for channel in feeder.CHANNELS]
        for line in lines:
            _, rendered = line.split(",")
            assert 0.0 <= float(rendered) <= float(feeder.FULL_SCALE)

    def test_a_client_leaving_is_not_a_crash(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """serial-sensor restarts; the feeder waits and takes it back.

        Without the handler the container exits on the first restart and
        the demo goes quiet, which reads as an acquisition fault rather
        than as the ordinary event it is.
        """
        monkeypatch.setattr(feeder, "SAMPLE_INTERVAL_SECONDS", 0.01)
        port = _free_port()
        _ = self._serve_in_background(port)

        with caplog.at_level(logging.INFO, logger="adc-feeder"):
            first = self._connect(port)
            _ = self._read_lines(first, len(feeder.CHANNELS))
            # Abort rather than close: a FIN is read as end-of-stream and
            # the next send succeeds into the void, while an RST is what
            # actually raises the error being checked here.
            first.setsockopt(
                socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00"
            )
            first.close()

            with self._connect(port) as second:
                lines = self._read_lines(second, len(feeder.CHANNELS))

        assert len(lines) == len(feeder.CHANNELS)
        messages = [record.getMessage() for record in caplog.records]
        assert any("client gone, waiting" in message for message in messages)
        assert sum("client connected" in message for message in messages) == 2


class TestLogFormat:
    """The feeder's lines have to sort against the sensors'."""

    def test_timestamps_are_utc_with_milliseconds(self) -> None:
        """Local time, or whole seconds, and a joint query reads wrong.

        The Logs dashboard interleaves this file's lines with the
        package's, and two clocks or two resolutions put them in an order
        neither process was in.
        """
        record = logging.LogRecord(
            "adc-feeder", logging.INFO, __file__, 1, "msg=x", None, None
        )
        record.created = 1_700_000_000.123456

        stamped = _UtcMilliseconds().formatTime(record)

        assert stamped == datetime.fromtimestamp(1_700_000_000.123456, UTC).isoformat(
            timespec="milliseconds"
        )
        assert stamped.endswith("+00:00")
        assert ".123" in stamped

    def test_main_lowercases_the_levels_and_exits_quietly(
        self, monkeypatch: pytest.MonkeyPatch, _restored_level_names: None
    ) -> None:
        """Ctrl-C is how the demo container stops, and it is not an error.

        `socket.socket` is what raises here because it is the first thing
        `serve` reaches for, which makes this the shortest path through
        the main block that does not open a port.

        The handler the block installs is not asserted on: `basicConfig`
        does nothing when the root logger already has handlers, and under
        pytest it always does. The rename is global and survives, so that
        is what is checked, and the formatter itself is covered directly
        by the test above.
        """

        def interrupted(*_args: object, **_kwargs: object) -> object:
            raise KeyboardInterrupt

        monkeypatch.setattr(socket, "socket", interrupted)
        assert logging.getLevelName(logging.WARNING) == "WARNING"

        with pytest.raises(SystemExit) as exited:
            _ = run_as_main(ADC_FEEDER)

        assert exited.value.code == 0
        assert logging.getLevelName(logging.WARNING) == "warning"
        assert logging.getLevelName(logging.DEBUG) == "debug"
