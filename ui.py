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


def _header(fb, fonts, left, middle, right):
    fb.fill_rect(0, 0, WIDTH, HEADER_H, BLACK)
    y = (HEADER_H - fonts.small.height) // 2
    nfont.draw(fb, fonts.small, left, MARGIN, y, ink=WHITE)
    if middle:
        nfont.draw_centered(fb, fonts.small, middle, y, ink=WHITE, w=WIDTH)
    if right:
        nfont.draw_right(fb, fonts.small, right, WIDTH - MARGIN, y, ink=WHITE)


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


def draw_word(fb, fonts, word, language, position, total, status=None):
    """Lay out one vocabulary card. `position` is 0-based within `total` words seen."""
    fb.fill(WHITE)
    level = word.get("level") or ""
    pos = word.get("partOfSpeech") or ""
    _header(fb, fonts, LANGUAGE_NAME[language], level, "%d / %d" % (position + 1, total))

    limit = HEIGHT - FOOTER_H - 2
    y = HEADER_H + 6
    head = word.get("characters") or ""
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
    if sentences and y + 8 + fonts.body.height <= limit:
        y += 4
        fb.hline(MARGIN + 40, y, WIDTH - 2 * (MARGIN + 40), BLACK)
        y += 6
        s = sentences[0]
        y = _lines(fb, fonts.body, s.get("characters") or "", y, limit, 2)
        s_reading = reading_of(language, s)
        if s_reading:
            y = _lines(fb, fonts.small, s_reading, y, limit, 2)
        y = _lines(fb, fonts.small, s.get("translation") or "", y, limit, 2)

    if status:
        _footer(fb, fonts, status)
    else:
        _footer(fb, fonts, "▲ next   ▼ back   ● speak   hold ● for " + LANGUAGE_NAME_EN[OTHER[language]])


def draw_message(fb, fonts, title, lines, footer=None):
    """A plain screen: a title bar, then up to a few lines of text."""
    fb.fill(WHITE)
    _header(fb, fonts, title, None, None)
    y = HEADER_H + 24
    for text in lines:
        y = _lines(fb, fonts.body, text, y, HEIGHT - FOOTER_H, 3)
        y += 6
    if footer:
        _footer(fb, fonts, footer)
