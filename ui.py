"""Screen layouts for the 400x300 e-paper. Pure drawing: no hardware access, so tools/preview.py
can render the same code on a Mac.

Colours follow note4_epd: 1 is white, 0 is black.
"""
import nfont

WIDTH = 400
HEIGHT = 300
WHITE = 1
BLACK = 0
MARGIN = 14
HEADER_H = 26
FOOTER_H = 22

LANGUAGE_NAME = {"ja": "日本語", "zh": "中文"}
LANGUAGE_NAME_EN = {"ja": "Japanese", "zh": "Mandarin"}
OTHER = {"ja": "zh", "zh": "ja"}


class Fonts:
    """The three sizes of one language's font."""

    def __init__(self, language, directory="fonts"):
        self.language = language
        self.big = nfont.Font("%s/%s40.nf1" % (directory, language))
        self.body = nfont.Font("%s/%s22.nf1" % (directory, language))
        self.small = nfont.Font("%s/%s16.nf1" % (directory, language))


def reading_of(language, item):
    """The pronunciation line for a word or sentence: kana (+ romaji) for ja, pinyin for zh."""
    if language == "zh":
        return item.get("pinyin") or ""
    kana = item.get("kana") or ""
    romaji = item.get("romaji") or ""
    if kana and kana == item.get("characters"):
        kana = ""  # the headword is already in kana
    if kana and romaji:
        return kana + "  " + romaji
    return kana or romaji


def levels_of(word):
    """The word's level labels as one string ("N5", "HSK1 · HSK3.0-1"). Older API responses
    had a single `level` string."""
    levels = word.get("levels")
    if levels is None:
        levels = [word["level"]] if word.get("level") else []
    return " · ".join(levels)


def texts_of(fonts, word, language):
    """Every (font, text) pair draw_word() will need, so the glyphs can be cached ahead of time."""
    out = [(fonts.big, word.get("characters") or ""), (fonts.body, reading_of(language, word)),
           (fonts.body, (word.get("meaning") or "") + (word.get("partOfSpeech") or "")),
           (fonts.small, levels_of(word) + "0123456789 /%:")]
    for s in word.get("sentences") or []:
        out.append((fonts.body, s.get("characters") or ""))
        out.append((fonts.small, (s.get("kana") or "") + (s.get("romaji") or "") + (s.get("pinyin") or "")
                    + (s.get("translation") or "")))
    return out


BATTERY_W = 24
BATTERY_H = 12


def draw_battery(fb, x, y, battery, ink=WHITE):
    """A battery outline with its charge level filled in, and a bolt whenever USB power is
    present (charging or already full). `battery` is the dict from battery.Battery.read(), or
    None to draw nothing."""
    if not battery:
        return x
    bg = 1 - ink
    fb.rect(x, y, BATTERY_W - 2, BATTERY_H, ink)
    fb.fill_rect(x + BATTERY_W - 2, y + 3, 2, BATTERY_H - 6, ink)  # the terminal nub
    inner = BATTERY_W - 6
    fill = (inner * battery.get("percent", 0) + 50) // 100
    if battery.get("full"):
        fill = inner
    if fill:
        fb.fill_rect(x + 2, y + 2, fill, BATTERY_H - 4, ink)
    if battery.get("charging") or battery.get("full"):
        # A small bolt drawn in the background colour over the fill, and ink over the empty part
        bx = x + BATTERY_W // 2 - 4
        for i, (dx, w) in enumerate(((3, 3), (2, 3), (1, 3), (0, 5), (3, 3), (2, 3), (1, 3), (0, 2))):
            yy = y + 2 + i
            c = bg if bx + dx < x + 2 + fill else ink
            fb.fill_rect(bx + dx, yy, w, 1, c)
    return x + BATTERY_W + 6


WIFI_W = 13
WIFI_H = 12


def draw_wifi(fb, x, y, online, ink=WHITE):
    """Four rising signal bars; when offline, a cross instead. `online` None draws nothing."""
    if online is None:
        return x
    if online:
        for i in range(4):
            h = 3 + 3 * i
            fb.fill_rect(x + 3 * i + (1 if i else 0), y + WIFI_H - h, 2, h, ink)
    else:
        for i in range(WIFI_H - 2):
            fb.pixel(x + 1 + i, y + 1 + i, ink)
            fb.pixel(x + 2 + i, y + 1 + i, ink)
            fb.pixel(x + WIFI_W - 2 - i, y + 1 + i, ink)
            fb.pixel(x + WIFI_W - 3 - i, y + 1 + i, ink)
    return x + WIFI_W + 6


