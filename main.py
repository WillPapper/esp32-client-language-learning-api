# Runs at boot on the NOTE4. Ctrl-C over USB (mpremote) drops to the REPL.
import sys
from machine import Pin

# Hold the battery latch high first: after a power-off (GPIO 17 low) the unit is powered only
# while the down button (GPIO 18, the hardware power button) is held, until this line runs.
# MicroPython gets here about a second after power is applied, so "hold down for a second"
# turns the unit on. The front button cannot do this: it is the BOOT strapping pin (GPIO 0),
# and holding it while the chip powers up enters the ROM download mode instead of booting.
Pin(17, Pin.OUT, value=1, hold=True)

import app

try:
    app.main()
except KeyboardInterrupt:
    print("stopped")
except Exception as e:
    sys.print_exception(e)
    raise
