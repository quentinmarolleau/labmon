from labmon.sensors.mock_temperature import TemperatureWalk


def test_next_reverts_toward_setpoint_on_average() -> None:
    walk = TemperatureWalk(setpoint=20.0, noise=0.0)
    walk.value = 25.0

    _ = walk.next()

    assert walk.value < 25.0


def test_next_holds_steady_at_setpoint_with_no_noise() -> None:
    walk = TemperatureWalk(setpoint=20.0, noise=0.0)

    reading = walk.next()

    assert reading == 20.0
