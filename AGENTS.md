# Working on this codebase

Notes for coding agents and anyone new to the repo. `README.md` documents what the app does
and how it works; this file is about how to work on it. The parent folder's `NOTE4/AGENTS.md`
(if you have the parent checkout) holds the hardware facts, the pin map, the stock-firmware
backup and a dated log of everything done to the device.

## Where things are

```
~/Dropbox/esp32/                       local git repo, no remote; CLAUDE.md there is a one-liner
└── NOTE4/
    ├── AGENTS.md, CLAUDE.md           hardware facts, pin map, device log; read before touching pins
    ├── esp32-client-language-learning-api/   THIS repo (GitHub remote); everything below is here
    │   ├── main.py                    boot entry: holds GPIO 17 (battery latch), then app.main()
    │   ├── app.py                     the state machine: buttons, modes, prefetch thread, sleep
    │   ├── ui.py                      screens as pure drawing (also runs on the Mac)
    │   ├── api.py                     the only module that knows API URLs and shapes
    │   ├── audio.py, es8311.py        speaker: I2S + codec, WAV streaming
    │   ├── buttons.py, battery.py, wifi.py, note4_epd.py, nfont.py, settings.py
    │   ├── wifi_secrets.py            git-ignored credentials (template: wifi_secrets.example.py)
    │   ├── fonts/*.nf1                bitmap fonts; on the device in /fonts, rarely re-uploaded
    │   └── tools/                     Mac-side only, never deployed:
    │       deploy.sh                  copy code + secrets, reset
    │       log.py                     passive serial reader
    │       preview.py, framebuf.py    render any screen to PNG with a framebuf shim
    │       build_font.py              render Noto Sans CJK into fonts/*.nf1
    ├── language-learning-api/         the API (Cloudflare Worker), its own repo; git pull often
    ├── micropython/                   the first Hello World and the downloaded firmware image
    ├── reference/                     upstream sources (board package, ES8311 driver, fonts)
    └── *.bin, SHA256SUMS.txt          stock-firmware backups; never delete
```

On the device the flat layout is the same minus `tools/`, plus `settings.json` and `boot.py`.
`README.md`'s Files table says what each module does in more detail.

## The loop

1. Edit on the Mac. There is no test suite; `ui.py` and `nfont.py` run unchanged on the Mac,
   so check layouts with `tools/preview.py` (see README, "Preview on a Mac") and look at the
   PNG before deploying. Everything else needs the device.
2. `python3 -m py_compile app.py ui.py ...` catches syntax errors in seconds; a syntax error
   deployed to the device leaves it at the REPL with no card drawn.
3. `tools/deploy.sh` copies the code (the list is the `CODE=` line in the script: **add new
   modules there**), uploads `wifi_secrets.py`, and resets the device so `main.py` starts.
   `--fonts` also checks the font files (needed once; they persist in flash).
4. Watch it: `uv run --with pyserial python tools/log.py 20` right after the deploy prints
   the boot log (Wi-Fi, first word, "ready"). From a terminal `mpremote connect PORT repl`
   works too. Anything else you run through mpremote (`exec`, `run`, `cp`, `reset`) sends
   Ctrl-C first and **stops the app** until the next reset; deploy again or
   `mpremote connect PORT reset` to restart it.
5. Commit after each change that works, and push (`origin` is GitHub). Commit messages say
   what changed for the user and why, not which functions were touched.

The device is at `/dev/cu.usbmodem2101` (`PORT=` overrides in both tools). It only shows up
while the app is awake: on battery the app light-sleeps after the "sleep after" setting and
the USB serial port disappears until a button wakes it. On USB power it never sleeps, so keep
the cable in while developing.

## Things that bite

- **`wifi_secrets.py` is git-ignored and must stay that way.** Never print, log, cat or commit
  it. `wifi_secrets.example.py` is the committed template.
- **`settings.json` lives on the device only** (volume, reveal mode, sleep delay, level
  filter). Deploys do not touch it. To reset it: `mpremote connect PORT rm :settings.json`.
