# esp32-client-language-learning-api

Vocabulary cards for the [language-learning-api](https://github.com/WillPapper/language-learning-api)
on a **Zectrix NOTE4** (ESP32-S3, 4.2" 400×300 e-paper, ES8311 codec and speaker), written in
MicroPython.

| Button                   | Action                                                                 |
| ------------------------ | ---------------------------------------------------------------------- |
| Up (GPIO 39)             | Next word. At the end of the history, fetches a new random word from the API |
| Down (GPIO 18)           | Previous word                                                          |
| Main, tap (GPIO 0)       | Speak the word using the API's text-to-speech WAV                      |
| Main, hold (≥ 0.6 s)     | Switch between Japanese and Mandarin. Each language keeps its own history |

Only one language is on screen at a time. A card shows the word, its reading (kana and romaji,
or pinyin), the meaning and part of speech, and as many example sentences as fit, each with its
reading and translation.

## Setup

Requires the NOTE4 already running MicroPython v1.29 (`ESP32_GENERIC_S3-SPIRAM_OCT`) and
[mpremote](https://docs.micropython.org/en/latest/reference/mpremote.html):

```sh
uv tool install mpremote            # or: pip install mpremote
cp wifi_secrets.example.py wifi_secrets.py   # then fill in SSID and PASSWORD (2.4 GHz only)
tools/deploy.sh --fonts             # first install: code + 6.8 MB of fonts, a few minutes
tools/deploy.sh                     # later: code only
mpremote connect /dev/cu.usbmodem2101 repl   # watch the log; Ctrl-C stops the app
```

`wifi_secrets.py` is git-ignored. The API base URL is in `api.py`.

## Files

| File | What |
| ---- | ---- |
| `main.py` | Boot entry; runs `app.main()` |
| `app.py` | State machine: history per language, button dispatch, error screens |
| `ui.py` | Card and message layouts (pure drawing, also runs on a Mac) |
| `nfont.py` | NF1 bitmap font reader, text drawing, word wrap with kinsoku rules |
| `api.py` | HTTP client for `GET /:language/word` and the audio streams |
| `audio.py` | Speaker: ES8311 over I2C, 16-bit mono PCM over I2S, amp enable, WAV streaming |
| `es8311.py` | Minimal ES8311 register driver (playback only) |
| `buttons.py` | Polled buttons with tap / hold detection |
| `note4_epd.py` | E-paper driver (full refresh only, about 1.2 s) |
| `wifi.py` | `wifi.connect()` with readable errors |
| `fonts/` | Noto Sans CJK JP and SC rendered to bitmaps at 40, 22 and 16 px (SIL OFL 1.1) |
| `tools/deploy.sh` | Copy everything to the device and reset it |
| `tools/build_font.py` | Render Noto Sans CJK into the `fonts/*.nf1` files |
| `tools/preview.py` | Render a card to PNG on a Mac with a `framebuf` shim, for layout work |

## How it works

- **Display.** The e-paper only supports a full refresh in this driver, so every word change
  redraws the whole screen (about 1.2 s plus packing time). Buttons are not read during a
  refresh or a fetch.
- **Fonts.** The built-in `framebuf` font is 8×8 ASCII, so CJK text comes from pre-rendered
  bitmaps. `tools/build_font.py` renders every character Shift_JIS (Japanese) or GB2312
  (Chinese) can encode, plus Latin, pinyin tone letters, kana and a few symbols, into a
  fixed-cell 1-bit format with per-glyph advance widths. Glyphs are read from flash on demand;
  only the index stays in RAM. Rebuild after changing sizes or character sets:

  ```sh
  mkdir -p ../reference/noto-cjk && cd ../reference/noto-cjk
  curl -LO https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/OTF/Japanese/NotoSansCJKjp-Regular.otf
  curl -LO https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf
  cd - && for l in ja zh; do for s in 40 22 16; do
    uv run --with pillow python tools/build_font.py --lang $l --size $s --out fonts/$l$s.nf1; done; done
  ```

- **Audio.** The API serves canonical 44-byte-header WAV, 16 kHz 16-bit mono, which is streamed
  from the HTTPS response straight into `machine.I2S` in 2 KB chunks. MicroPython's I2S cannot
  output MCLK, so the ES8311 is told to derive its master clock from the bit clock; to make that
  clock fast enough (1.024 MHz, an entry in Espressif's coefficient table) the bus runs 32-bit
  slots and each 16-bit sample is widened in a viper function. GPIO 42 powers the codec and
  GPIO 46 enables the amplifier; both are pad-held by the stock firmware, so the driver passes
  `hold=` explicitly.
- **Preview on a Mac.** `uv run --with pillow python tools/preview.py ja out.png` draws a live
  random word with the real `ui.py`; pass a saved JSON response as a third argument, or
  `--message` for the start-up screen.

## Hardware notes

Pin assignments come from Zectrix's board package in
[xiaozhi-esp32](https://github.com/78/xiaozhi-esp32) (`main/boards/zectrix/zectrix-s3-epaper-4.2/config.h`).
See `NOTE4/AGENTS.md` in the parent folder for the full pin map, the stock-firmware backup and
MicroPython gotchas (pad holds, `miso=None` on SPI 2).
