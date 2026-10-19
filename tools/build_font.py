#!/usr/bin/env python3
"""Render Noto Sans CJK into the NF1 bitmap format read by nfont.py on the device.

Run on the Mac (not on the ESP32):

    uv run --with pillow python tools/build_font.py --lang ja --size 40 --out fonts/ja40.nf1
    uv run --with pillow python tools/build_font.py --lang zh --size 22 --out fonts/zh22.nf1

Source fonts: Noto Sans CJK JP and Noto Sans CJK SC (SIL Open Font License 1.1, see
fonts/LICENSE-NotoSansCJK.txt), downloaded from https://github.com/notofonts/noto-cjk into
../reference/noto-cjk/. Each language uses its own font so kanji and hanzi keep their
regional forms. The character set is everything Shift_JIS (ja) or GB2312 (zh) can encode,
plus Latin, pinyin tone letters, punctuation, kana and a few symbols for the UI, so any word
the API returns should render.

NF1 layout (little endian):
    0   4   magic  b"NF1\\0"
    4   1   cell_w       width of every glyph bitmap in pixels
    5   1   cell_h       height of every glyph bitmap in pixels
    6   1   baseline     rows from the top of the cell to the text baseline
    7   1   flags        0
    8   4   count        number of glyphs
    12  2*count  code points (u16, sorted ascending)
    ..  count    advance width per glyph (u8)
    ..  count * cell_bytes  bitmaps, MONO_HLSB (bit 7 = leftmost pixel), 1 = ink,
                            cell_bytes = ceil(cell_w / 8) * cell_h
"""
import argparse
import os
import struct
import sys

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.normpath(os.path.join(HERE, "..", "..", "reference", "noto-cjk"))
FONTS = {
    "ja": os.path.join(FONT_DIR, "NotoSansCJKjp-Regular.otf"),
    "zh": os.path.join(FONT_DIR, "NotoSansCJKsc-Regular.otf"),
}

# Ranges every font gets, whatever the language.
COMMON_RANGES = [
    (0x0020, 0x007E),  # ASCII
    (0x00A0, 0x024F),  # Latin-1, Latin Extended-A/B (pinyin tone letters live here too)
    (0x1E00, 0x1EFF),  # Latin Extended Additional (ḿ, ǹ…)
    (0x2010, 0x2027),  # dashes, quotes, ellipsis
    (0x2030, 0x203A),  # per mille, primes, guillemets
    (0x2190, 0x2193),  # arrows
    (0x25A0, 0x25A1),  # squares (U+25A1 is the missing-glyph box)
    (0x25B2, 0x25B3),  # ▲ △
    (0x25B6, 0x25B7),  # ▶ ▷
    (0x25BC, 0x25BD),  # ▼ ▽
    (0x25CB, 0x25CF),  # ○ ◌ ◍ ◎ ●
    (0x3000, 0x303F),  # CJK symbols and punctuation
    (0x3041, 0x30FF),  # hiragana, katakana
    (0xFF01, 0xFF5E),  # fullwidth ASCII
    (0xFF61, 0xFF9F),  # halfwidth katakana
    (0xFFFD, 0xFFFD),  # replacement character
]
CODECS = {"ja": "shift_jis", "zh": "gb2312"}


def charset(lang):
    cps = set()
    for lo, hi in COMMON_RANGES:
        cps.update(range(lo, hi + 1))
    codec = CODECS[lang]
    for cp in range(0x0080, 0x10000):
        if 0xD800 <= cp <= 0xDFFF:
            continue
        try:
            chr(cp).encode(codec)
        except UnicodeEncodeError:
            continue
        cps.add(cp)
    cps.discard(0xFEFF)
    return sorted(cps)


def has_glyph(font, ch):
    # Pillow has no cmap lookup; a glyph with no extent and no advance is "missing".
    if ch.isspace():
        return True
    try:
        left, top, right, bottom = font.getbbox(ch)
    except Exception:
        return False
    return right > left and bottom > top


def render(font, ch, cell_w, canvas_h, origin_y):
    img = Image.new("1", (cell_w, canvas_h), 0)
    # Mode "1" makes FreeType render monochrome with hinting, which reads better on
    # e-paper than thresholded antialiasing.
    ImageDraw.Draw(img).text((0, origin_y), ch, font=font, fill=1, anchor="ls")
    return img


def build(lang, size, out):
    path = FONTS[lang]
    if not os.path.exists(path):
        sys.exit(f"missing {path}; download Noto Sans CJK into {FONT_DIR} (see the module docstring)")
    font = ImageFont.truetype(path, size)
    ascent, descent = font.getmetrics()
    cell_w = size + 2  # full-width CJK glyphs advance exactly `size`; slack for wide Latin glyphs
    canvas_h = ascent + descent
    cps = [cp for cp in charset(lang) if has_glyph(font, chr(cp))]

    # Pass 1: find the rows that actually carry ink, so the cell is as short as possible.
    # Noto's nominal line height is about 1.45 em but its glyphs span only about 1.2 em.
    rendered = []
    top = canvas_h
    bottom = 0
    for cp in cps:
        img = render(font, chr(cp), cell_w, canvas_h, ascent)
        bbox = img.getbbox()
        if bbox:
            top = min(top, bbox[1])
            bottom = max(bottom, bbox[3])
        rendered.append((cp, img))
    # A few rare symbols reach far outside the em box; clip those rather than pay for the
    # extra rows in every glyph. Ideographs and Latin letters fit comfortably in this band.
    top = max(top, ascent - int(round(0.95 * size)))
    bottom = min(bottom, ascent + int(round(0.27 * size)))
    cell_h = bottom - top
    baseline = ascent - top
    row_bytes = (cell_w + 7) // 8

    # Pass 2: crop and pack.
    index_bytes = bytearray()
    widths = bytearray()
    bitmaps = bytearray()
    for cp, img in rendered:
        ch = chr(cp)
        advance = max(0, min(255, int(round(font.getlength(ch)))))
        cropped = img.crop((0, top, cell_w, bottom))
        data = cropped.tobytes()  # mode "1" packs rows MSB-first, padded to bytes
        assert len(data) == row_bytes * cell_h
        index_bytes += struct.pack("<H", cp)
        widths.append(advance)
        bitmaps += data

    count = len(widths)
    header = b"NF1\0" + bytes((cell_w, cell_h, baseline, 0)) + struct.pack("<I", count)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "wb") as f:
        f.write(header)
        f.write(index_bytes)
        f.write(widths)
        f.write(bitmaps)
    total = len(header) + len(index_bytes) + len(widths) + len(bitmaps)
    print(f"{out}: {count} glyphs, cell {cell_w}x{cell_h}, baseline {baseline}, {total / 1024:.0f} KB")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--lang", choices=sorted(FONTS), required=True)
    ap.add_argument("--size", type=int, required=True, help="pixel size (em height)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    build(args.lang, args.size, args.out)


if __name__ == "__main__":
    sys.exit(main())
