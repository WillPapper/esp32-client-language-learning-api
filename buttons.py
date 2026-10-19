"""Polled buttons with tap and hold detection for the Zectrix NOTE4.

All three buttons pull their GPIO to ground when pressed (idle reads 1 with the pull-up).
Call poll() often (every 10 ms or so); it returns TAP once when a short press is released,
HOLD once when a press has lasted hold_ms, and None otherwise. A release after HOLD does not
also report a TAP.
"""
import time
from machine import Pin

PIN_MAIN = 0  # round front button (also BOOT)
PIN_UP = 39
PIN_DOWN = 18

TAP = "tap"
HOLD = "hold"


class Button:
    def __init__(self, pin, hold_ms=600, debounce_ms=30):
        self.pin = Pin(pin, Pin.IN, Pin.PULL_UP)
        self.hold_ms = hold_ms
        self.debounce_ms = debounce_ms
        self._down_at = None
        self._hold_sent = False

    def pressed(self):
        return self.pin() == 0

    def poll(self):
        now = time.ticks_ms()
        if self.pressed():
            if self._down_at is None:
                self._down_at = now
                self._hold_sent = False
            elif not self._hold_sent and time.ticks_diff(now, self._down_at) >= self.hold_ms:
                self._hold_sent = True
                return HOLD
            return None
        if self._down_at is None:
            return None
        held = time.ticks_diff(now, self._down_at)
        self._down_at = None
        if not self._hold_sent and held >= self.debounce_ms:
            return TAP
        return None

    def wait_release(self):
        while self.pressed():
            time.sleep_ms(10)
        self._down_at = None
