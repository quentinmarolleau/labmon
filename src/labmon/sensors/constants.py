"""Defaults the command line needs to name without loading a sensor.

An option default is evaluated when the command module is imported, so
reaching these through the modules that own them would make every
`labmon --help` and every tab completion import the InfluxDB client,
pyserial and pint. They are plain numbers; the modules that use them
re-export them so nothing else has to know they moved.
"""

# Seconds between "still writing" summary lines. Long enough that an
# idle sensor is not chatty, short enough that silence means something.
DEFAULT_SUMMARY_INTERVAL_SECONDS = 30.0

# Digits a simulated reading carries when no absolute step is given.
# Significant rather than decimal places because a mock sensor may sit
# anywhere on the scale.
DEFAULT_SIGNIFICANT_DIGITS = 6

# Serial baud rate. Ignored by a board on native USB, which runs at full
# USB speed, but pyserial still requires a value.
DEFAULT_BAUDRATE = 115200
