"""Just enough of MicroPython's framebuf (MONO_HLSB only) to render ui.py on a Mac.

Used by tools/preview.py; not for the device, which has the real module built in.
"""
MONO_HLSB = 3


class FrameBuffer:
    def __init__(self, buf, width, height, fmt, stride=None):
        assert fmt == MONO_HLSB
        self.buf = buf
        self.width = width
        self.height = height
        self.stride = stride or width
        self._rb = (self.stride + 7) // 8

    def pixel(self, x, y, c=None):
        if not (0 <= x < self.width and 0 <= y < self.height):
            return None
        i = y * self._rb + (x >> 3)
        m = 0x80 >> (x & 7)
        if c is None:
            return 1 if self.buf[i] & m else 0
        if c:
            self.buf[i] |= m
        else:
            self.buf[i] &= ~m & 0xFF

    def fill(self, c):
        self.buf[:] = bytes([0xFF if c else 0]) * len(self.buf)

    def fill_rect(self, x, y, w, h, c):
        for yy in range(max(0, y), min(self.height, y + h)):
            for xx in range(max(0, x), min(self.width, x + w)):
                self.pixel(xx, yy, c)

    def hline(self, x, y, w, c):
        self.fill_rect(x, y, w, 1, c)

    def vline(self, x, y, h, c):
        self.fill_rect(x, y, 1, h, c)

    def rect(self, x, y, w, h, c, f=False):
        if f:
            self.fill_rect(x, y, w, h, c)
        else:
            self.hline(x, y, w, c)
            self.hline(x, y + h - 1, w, c)
            self.vline(x, y, h, c)
            self.vline(x + w - 1, y, h, c)

    def blit(self, src, x, y, key=-1, palette=None):
        for sy in range(src.height):
            for sx in range(src.width):
                col = src.pixel(sx, sy)
                if palette is not None:
                    col = palette.pixel(col, 0)
                if col != key:
                    self.pixel(x + sx, y + sy, col)

    def to_image(self):
        from PIL import Image
        img = Image.new("1", (self.width, self.height))
        for yy in range(self.height):
            for xx in range(self.width):
                img.putpixel((xx, yy), self.pixel(xx, yy))
        return img
