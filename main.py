# Runs at boot on the NOTE4. Ctrl-C over USB (mpremote) drops to the REPL.
import sys
from machine import Pin

# Hold the battery latch high first: after a power-off (GPIO 17 low) the unit runs from the
# buttons only until this line, so pressing up + down together briefly is enough to turn it on.
Pin(17, Pin.OUT, value=1, hold=True)

import app

try:
    app.main()
except KeyboardInterrupt:
    print("stopped")
except Exception as e:
    sys.print_exception(e)
    raise
