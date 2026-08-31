"""The two files docs/custom-sensor.md tells people to copy.

They are the only code here that is meant to be edited rather than run
as shipped, which makes their defaults load-bearing: a template that
polls the wrong function, or exits non-zero when the instrument simply
had nothing to say, is a bug reproduced by everyone who follows the
page. Both are checked as shipped — the `read_value` stub included,
since it is what makes the template run before any vendor code exists.
"""

import logging
from collections.abc import Callable
from typing import cast

import pytest

from tests.loader import (
    SENSOR_CONTINUOUS,
    SENSOR_TRIGGERED,
    run_as_main,
    sensor_template,
)


class _Recorder:
    """Stands in for `poll` or `write_reading`, keeping what it was sent."""

    def __init__(self) -> None:
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def __call__(self, *args: object, **kwargs: object) -> None:
        self.calls.append((args, kwargs))

    @property
    def kwargs(self) -> dict[str, object]:
        """The keyword arguments of the single call made."""
        assert len(self.calls) == 1, f"wanted one call, got {len(self.calls)}"
        return self.calls[0][1]


def _returning(value: float | None) -> Callable[..., float | None]:
    """A stand-in for `random.normalvariate` with a decided answer."""

    def answer(*_args: object, **_kwargs: object) -> float | None:
        return value

    return answer


@pytest.fixture
def _quiet_logging(  # pyright: ignore[reportUnusedFunction]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stop `logs.configure()` reconfiguring the suite's own logging.

    Both templates call it at the top of their main block, and it
    installs a root handler; left alone, the first template to run
    changes how every later test's output is formatted.
    """

    def configured(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr("labmon.logs.configure", configured)


@pytest.mark.parametrize(
    "relative", [SENSOR_CONTINUOUS, SENSOR_TRIGGERED], ids=["continuous", "triggered"]
)
def test_read_value_stub_returns_a_reading(relative: str) -> None:
    """The shipped stub answers, so the template runs before any SDK does.

    That is the property the docstring promises and the reason to start
    the container before touching vendor code: if readings appear in
    Grafana, the plumbing is right and anything that breaks afterwards
    is the instrument.
    """
    read_value = sensor_template(relative).read_value
    values = [read_value() for _ in range(50)]

    assert all(isinstance(value, float) for value in values)
    # Simulated around 21 degC with a 0.1 spread; a hundredth of that
    # either way is far outside anything the generator produces, so this
    # catches a stub rewired to return counts or millivolts.
    assert all(value is not None and 20.0 < value < 22.0 for value in values)
    assert len(set(values)) > 1, "a constant stub would hide a dead poll loop"


def test_continuous_polls_with_the_identifiers_it_documents(
    monkeypatch: pytest.MonkeyPatch, _quiet_logging: None
) -> None:
    """The main block hands `poll` the stub and the placeholder id.

    `CHANGE-ME` is deliberate and checked: the template is wrong if it
    ships an id that looks like a real sensor, because the copy that is
    never edited then writes under a plausible name.
    """
    recorder = _Recorder()
    monkeypatch.setattr("labmon.sensors.polling.poll", recorder)

    namespace = run_as_main(SENSOR_CONTINUOUS)

    positional, kwargs = recorder.calls[0]
    assert positional == (namespace["read_value"],)
    assert kwargs == {
        "sensor_id": "CHANGE-ME",
        "measurement": "temperature",
        "unit": "degC",
        "interval": 5.0,
    }


def test_triggered_writes_one_reading_and_stops(
    monkeypatch: pytest.MonkeyPatch, _quiet_logging: None
) -> None:
    """One reading, written synchronously, with no loop around it."""
    recorder = _Recorder()
    monkeypatch.setattr("labmon.sensors.polling.write_reading", recorder)
    monkeypatch.setattr("random.normalvariate", _returning(20.5))

    _ = run_as_main(SENSOR_TRIGGERED)

    positional, kwargs = recorder.calls[0]
    assert positional == (20.5,)
    assert kwargs == {
        "sensor_id": "CHANGE-ME",
        "measurement": "temperature",
        "unit": "degC",
    }


def test_triggered_exits_zero_when_there_is_no_reading(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    _quiet_logging: None,
) -> None:
    """No reading is not a failure, and a timer must not be told it is.

    systemd reports a unit that exits non-zero as failed, so returning 1
    here would turn an instrument that was merely warming up into a red
    unit somebody has to go and clear.
    """
    recorder = _Recorder()
    monkeypatch.setattr("labmon.sensors.polling.write_reading", recorder)
    monkeypatch.setattr("random.normalvariate", _returning(None))

    with caplog.at_level(logging.INFO), pytest.raises(SystemExit) as exited:
        _ = run_as_main(SENSOR_TRIGGERED)

    assert exited.value.code == 0
    assert recorder.calls == []
    record = caplog.records[-1]
    assert record.message == "no reading available; nothing written"
    # The identifier travels in `extra=`, not inside the message, which is
    # what lets a Loki query filter on it.
    assert cast(str, record.sensor_id) == "CHANGE-ME"  # pyright: ignore[reportAttributeAccessIssue]
