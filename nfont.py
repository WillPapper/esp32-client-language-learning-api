"""Bitmap fonts for the e-paper: reads the NF1 files made by tools/build_font.py and draws text.

A Font keeps the code-point index and advance widths in RAM and reads glyph bitmaps from
flash on demand, caching recent ones. Glyphs are MONO_HLSB FrameBuffers with 1 = ink, and
are blitted through a palette so they can be drawn black on white or white on black.
"""
import framebuf
import struct

MISSING = 0x25A1  # □ is drawn for characters the font lacks

# Characters that must not start a line (Japanese kinsoku and the Chinese equivalents).
_NO_LINE_START = "、。，．,.:;!?)]}」』】〕〉》）］｝！？：；…ー々ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮヵヶ"
_NO_LINE_END = "（「『【〔〈《([{"


def _is_cjk(cp):
    return cp >= 0x2E80 and not (0xFF61 <= cp <= 0xFF9F)


class Font:
    def __init__(self, path, cache=400):
        self.path = path
        self.f = open(path, "rb")
        hdr = self.f.read(12)
        if hdr[:4] != b"NF1\0":
            raise ValueError("not an NF1 font: " + path)
        self.cell_w = hdr[4]
        self.cell_h = hdr[5]
        self.baseline = hdr[6]
        self.height = self.cell_h
        self.count = struct.unpack_from("<I", hdr, 8)[0]
        self._index = self.f.read(2 * self.count)
        self._widths = self.f.read(self.count)
        self._bitmaps = 12 + 3 * self.count
        self._cell_bytes = ((self.cell_w + 7) // 8) * self.cell_h
        self._cache = {}
        self._cache_max = cache
        self._missing = self._find(MISSING)

    def _find(self, cp):
        idx = self._index
        lo, hi = 0, self.count - 1
        while lo <= hi:
            mid = (lo + hi) >> 1
            v = idx[2 * mid] | (idx[2 * mid + 1] << 8)
            if v == cp:
                return mid
            if v < cp:
                lo = mid + 1
            else:
                hi = mid - 1
        return -1

    def has(self, ch):
        return self._find(ord(ch)) >= 0

    def glyph(self, ch):
        """(FrameBuffer, advance) for one character, or None if neither it nor □ exists."""
        g = self._cache.get(ch)
        if g is not None:
            return g
        i = self._find(ord(ch))
        if i < 0:
            i = self._missing
            if i < 0:
                return None
        buf = bytearray(self._cell_bytes)
        self.f.seek(self._bitmaps + i * self._cell_bytes)
        self.f.readinto(buf)
        g = (framebuf.FrameBuffer(buf, self.cell_w, self.cell_h, framebuf.MONO_HLSB), self._widths[i])
        if len(self._cache) >= self._cache_max:
            self._cache.clear()
        self._cache[ch] = g
        return g

    def advance(self, ch):
        i = self._find(ord(ch))
        if i < 0:
            i = self._missing
        return self._widths[i] if i >= 0 else 0

    def width(self, s):
        w = 0
        for ch in s:
            w += self.advance(ch)
        return w


def _palette(ink):
    # Maps glyph value 0 (background) to the opposite of ink and 1 to ink; the background
    # value is then passed as the blit key so it is transparent.
    pal = framebuf.FrameBuffer(bytearray(1), 2, 1, framebuf.MONO_HLSB)
    pal.pixel(0, 0, 1 - ink)
    pal.pixel(1, 0, ink)
    return pal


_PALETTES = {0: _palette(0), 1: _palette(1)}


def draw(fb, font, s, x, y, ink=0):
    """Draw s with its cell top at y. ink=0 is black on the e-paper, 1 is white. Returns the end x."""
    pal = _PALETTES[ink]
    key = 1 - ink
    for ch in s:
        g = font.glyph(ch)
        if g is None:
            continue
        fb.blit(g[0], x, y, key, pal)
        x += g[1]
    return x


def draw_centered(fb, font, s, y, ink=0, x0=0, w=None):
    if w is None:
        w = fb.width if hasattr(fb, "width") else 400
    return draw(fb, font, s, x0 + (w - font.width(s)) // 2, y, ink)


def draw_right(fb, font, s, right, y, ink=0):
    return draw(fb, font, s, right - font.width(s), y, ink)


def _tokens(s):
    """Split into unbreakable chunks: CJK characters one by one (with trailing punctuation
    attached), Latin words with their following space."""
    out = []
    cur = ""
    for ch in s:
        cp = ord(ch)
        if _is_cjk(cp):
            if cur:
                out.append(cur)
                cur = ""
            if out and (ch in _NO_LINE_START or (out[-1] and out[-1][-1] in _NO_LINE_END)):
                out[-1] += ch
            else:
                out.append(ch)
        elif ch == " ":
            cur += ch
            out.append(cur)
            cur = ""
        elif ch == "\n":
            if cur:
                out.append(cur)
                cur = ""
            out.append("\n")
        else:
            if out and not cur and ch in _NO_LINE_START and not out[-1].endswith(" "):
                out[-1] += ch
            else:
                cur += ch
    if cur:
        out.append(cur)
    return out


def wrap(font, s, max_w):
    """Greedy word wrap. Returns a list of lines (trailing spaces stripped)."""
    lines = []
    line = ""
    line_w = 0
    for tok in _tokens(s):
        if tok == "\n":
            lines.append(line.rstrip())
            line, line_w = "", 0
            continue
        tw = font.width(tok)
        if line and line_w + font.width(tok.rstrip()) > max_w:
            lines.append(line.rstrip())
            line, line_w = "", 0
        if tw > max_w and not line:
            # A single chunk wider than the line: hard-break it by character.
            for ch in tok:
                cw = font.advance(ch)
                if line and line_w + cw > max_w:
                    lines.append(line.rstrip())
                    line, line_w = "", 0
                line += ch
                line_w += cw
            continue
        line += tok
        line_w += tw
    if line.rstrip():
        lines.append(line.rstrip())
    return lines


def fit(font, s, max_w):
    """Trim s with an ellipsis so it fits in max_w."""
    if font.width(s) <= max_w:
        return s
    ell = font.width("…")
    out = ""
    w = 0
    for ch in s:
        cw = font.advance(ch)
        if w + cw + ell > max_w:
            break
        out += ch
        w += cw
    return out + "…"