def _header(fb, fonts, left, middle, right, state=None):
    """Black title bar. `state` holds "battery" (battery.Battery.read()) and "online" (bool, or
    None to draw no Wi-Fi glyph); either may be missing."""
    state = state or {}
    fb.fill_rect(0, 0, WIDTH, HEADER_H, BLACK)
    y = (HEADER_H - fonts.small.height) // 2
    battery = state.get("battery")
    x = draw_battery(fb, MARGIN, (HEADER_H - BATTERY_H) // 2, battery)
    if battery:
        pct = "%d%%" % battery.get("percent", 0)
        nfont.draw(fb, fonts.small, pct, x - 2, y, ink=WHITE)
        x += fonts.small.width(pct) + 6
    x = draw_wifi(fb, x, (HEADER_H - WIFI_H) // 2, state.get("online"))
    if left:
        x = nfont.draw(fb, fonts.small, left, x, y, ink=WHITE)
    right_x = WIDTH - MARGIN
    if right:
        right_x = nfont.draw_right(fb, fonts.small, right, WIDTH - MARGIN, y, ink=WHITE) - fonts.small.width(right)
    if middle:
        # Centred when it fits between the left and right texts, else pushed right of the left
        # text and shortened.
        room = right_x - 12 - (x + 12)
        middle = nfont.fit(fonts.small, middle, room)
        mx = max(x + 12, (WIDTH - fonts.small.width(middle)) // 2)
        nfont.draw(fb, fonts.small, middle, mx, y, ink=WHITE)


def _footer(fb, fonts, text):
    y = HEIGHT - FOOTER_H + (FOOTER_H - fonts.small.height) // 2
    fb.hline(MARGIN, HEIGHT - FOOTER_H, WIDTH - 2 * MARGIN, BLACK)
    nfont.draw_centered(fb, fonts.small, nfont.fit(fonts.small, text, WIDTH - 2 * MARGIN), y, w=WIDTH)


def _lines(fb, font, text, y, limit, max_lines, centered=False, x=MARGIN):
    """Draw wrapped text from y downwards, stopping at `limit`. Returns the next y."""
    for line in nfont.wrap(font, text, WIDTH - 2 * MARGIN)[:max_lines]:
        if y + font.height > limit:
            break
        if centered:
            nfont.draw_centered(fb, font, line, y, w=WIDTH)
        else:
            nfont.draw(fb, font, line, x, y)
        y += font.height
    return y


def _sentence_block(fonts, language, s):
    """[(font, lines)] for one example sentence: text, its reading(s), translation."""
    max_w = WIDTH - 2 * MARGIN
    block = [(fonts.body, nfont.wrap(fonts.body, s.get("characters") or "", max_w)[:2])]
    if language == "zh":
        readings = [s.get("pinyin")]
    else:
        readings = [s.get("kana"), s.get("romaji")]
    for text in readings:
        if text:
            block.append((fonts.small, nfont.wrap(fonts.small, text, max_w)[:2]))
    block.append((fonts.small, nfont.wrap(fonts.small, s.get("translation") or "", max_w)[:2]))
    return block


def draw_word(fb, fonts, word, language, position, total, status=None, state=None, hidden=False):
    """Lay out one vocabulary card. `position` is 0-based within `total` words seen. With
    `hidden` (reveal mode) only the headword is drawn, with its reading and meaning hidden
    until the next "up" press."""
    fb.fill(WHITE)
    pos = word.get("partOfSpeech") or ""
    _header(fb, fonts, LANGUAGE_NAME[language], levels_of(word), "%d / %d" % (position + 1, total), state)

    limit = HEIGHT - FOOTER_H - 2
    y = HEADER_H + 6
    head = word.get("characters") or ""
    if hidden:
        y = (HEIGHT - FOOTER_H - fonts.big.height) // 2 - 10
        if fonts.big.width(head) <= WIDTH - 2 * MARGIN:
            nfont.draw_centered(fb, fonts.big, head, y, w=WIDTH)
        else:
            _lines(fb, fonts.body, head, y, limit, 3, centered=True)
        _footer(fb, fonts, status or "▲ reveal   ▼ back   ● speak   hold ▼ to exit")
        return
    if fonts.big.width(head) <= WIDTH - 2 * MARGIN:
        nfont.draw_centered(fb, fonts.big, head, y, w=WIDTH)
        y += fonts.big.height
    else:
        y = _lines(fb, fonts.body, head, y, limit, 2, centered=True)

    reading = reading_of(language, word)
    if reading:
        y = _lines(fb, fonts.body, reading, y, limit, 1, centered=True)
    y += 2

    meaning = word.get("meaning") or ""
    if pos:
        meaning = meaning + "  (" + pos + ")"
    y = _lines(fb, fonts.body, meaning, y, limit, 2, centered=True)

    sentences = word.get("sentences") or []
    if sentences:
        # One example sentence: the one chosen at random when the word was fetched (its
        # audio is what a tap plays); hold "down" cycles to the next.
        index = word.get("sentence", 0) % len(sentences)
        block = _sentence_block(fonts, language, sentences[index])
        height = 10 + sum(font.height * len(lines) for font, lines in block)
        if y + height <= limit:
            y += 4
            fb.hline(MARGIN + 40, y, WIDTH - 2 * (MARGIN + 40), BLACK)
            if len(sentences) > 1:
                nfont.draw_right(fb, fonts.small, "%d/%d" % (index + 1, len(sentences)), WIDTH - MARGIN,
                                 y - fonts.small.height // 2)
            y += 6
            for font, lines in block:
                for line in lines:
                    nfont.draw(fb, font, line, MARGIN, y)
                    y += font.height

    if status:
        _footer(fb, fonts, status)
    else:
        _footer(fb, fonts, "▲ next   ▼ back   ● speak   hold ● for " + LANGUAGE_NAME_EN[OTHER[language]])


def draw_settings(fb, fonts, rows, cursor, state=None):
    """The settings list. `rows` is [(label, value, checked)]: `value` is text drawn at the
    right, or None; `checked` draws a checkbox instead (True/False), or None for no box. The
    row under `cursor` is inverted. Rows scroll so the cursor stays visible."""
    fb.fill(WHITE)
    _header(fb, fonts, "Settings", None, None, state)
    row_h = fonts.body.height + 6
    top = HEADER_H + 4
    visible = (HEIGHT - FOOTER_H - top - 2) // row_h
    first = max(0, min(cursor - visible + 1, len(rows) - visible)) if cursor >= visible else 0
    y = top
    for i in range(first, min(len(rows), first + visible)):
        label, value, checked = rows[i]
        selected = i == cursor
        ink = WHITE if selected else BLACK
        if selected:
            fb.fill_rect(MARGIN - 6, y, WIDTH - 2 * (MARGIN - 6), row_h, BLACK)
        ty = y + 3
        nfont.draw(fb, fonts.body, label, MARGIN, ty, ink=ink)
        if value is not None:
            nfont.draw_right(fb, fonts.body, value, WIDTH - MARGIN, ty, ink=ink)
        elif checked is not None:
            size = 14
            bx = WIDTH - MARGIN - size
            by = y + (row_h - size) // 2
            fb.rect(bx, by, size, size, ink)
            if checked:
                fb.fill_rect(bx + 3, by + 3, size - 6, size - 6, ink)
        y += row_h
    _footer(fb, fonts, "▲▼ move   ● change   hold ● back   ▲+▼ save")
    fy = HEIGHT - FOOTER_H + (FOOTER_H - fonts.small.height) // 2
    if first > 0:
        nfont.draw(fb, fonts.small, "▲", 2, fy)
    if first + visible < len(rows):
        nfont.draw_right(fb, fonts.small, "▼", WIDTH - 2, fy)


def draw_message(fb, fonts, title, lines, footer=None, state=None):
    """A plain screen: a title bar, then up to a few lines of text."""
    fb.fill(WHITE)
    _header(fb, fonts, title, None, None, state)
    y = HEADER_H + 24
    for text in lines:
        y = _lines(fb, fonts.body, text, y, HEIGHT - FOOTER_H, 3)
        y += 6
    if footer:
        _footer(fb, fonts, footer)
