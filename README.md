# esp32-client-language-learning-api

Vocabulary cards for the [language-learning-api](https://github.com/WillPapper/language-learning-api)
on a **Zectrix NOTE4** (ESP32-S3, 4.2" 400×300 e-paper, ES8311 codec and speaker), written in
MicroPython.

| Button                   | Action                                                                 |
| ------------------------ | ---------------------------------------------------------------------- |
| Up (GPIO 39)             | Next word. At the end of the history, fetches a new random word from the API. In reveal mode the first press reveals the card (Mandarin: pinyin first, then everything on the second press) |
| Down (GPIO 18)           | Scroll the sentence area down a page when it says "▼ more"; otherwise the word's next example sentence (cycles) |
| Main, tap (GPIO 0)       | Speak the word, then after a 0.5 s pause the example sentence on screen (the API's text-to-speech WAVs). Playback always runs to the end; taps while it plays are ignored, other presses are handled afterwards |
| Main, double tap         | The same at slow speed (the API's `audio.slow`, the voice's own 0.75× delivery); the LED flashes instead of staying lit. Words without slow audio yet play at normal speed with a note in the footer |
| Main, hold (≥ 0.6 s)     | Switch between Japanese and Mandarin. Each language keeps its own history |
| Up, hold                 | Reveal mode on/off: cards show only the headword until "up" is pressed |
| Down, hold               | Previous word                                                          |
| Up + down together       | Open or close the settings screen                                      |

Only one language is on screen at a time. A card shows the word, its reading (kana and romaji,
or pinyin), the meaning and part of speech, and below a divider one example sentence with its
reading(s) and translation. The sentence is picked at random when the word is fetched (every
word has two or four) and its audio is what a tap plays. The area below the divider scrolls:
when the whole sentence block does not fit, the divider says `▼ more` and "down" shows the next
screenful; at the end it says `2/4  ▼ next` and "down" moves to the next sentence from its top.
The header shows the level lists the word on screen belongs to (with an ellipsis if they do not
fit), the battery and the Wi-Fi state.

**Controls page**: the first row of the settings list, "Controls", opens a page that lists
every gesture above. Any button returns to settings.

**Settings** (up + down together): up/down move, the main button changes the selected row
(the footer says how: tap for louder or longer, hold for quieter or shorter; tap toggles
reveal mode or a level checkbox), up + down again saves and closes.

| Setting | |
| ------- | - |
| Volume | 0-100 % in steps of 10; a short beep plays the new level |
| Reveal mode | on/off, same as holding "up" |
| Sleep after | 1, 2, 5, 10 or 30 minutes idle, or never (see Power) |
| Word levels | One checkbox per level label the API reports for the current language, the one the cards are showing (`GET /:language/levels`, with word counts); the other language's list appears after switching with a hold of the main button. All are checked until you uncheck some; the checked ones are sent as `?level=A,B` on word fetches, and checking them all again clears the filter |

Settings are saved to `settings.json` on the device and survive resets and deploys.

## Setup

Requires the NOTE4 already running MicroPython v1.29 (`ESP32_GENERIC_S3-SPIRAM_OCT`) and
[mpremote](https://docs.micropython.org/en/latest/reference/mpremote.html):

```sh
uv tool install mpremote            # or: pip install mpremote
cp wifi_secrets.example.py wifi_secrets.py   # then fill in SSID and PASSWORD (2.4 GHz only)
tools/deploy.sh --fonts             # first install: code + 6.8 MB of fonts, a few minutes
tools/deploy.sh                     # later: code only
uv run --with pyserial python tools/log.py   # watch the log (or: mpremote connect /dev/cu.usbmodem2101 repl)
```

`wifi_secrets.py` is git-ignored. The API base URL is in `api.py`.

The fonts stay in the device's flash filesystem across resets and code deploys, so they are
uploaded once. `--fonts` compares sizes and only sends files that are missing or changed; a full
upload is about 25 minutes (mpremote moves roughly 5 KB/s). Only `esptool erase-flash` or
reflashing MicroPython removes them. The 14 MB filesystem has about 7 MB free with the fonts
installed.

## Files

| File | What |
| ---- | ---- |
| `main.py` | Boot entry; runs `app.main()` |
| `app.py` | State machine: history per language, button dispatch, error screens |
| `ui.py` | Card and message layouts (pure drawing, also runs on a Mac) |
| `nfont.py` | NF1 bitmap font reader, text drawing, word wrap with kinsoku rules |
| `api.py` | HTTP client for `GET /:language/word` and the audio streams |
| `audio.py` | Speaker: ES8311 over I2C, 16-bit mono PCM over I2S, amp enable, WAV streaming, LED on while speaking |
| `es8311.py` | Minimal ES8311 register driver (playback only) |
| `buttons.py` | Polled buttons with tap / hold detection |
| `battery.py` | Battery voltage (ADC on GPIO 4), percent and charging state (GPIO 2 / GPIO 1) |
| `settings.py` | Settings model, saved as `settings.json` on the device |
| `note4_epd.py` | E-paper driver: partial refresh (about 0.8 s), full every 10 partials or on request |
| `wifi.py` | `wifi.connect()` with readable errors |
| `fonts/` | Noto Sans CJK JP and SC rendered to bitmaps at 40, 22 and 16 px (SIL OFL 1.1) |
| `tools/deploy.sh` | Copy everything to the device and reset it |
| `tools/build_font.py` | Render Noto Sans CJK into the `fonts/*.nf1` files |
| `tools/preview.py` | Render any screen to PNG on a Mac with a `framebuf` shim, for layout work |
| `tools/log.py` | Print the device's serial output without interrupting the app (mpremote's `exec`/`run`/`cp` all send Ctrl-C) |
| `AGENTS.md`, `CLAUDE.md` | How to work on this codebase: the edit/deploy/watch loop and the pitfalls |

## How it works

- **Display.** `note4_epd.show()` does a partial refresh (about 0.8 s) when it can, telling the
  panel the previous frame as well as the new one, and a full refresh (about 1.1 s, with a
  white flash) the first time, every 10 partials to clear ghosting, and on a language switch.
  Row packing is a viper function (10 ms).
- **Responsiveness.** A background thread keeps one random word prefetched per language and
  downloads the WAVs (word and shown sentence, normal then slow) for the current and next
  word into RAM (up to 16 cached, 20-50 KB each),
  and the main loop warms the font caches for the next words while idle. So "up" costs one
  partial refresh, a tap speaks at once, and a language switch is one full refresh. Buttons are
  polled while the panel is busy and between audio chunks; presses noticed then are queued, and
  runs of up/down are merged into one move with a single refresh. Beyond the end of the history
  several "up" presses fetch only one new word, since random words are interchangeable.
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
  slots and each 16-bit sample is widened in a viper function. The I2S bus starts at boot and
  keeps running (looping silence between clips) because the codec's state machine needs its
  clock to finish powering up: initialising the codec and only then starting I2S per clip gave
  no sound. GPIO 42 powers the codec and GPIO 46 enables the amplifier (only while playing);
  the front LED (GPIO 3, active low) is lit exactly while the amplifier is on, so it shows
  when the device is speaking and goes out when the last clip has played: solid for normal
  speed, flashing (4 Hz) for the slow version, dimmed by PWM to 10 % duty
  (`LED_BRIGHTNESS` in `audio.py`; at full duty it is glaring);
  both are pad-held by the stock firmware, so the driver passes `hold=` explicitly. Volume is
  `audio.Speaker(volume=...)`, 100 = 0 dB at the DAC; the API's files are normalised to
  -20 dBFS RMS.
- **Header.** Battery icon (filled to the stock firmware's percent curve, a bolt whenever USB
  power is present, charging or full) with the percentage, then a Wi-Fi glyph: signal bars
  when connected, a cross while the link is down. Both are read each time a card is drawn, and
  the idle loop checks them every 0.5 s: when the USB state, the 10 % step or the connection
  differs from what is shown for two reads in a row, the card is redrawn (one partial refresh,
  about a second after plugging or unplugging the cable). If Wi-Fi drops, the prefetch thread
  reconnects on its own; the cross goes away when it has. The middle of the header lists the
  word's own level labels (`levels`), space-separated; when they do not all fit (three HSK
  labels at a high battery percentage), whole labels are kept and the rest counted, as in
  `HSK1 HSK2025-1 +1`. A reading ending in `*` (`dì*`) is the API's heteronym marker: the characters have
  another reading too, so the recording may carry that one. It is shown as the API sends it.
- **Power.** While nothing is queued and the prefetch thread is idle, the main loop drops the
  CPU to 80 MHz and goes back to 240 MHz when work arrives. On battery, after the "sleep
  after" setting (default 2 min) with no presses, the app turns Wi-Fi and the codec off and
  light-sleeps in 100 ms slices, polling the three buttons and the charger pins between slices
  (MicroPython's pin wake-up uses the ext0 source, which takes one RTC-capable pin, and the up
  button on GPIO 39 is not one). The card stays on the e-paper, which needs no power to hold
  an image. Any press wakes it: the press itself is handled right away from the prefetched
  word and audio, the codec is re-initialised (about 0.2 s) and Wi-Fi reconnects in the
  background (about 6 s, during which the header keeps its signal bars). Plugging or unplugging
  the cable also wakes it for one header redraw. After 30 idle minutes on battery it drops the
  battery latch (GPIO 17), as the stock firmware does after five. The card stays on the panel
  (it needs no power to hold an image) with its footer replaced by "Powered off   hold ▼ to
  turn on". ▼ (GPIO 18) is the hardware power button: holding it powers the board, and
  `main.py` raises the latch first thing at boot, about a second after power is applied, so
  the hold only needs to last until then. The press that turned the unit on is ignored (the
  app waits for every button to be released before it starts polling). ● cannot turn the unit
  on: it is the BOOT strapping pin, and holding it at power-up puts the chip in the ROM
  download mode instead of booting. USB power always revives the unit. On USB power the app
  never sleeps or powers off, which also
  keeps the USB console alive for `tools/deploy.sh` (light sleep disconnects the USB serial
  port). Idle draw is not measured; awake, expect tens of milliamps, mostly the Wi-Fi radio.
- **Preview on a Mac.** `uv run --with pillow python tools/preview.py ja out.png` draws a live
  random word with the real `ui.py`; pass a saved JSON response (`curl .../ja/words/1326`) as a
  third argument. Flags: `--hidden` and `--pinyin` for the reveal-mode stages, `--scroll=N` to
  start the sentence area at line N, `--off` (the card left on the panel after power-off),
  `--settings`, `--info` (the controls page) and `--message`
  (the start-up screen). This is the only test harness: look at the PNG before deploying.
- **Working on the code.** See `AGENTS.md`: the deploy and log loop, what interrupts the app,
  and the things that have bitten before (secrets, pad holds, audio start-up order, the API
  changing shape).

## Hardware notes

Pin assignments come from Zectrix's board package in
[xiaozhi-esp32](https://github.com/78/xiaozhi-esp32) (`main/boards/zectrix/zectrix-s3-epaper-4.2/config.h`).
See `NOTE4/AGENTS.md` in the parent folder for the full pin map, the stock-firmware backup and
MicroPython gotchas (pad holds, `miso=None` on SPI 2).
