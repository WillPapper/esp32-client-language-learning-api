"""MicroPython driver for the Zectrix NOTE4 4.2" 400x300 black/white e-paper.

Ported from Zectrix's board package in xiaozhi-esp32: EPD_Init() and EPD_Display() in
../reference/xiaozhi-esp32/main/boards/zectrix/zectrix-s3-epaper-4.2/custom_lcd_display.cc.
Only the full refresh is implemented. Draw with the FrameBuffer methods, then call show().
"""
import framebuf
import time
from machine import SPI, Pin

WIDTH = 400
HEIGHT = 300
ROW_BYTES = WIDTH // 8

# GPIOs from config.h in the same board folder
PIN_SCK = 12
PIN_MOSI = 13
PIN_CS = 11
PIN_DC = 10
PIN_RST = 9
PIN_BUSY = 8  # 0 = busy
PIN_EPD_PWR = 6
PIN_VBAT_PWR = 17
PIN_POWER_GOOD = 18

BLACK = 0
WHITE = 1


def _make_lut():
    # The panel takes 2 bits per pixel, with the pixel in the low bit of each
    # pair, so each 1bpp byte becomes two bytes (pack_1bpp_to_2683 in the stock driver).
    lut = bytearray(512)
    for b in range(256):
        v = 0
        for i in range(8):
            v |= ((b >> (7 - i)) & 1) << (14 - 2 * i)
        lut[2 * b] = v >> 8
        lut[2 * b + 1] = v & 0xFF
    return lut


class EPD(framebuf.FrameBuffer):
    def __init__(self):
        self.buf = bytearray(ROW_BYTES * HEIGHT)
        super().__init__(self.buf, WIDTH, HEIGHT, framebuf.MONO_HLSB)
        self.fill(WHITE)
        self._lut = _make_lut()
        self._row = bytearray(ROW_BYTES * 2)

        # Power-up order from Power_Init() in zectrix-s3-epaper-4.2.cc. Like the stock firmware,
        # set both rails high and pad-hold them, so they stay on through resets (the stock driver
        # notes that cutting display power after a refresh blanks the screen). hold= must be passed:
        # a hold left by earlier firmware survives resets and silently ignores plain writes.
        Pin(PIN_VBAT_PWR, Pin.OUT, value=1, hold=True)
        Pin(PIN_EPD_PWR, Pin.OUT, value=1, hold=True)
        power_good = Pin(PIN_POWER_GOOD, Pin.IN)
        start = time.ticks_ms()
        while not power_good() and time.ticks_diff(time.ticks_ms(), start) < 1000:
            time.sleep_ms(10)

        self.cs = Pin(PIN_CS, Pin.OUT, value=1, hold=False)
        self.dc = Pin(PIN_DC, Pin.OUT, value=1, hold=False)
        self.rst = Pin(PIN_RST, Pin.OUT, value=1, hold=False)
        self.busy = Pin(PIN_BUSY, Pin.IN, Pin.PULL_UP)
        self._spi_on()
        self._init_panel()

    def _spi_on(self):
        # miso=None matters: SPI(2)'s default MISO on the ESP32-S3 is GPIO 37, an octal PSRAM pin.
        self.spi = SPI(2, baudrate=20_000_000, polarity=0, phase=0,
                       sck=Pin(PIN_SCK), mosi=Pin(PIN_MOSI), miso=None)

    def _data(self, data):
        self.dc(1)
        self.cs(0)
        self.spi.write(data)
        self.cs(1)

    def _command(self, cmd, params=b""):
        self.dc(0)
        self.cs(0)
        self.spi.write(bytes((cmd,)))
        self.cs(1)
        for p in params:
            self._data(bytes((p,)))

    def _wait_idle(self, timeout_ms=20_000):
        start = time.ticks_ms()
        while self.busy() == 0:
            if time.ticks_diff(time.ticks_ms(), start) > timeout_ms:
                raise OSError("e-paper BUSY stuck low")
            time.sleep_ms(5)

    def _init_panel(self):
        time.sleep_ms(10)
        self.rst(1)
        time.sleep_ms(10)
        self.rst(0)
        time.sleep_ms(20)
        self.rst(1)
        time.sleep_ms(10)
        self._wait_idle()
        self._command(0x00, b"\x2f\x2e")  # panel setting
        self._command(0xE9, b"\x01")
        self._wait_idle()

    def read_temperature(self):
        """Raw panel temperature byte (reads 34 on a desk; units unconfirmed)."""
        self._command(0x40)
        self._wait_idle()
        # The controller replies on the MOSI line (3-wire SPI), so clock the byte in by hand.
        self.spi.deinit()
        sck = Pin(PIN_SCK, Pin.OUT, value=0)
        sda = Pin(PIN_MOSI, Pin.IN)
        self.dc(1)
        self.cs(0)
        value = 0
        for _ in range(8):
            sck(1)
            value = (value << 1) | sda()
            sck(0)
        self.cs(1)
        self._spi_on()
        return value

    def show(self):
        """Full refresh of the whole screen (about 1.2 s). Returns the signed temperature reading."""
        raw = self.read_temperature()
        # Temperature-compensation setting, same table as the stock driver.
        if raw <= 5:
            tsset = 232
        elif raw <= 10:
            tsset = 235
        elif raw <= 20:
            tsset = 238
        elif raw <= 30:
            tsset = 241
        elif raw <= 127:
            tsset = 244
        else:
            tsset = 232
        self._command(0xE0, b"\x02")
        self._command(0xE6, bytes((tsset,)))
        self._command(0xA5)
        self._wait_idle()
        time.sleep_ms(10)

        self._command(0x10)
        lut, row, buf = self._lut, self._row, self.buf
        for y in range(HEIGHT):
            base = y * ROW_BYTES
            for x in range(ROW_BYTES):
                i = buf[base + x] * 2
                row[2 * x] = lut[i]
                row[2 * x + 1] = lut[i + 1]
            self._data(row)

        self._command(0x04)  # power on
        self._wait_idle()
        self._command(0x12, b"\x00")  # refresh
        self._wait_idle()
        self._command(0x02, b"\x00")  # power off (panel boost only; GPIO 6 stays on)
        self._wait_idle()
        return raw - 256 if raw > 127 else raw

    def big_text(self, s, x, y, scale=2, c=BLACK):
        """Like text(), but scaled up by an integer factor (the built-in font is 8x8)."""
        w = len(s) * 8
        glyphs = framebuf.FrameBuffer(bytearray(w), w, 8, framebuf.MONO_VLSB)
        glyphs.text(s, 0, 0, 1)
        for py in range(8):
            for px in range(w):
                if glyphs.pixel(px, py):
                    self.fill_rect(x + px * scale, y + py * scale, scale, scale, c)
