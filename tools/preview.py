#!/usr/bin/env python3
"""Render the e-paper screens to PNG on a Mac, using the real ui.py and nfont.py.

    uv run --with pillow python tools/preview.py zh out.png          # a random word from the live API
    uv run --with pillow python tools/preview.py ja out.png word.json # a saved API response
    uv run --with pillow python tools/preview.py ja out.png --message

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
    if len(argv) > 3 and argv[3] == "--message":
        ui.draw_message(fb, fonts, "Language cards", ["Connecting to Wi-Fi…"],
                        battery={"percent": 20, "charging": False, "full": False, "mv": 3600})
    else:
        if len(argv) > 3:
            with open(argv[3], encoding="utf-8") as f:
                word = json.load(f)
        else:
            with urllib.request.urlopen(BASE_URL + "/%s/word" % language, timeout=20) as r:
                word = json.load(r)
        print(json.dumps(word, ensure_ascii=False)[:300])
        ui.draw_word(fb, fonts, word, language, 2, 5, argv[4] if len(argv) > 4 else None,
                     battery={"percent": 63, "charging": True, "full": False, "mv": 3780})
    fb.to_image().save(out)
    print("wrote", out)


if __name__ == "__main__":
    main(sys.argv)
