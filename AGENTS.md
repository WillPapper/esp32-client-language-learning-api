# Working on this codebase

Notes for coding agents and anyone new to the repo. `README.md` documents what the app does
and how it works; this file is about how to work on it. The parent folder's `NOTE4/AGENTS.md`
(if you have the parent checkout) holds the hardware facts, the pin map, the stock-firmware
backup and a dated log of everything done to the device.

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

Wi-Fi, first word, partial/full refresh timings, speech (the user hears it), tap-to-cancel
plumbing, the sleep/wake cycle (sleep, wake on a press, codec re-init, Wi-Fi reconnect in
about 6 s, fetch, speak). **Not verified:** power-on after the 30-minute power-off (needs the
cable out; the assumption is the stock behaviour, hold the front button about a second), and
slow audio playback end to end (the API had almost no slow clips when this was written).

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
