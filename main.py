# Runs at boot on the NOTE4. Ctrl-C over USB (mpremote) drops to the REPL.
import sys

import app

try:
    app.main()
except KeyboardInterrupt:
    print("stopped")
except Exception as e:
    sys.print_exception(e)
    raise
