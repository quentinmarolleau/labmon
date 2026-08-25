"""What an ADC reading is worth, before anything physical is involved.

These live apart from `labmon.calibration` because they are hardware
facts rather than calibration logic, and because the command line needs
them as option defaults — which are evaluated at import. Reaching them
through `calibration` would make every `labmon --help` and every tab
completion pay for `pint`, which is a tenth of a second to load and has
nothing to do with naming a default.
"""

# Bits in one conversion. Twelve suits the parts these sensors are
# usually built around; a 10-bit or 16-bit board passes its own.
ADC_RESOLUTION_BITS = 12

# Full-scale reference, in volts. 3.3 V is the common rail; a 5 V part
# passes its own.
ADC_VREF_VOLTS = 3.3
