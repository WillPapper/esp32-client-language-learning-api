#!/usr/bin/env python3
"""Print the NOTE4's serial output without interrupting the running app.

    uv run --with pyserial python tools/log.py            # until Ctrl-C
    uv run --with pyserial python tools/log.py 20         # for 20 seconds
    PORT=/dev/cu.usbmodemXXXX uv run --with pyserial python tools/log.py

`mpremote repl` works too in a terminal, but every mpremote `exec`, `run`, `cp` or `reset`
sends Ctrl-C first, which stops main.py until the next reset; and `repl` needs a TTY, so it
fails from scripts and coding agents. This only reads. Output starts with whatever the app
prints after you start it (the boot log is gone by then; `tools/deploy.sh` resets the device,
so start this right after it to catch the boot).
"""
import os
import sys
import time

import serial

PORT = os.environ.get("PORT", "/dev/cu.usbmodem2101")


def _open():
    for _ in range(50):  # the port disappears for a few seconds after a reset
        try:
            return serial.Serial(PORT, 115200, timeout=0.2)
        except (OSError, serial.SerialException):
            time.sleep(0.2)
    sys.exit("no device at " + PORT)


def main(argv):
    seconds = float(argv[1]) if len(argv) > 1 else None
    port = _open()
    end = time.time() + seconds if seconds else None
    try:
        while end is None or time.time() < end:
            try:
                data = port.read(4096)
            except (OSError, serial.SerialException):
                # The device re-enumerated (a reset right after we opened it): reopen.
                port.close()
                port = _open()
                continue
            if data:
                sys.stdout.write(data.decode("utf-8", "replace"))
                sys.stdout.flush()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main(sys.argv)
