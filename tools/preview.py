#!/usr/bin/env python3
"""Render the e-paper screens to PNG on a Mac, using the real ui.py and nfont.py.

    uv run --with pillow python tools/preview.py zh out.png          # a random word from the live API
    uv run --with pillow python tools/preview.py ja out.png word.json # a saved API response
    uv run --with pillow python tools/preview.py ja out.png --message
    uv run --with pillow python tools/preview.py ja out.png --hidden   # reveal mode, before the reveal
    uv run --with pillow python tools/preview.py ja out.png --settings

Run from the repository root so the fonts/ directory is found.
"""
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)  # the framebuf shim shadows nothing on the Mac
sys.path.insert(1, ROOT)
os.chdir(ROOT)

import framebuf  # noqa: E402  (tools/framebuf.py)
import ui  # noqa: E402

BASE_URL = "https://language-learning-api.papper.workers.dev"


def main(argv):
    if len(argv) < 3:
        sys.exit(__doc__)
    language, out = argv[1], argv[2]
    fonts = ui.Fonts(language)
    fb = framebuf.FrameBuffer(bytearray(ui.WIDTH * ui.HEIGHT // 8), ui.WIDTH, ui.HEIGHT, framebuf.MONO_HLSB)
    state = {"battery": {"percent": 63, "charging": True, "full": False, "mv": 3780}, "online": True}
    flags = [a for a in argv[3:] if a.startswith("--")]
    rest = [a for a in argv[3:] if not a.startswith("--")]
    if "--settings" in flags:
        rows = [("Volume", "80 %", None), ("Reveal mode", "off", None), ("Sleep after", "2 min", None),
                ("日本語  N5  (24)", None, True), ("日本語  N2  (500)", None, False), ("日本語  N1  (500)", None, False),
                ("中文  HSK1  (158)", None, True), ("中文  HSK2  (2)", None, False), ("中文  HSK3.0-1  (508)", None, True),
                ("中文  HSK2025-1  (600)", None, False)]
        ui.draw_settings(fb, fonts, rows, 6, state)
    elif "--message" in flags:
        state["battery"] = {"percent": 20, "charging": False, "full": False, "mv": 3600}
        state["online"] = False
        ui.draw_message(fb, fonts, "Language cards", ["Connecting to Wi-Fi…"], state=state)
    else:
        if rest:
            with open(rest[0], encoding="utf-8") as f:
                word = json.load(f)
        else:
            req = urllib.request.Request(BASE_URL + "/%s/word" % language, headers={"User-Agent": "note4-preview"})
            with urllib.request.urlopen(req, timeout=20) as r:
                word = json.load(r)
        print(json.dumps(word, ensure_ascii=False)[:300])
        word["sentence"] = 1
        ui.draw_word(fb, fonts, word, language, 2, 5, rest[1] if len(rest) > 1 else None,
                     state=state, hidden="--hidden" in flags)
    fb.to_image().save(out)
    print("wrote", out)


if __name__ == "__main__":
    main(sys.argv)