- **The API is a moving target.** It is developed in another session; `git pull` its repo
  (`../language-learning-api/`, if you have it) and re-read its README before relying on a
  response shape. Shapes that have already changed under this client: `audio.url` became
  absolute; `level` became a `levels` list; `audio` became `{"normal": ..., "slow": ...}`;
  `?level=` accepts comma-separated lists. `api.py` is the only module that knows URLs.
  Additive changes seen since (harmless, but know they exist): `heteronym: true` with a `*`
  appended to the reading; a `sources` list per word; up to three level labels per word
  (`levels_of` joins them with single spaces so they fit the header). A quick check that
  nothing broke: `curl -s -A note4 "$BASE/zh/word" | python3 -m json.tool` and look for
  `levels`, `audio.normal.url`, `audio.slow.url`, `sentences[].audio`, `pinyin`/`kana`;
  then `tools/preview.py` with the saved JSON. Python's urllib needs a `User-Agent` (403).
- **Pad holds.** GPIO 3, 6, 17, 42 and 46 may be pad-held by earlier firmware; a `Pin(...)`
  without `hold=` silently does nothing on them. Always pass `hold=True` or `hold=False`.
- **Audio order.** The I2S bus must be running before the ES8311 is initialised and must keep
  running; `audio.Speaker()` does this. Re-create the whole `Speaker` after any `deinit()`.
- **E-paper.** Re-init the panel before every refresh (`note4_epd.show()` does). Each refresh
  is 0.8-1.1 s; design screens so one press is one refresh. `ui.draw_*` take a `state` dict
  (battery, online) for the header.
- **Buttons are polled, not interrupt-driven**, also during BUSY waits and audio chunks via
  callbacks (`epd.on_wait`, `speaker.on_chunk`). New long operations should call
  `App.poll_buttons()` now and then or presses are missed. Events are queued in `App.pending`
  and dispatched in `process_pending()`, which is where the button mapping lives; the settings
  and controls screens use the same events with their own handlers (`settings_event`).
- **Threads.** One `_thread` (24 KB stack) does all background network work. Share state only
  under `App.lock`; one HTTPS request at a time under `App.net_lock`. The main loop must never
  block on the network except in `take_word()` and `speak()`'s streaming fallback.
- **Memory.** Word dicts and WAV bytes live in PSRAM (8 MB); the glyph caches are bounded.
  `gc.collect()` after every refresh is deliberate.
- **Light sleep** drops the USB serial port and is only entered on battery. `machine.lightsleep`
  with the thread alive is fine. Wake sources are polled every 100 ms; GPIO 39 cannot be an
  ext0/ext1 wake pin on the S3.

## Verified on the device

Wi-Fi, first word, partial/full refresh timings, speech (the user hears it), the sleep/wake cycle (sleep, wake on a press, codec re-init, Wi-Fi reconnect in
about 6 s, fetch, speak). **Not verified by an agent:** power-on after the 30-minute power-off
(needs the cable out). The down button (GPIO 18) is the hardware power button: Zectrix's
reference demo documents it as "DOWN / power button", the stock firmware waits for GPIO 18 to
read high after raising the latch (that is, for the power-on press to be released), and the
user confirmed the front button does nothing (it is the BOOT strap). The user turned the unit
on with up + down together before the footer said to hold down; down alone is what the
hardware uses. The app only raises GPIO 17 at boot and ignores buttons held at start. Also
unverified:
slow audio playback end to end (as of 2026-10-21 the API has normal and slow clips for every
sampled word and sentence in both languages, so a double tap on any card tests it).

## Conventions

- MicroPython, not CPython: `%` formatting throughout, no `dataclasses`, `typing` or
  `pathlib`, `time.ticks_ms()`/`ticks_diff()` for timing. `micropython.viper` for byte loops.
- Screens are pure functions in `ui.py` that draw into a `framebuf.FrameBuffer`; `app.py`
  decides what to draw and when to refresh. Keep hardware access out of `ui.py` so the Mac
  preview keeps working.
- Footers are hints, not menus: keep them under about 45 characters of the 16 px font or they
  get an ellipsis. The full gesture list is the controls page (`ui.CONTROLS`); update it, the
  button table in `README.md` and the docstring at the top of `app.py` together.
- Document user-facing changes in `README.md` in the same commit.
