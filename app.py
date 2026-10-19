"""Vocabulary cards on the Zectrix NOTE4.

    Up button (GPIO 39)     next word; a new random word when at the end of the history
    Down button (GPIO 18)   previous word
    Main button tap (GPIO 0) speak the word, then after a short pause its first example sentence
    Main button double tap  the same, slowly (once the API serves slow audio)
    Main button tap while speaking   stop; a second tap right after plays the slow version
    Main button hold        switch between Japanese and Mandarin (each keeps its own history)
    Up button hold          reveal mode on/off (cards show only the headword until "up")
    Down button hold        show the word's next example sentence
    Up + down together      open or close the settings screen (levels, volume, reveal, sleep)

Only one language is shown at a time. Each card shows one example sentence, chosen at random
when the word is fetched; a tap plays the word and that sentence.

Power: on battery, after the "sleep after" setting (default 2 min) of no presses the app turns
Wi-Fi and the codec off and light-sleeps in 100 ms slices, polling the buttons and the charger
pins between slices (the card stays on the e-paper). Any press wakes it; Wi-Fi reconnects in
the background. After POWER_OFF_MIN idle on battery it drops the battery latch (GPIO 17) and
powers off like the stock firmware. On USB power it never sleeps, which also keeps the USB
console alive for deploys.

Responsiveness: a background thread keeps one random word prefetched per language and
downloads the audio for the current and the next word into RAM, and the main loop warms the
glyph cache for the next word while idle, so "up" costs only a partial e-paper refresh (about
0.8 s) and a tap starts speaking at once. Buttons are polled while the panel is busy and while
audio plays, and queued presses are coalesced: several "up" presses during one refresh move
several words with a single refresh.
"""
import _thread
import gc
import random
import sys
import time

import api
import audio
import battery
import buttons
import note4_epd
import settings
import ui
import wifi

LANGUAGES = ("ja", "zh")
START_LANGUAGE = "ja"
POLL_MS = 10
# Words kept per language for the down button. Each is a small JSON object in PSRAM (8 MB);
# the oldest is dropped beyond this. Audio is streamed on each tap and never stored.
HISTORY_LIMIT = 200
PREFETCH_WAIT_MS = 25_000
AUDIO_CACHE_MAX = 16  # WAVs kept in RAM (about 20-50 KB each): word + sentence, normal + slow, current + next

UP, DOWN, SPEAK, TOGGLE = "up", "down", "speak", "toggle"
UP_HOLD, DOWN_HOLD, SETTINGS = "up_hold", "down_hold", "settings"
# Level labels per language until GET /:language/levels has answered.
KNOWN_LEVELS = {"ja": ["N5", "N4", "N3", "N2", "N1"], "zh": ["HSK1", "HSK2", "HSK3.0-1", "HSK2025-1"]}
DOUBLE_TAP_MS = 350  # a second tap within this window of the first makes a double tap
# CPU clock while idle (nothing queued, no prefetch or glyph warming in progress) and while busy.
IDLE_MHZ = 80
BUSY_MHZ = 240
SLEEP_SLICE_MS = 100  # light-sleep length between button polls (wake latency)
POWER_OFF_MIN = 30  # minutes idle on battery before powering off


class App:
    def __init__(self):
        self.epd = note4_epd.EPD()
        self.fonts = {lang: ui.Fonts(lang) for lang in LANGUAGES}
        self.language = START_LANGUAGE
        self.settings = settings.Settings()
        self.levels = {lang: None for lang in LANGUAGES}  # [{"level", "words"}] from the API
        self.mode = "cards"  # or "settings" or "info" (the controls page)
        self.cursor = 0  # selected settings row
        self.revealed = 0  # reveal mode: how much of the current card is showing (ui.REVEAL_STAGES)
        self.expanded = False  # showing the sentence page (whole sentence) instead of the card
        self._trimmed = False  # the card's sentence was shrunk to fit, so a sentence page exists
        self.history = {lang: [] for lang in LANGUAGES}
        self.position = {lang: -1 for lang in LANGUAGES}
        self.speaker = None
        self.speaker_error = None
        self.wlan = None
        self.battery = battery.Battery()
        self.online = False  # Wi-Fi connected, as last seen by the thread or the main loop
        self._status_shown = None  # (charging, full, percent // 10, online) as last drawn
        self._status_seen = None  # the same, as read in the last two checks
        self._status_checked = 0
        self.up = buttons.Button(buttons.PIN_UP)
        self.down = buttons.Button(buttons.PIN_DOWN)
        self.main = buttons.Button(buttons.PIN_MAIN)
        self.pending = []  # button events noticed while busy, handled by the main loop
        self._chord = False  # up and down pressed together; their releases are swallowed
        self._active_at = time.ticks_ms()  # last button event, for the sleep timers
        self._woke_at = None  # end of the last sleep; Wi-Fi gets a grace period before the glyph
        self.sleeping = False
        self.speaking = False  # inside speak(); a main-button tap then cancels instead of queueing
        self._tap_at = None  # ticks of a first tap, waiting DOUBLE_TAP_MS for a second one
        self._cancelled_at = None  # ticks when a tap stopped playback (a quick second tap = slow)
        self.epd.on_wait = self.poll_buttons
        # Prefetch state, shared with the background thread.
        self.prefetched = {lang: None for lang in LANGUAGES}
        self.fetching = {lang: False for lang in LANGUAGES}
        self.fetch_error = None
        self.audio_cache = {}  # audio url -> WAV bytes, filled by the thread
        self.downloading = False  # set by the thread around audio downloads
        self.lock = _thread.allocate_lock()
        self.net_lock = _thread.allocate_lock()  # one HTTPS connection at a time
        self._warming = None  # generator loading glyphs for the next word, run while idle
        self._warmed = set()  # (language, id) of words whose glyphs are cached

    # --- display -----------------------------------------------------------------

    def state(self):
        """What the header shows besides the title: battery and Wi-Fi."""
        return {"battery": self.battery.read(), "online": self.online}

    def message(self, title, lines, footer=None):
        state = self.state()
        self._status_shown = self._status_seen = self._status_key(state)
        ui.draw_message(self.epd, self.fonts[self.language], title, lines, footer, state)
        self.epd.show()
        gc.collect()

    def show(self, status=None, full=False):
        if self.mode == "settings":
            self.show_settings()
            return
        word = self.current()
        if word is None:
            self.message("Language cards", ["No word loaded."], status)
            return
        t = time.ticks_ms()
        state = self.state()
        self._status_shown = self._status_seen = self._status_key(state)
        stage = self.revealed if self.settings["reveal"] else None
        self._trimmed = ui.draw_word(self.epd, self.fonts[self.language], word, self.language,
                                     self.position[self.language], len(self.history[self.language]),
                                     status, state, stage, self.expanded)
        drew = time.ticks_diff(time.ticks_ms(), t)
        kind = self.epd.show(full=full)
        print("drew in %d ms, %s refresh in %d ms" % (drew, kind, time.ticks_diff(time.ticks_ms(), t) - drew))
        gc.collect()

    # --- words -------------------------------------------------------------------

    def current(self):
        h = self.history[self.language]
        return h[self.position[self.language]] if h else None

    def _ensure_online(self):
        """Reconnect Wi-Fi if it dropped. Runs in the thread, so the buttons stay responsive;
        the header's Wi-Fi glyph follows self.online."""
        if self.wlan.isconnected():
            self.online = True
            return True
        self.online = False
        try:
            with self.net_lock:
                wifi.connect(timeout_ms=15_000)
            self.online = True
            print("Wi-Fi: reconnected")
        except OSError as e:
            print("Wi-Fi:", e)
            time.sleep(5)
        return self.online

    def _fetch_levels(self):
        for lang in LANGUAGES:
            if self.levels[lang] is None:
                try:
                    with self.net_lock:
                        self.levels[lang] = api.levels(lang)
                except Exception as e:
                    print("levels:", e)

    @staticmethod
    def prepare(word):
        """Pick the example sentence this card shows (and its tap plays), at random."""
        n = len(word.get("sentences") or [])
        word["sentence"] = random.randrange(n) if n else 0
        return word

    def _prefetch_loop(self):
        while True:
            if not self._ensure_online():
                continue
            self._fetch_levels()
            for lang in (self.language, ui.OTHER[self.language]):
                with self.lock:
                    needed = self.prefetched[lang] is None and not self.fetching[lang]
                    if needed:
                        self.fetching[lang] = True
                if not needed:
                    continue
                try:
                    levels = self.settings.levels(lang)
                    with self.net_lock:
                        word = self.prepare(api.random_word(lang, levels))
                    with self.lock:
                        if self.settings.levels(lang) == levels:  # not changed meanwhile
                            self.prefetched[lang] = word
                        self.fetch_error = None
                except Exception as e:
                    with self.lock:
                        self.fetch_error = e
                    print("prefetch:", e)
                    time.sleep(5)
                finally:
                    with self.lock:
                        self.fetching[lang] = False
            self._prefetch_audio()
            time.sleep_ms(100)

    @staticmethod
    def clips_of(word, slow=False):
        """Audio URLs a tap plays for this word: the word, then the example sentence on screen.
        `audio` is {"normal": {...} | null, "slow": {...} | null} (slow is the provider's own
        0.75x delivery); each is null until generated. With `slow`, the slow versions, as far
        as they exist. Older responses with a flat {"url": ...} are still read."""
        urls = []
        sentences = word.get("sentences") or []
        shown = sentences[word.get("sentence", 0) % len(sentences)] if sentences else None
        for item in (word, shown):
            info = item.get("audio") if item else None
            if not info:
                continue
            if "normal" in info or "slow" in info:
                info = info.get("slow" if slow else "normal")
            elif slow:
                info = None
            if info and info.get("url"):
                urls.append(info["url"])
        return urls

    def _wanted_audio(self):
        word = self.current()
        with self.lock:
            candidates = [word, self.prefetched[self.language]]
        urls = []
        for slow in (False, True):  # normal clips for both words first, then the slow ones
            for w in candidates:
                if w:
                    urls.extend(self.clips_of(w, slow))
        return urls

    def _prefetch_audio(self):
        """Download at most one missing WAV per call, and drop cached ones no longer wanted."""
        wanted = self._wanted_audio()
        for url in wanted:
            if url not in self.audio_cache:
                with self.lock:
                    self.downloading = True
                try:
                    with self.net_lock:
                        data = api.fetch_audio(url)
                    with self.lock:
                        self.audio_cache[url] = data
                    print("audio cached:", len(data), "bytes")
                except Exception as e:
                    print("audio prefetch:", e)
                    time.sleep(5)
                finally:
                    with self.lock:
                        self.downloading = False
                break
        if len(self.audio_cache) > AUDIO_CACHE_MAX:
            with self.lock:
                for url in list(self.audio_cache):
                    if url not in wanted:
                        del self.audio_cache[url]
                        break

    def warm_glyphs(self):
        """Load a few glyphs of the next word into the font caches; call repeatedly while idle."""
        if self._warming is None:
            for lang in (self.language, ui.OTHER[self.language]):
                with self.lock:
                    word = self.prefetched[lang]
                if word is not None and (lang, word.get("id")) not in self._warmed:
                    self._warming = (lang, word.get("id"), self._warm_gen(lang, word))
                    break
            else:
                return
        lang, wid, gen = self._warming
        try:
            next(gen)
        except StopIteration:
            self._warmed.add((lang, wid))
            self._warming = None
            if len(self._warmed) > 50:
                self._warmed.clear()

    def _warm_gen(self, lang, word):
        n = 0
        for font, text in ui.texts_of(self.fonts[lang], word, lang):
            for ch in text:
                font.glyph(ch)
                n += 1
                if n % 8 == 0:
                    yield

    def take_word(self, lang):
        """A prefetched random word, waiting for the thread if one is on its way."""
        start = time.ticks_ms()
        while True:
            with self.lock:
                word = self.prefetched[lang]
                self.prefetched[lang] = None
                busy = self.fetching[lang]
                error = self.fetch_error
            if word is not None:
                return word
            if not busy:
                if error is not None and time.ticks_diff(time.ticks_ms(), start) > 2000:
                    raise api.ApiError(str(error))
                with self.net_lock:
                    return self.prepare(api.random_word(lang, self.settings.levels(lang)))
            if time.ticks_diff(time.ticks_ms(), start) > PREFETCH_WAIT_MS:
                raise api.ApiError("timed out waiting for a word")
            self.poll_buttons()
            time.sleep_ms(20)

    def fetch(self):
        """Append a new random word for the current language and move to it."""
        current = self.current()
        word = self.take_word(self.language)
        if current is not None and word.get("id") == current.get("id"):
            word = self.take_word(self.language)  # the server picked the same word; try once more
        history = self.history[self.language]
        history.append(word)
        if len(history) > HISTORY_LIMIT:
            del history[0]
        self.position[self.language] = len(history) - 1
        print("word:", self.language, word.get("id"), word.get("characters"), "-", word.get("meaning"))

    def move(self, delta):
        """Step delta words (negative = back) with one refresh. Beyond the end of the history,
        new random words are all the same to the user, so only one is fetched."""
        if delta == 0:
            return
        if self.settings["reveal"] and delta > 0 and self.revealed < ui.REVEAL_STAGES[self.language]:
            self.revealed += 1  # "up" on a hidden card reveals the next part (zh: pinyin, then all)
            self.show()
            return
        self.revealed = 0
        self.expanded = False
        pos = self.position[self.language]
        last = len(self.history[self.language]) - 1
        if delta < 0:
            new = max(0, pos + delta)
            if new == pos:
                print("already at the first word")
                return
            self.position[self.language] = new
        elif pos + delta <= last:
            self.position[self.language] = pos + delta
        elif pos < last:
            self.position[self.language] = last
        else:
            self.fetch()
        self.show()

    def next_word(self):
        self.move(1)

    def previous_word(self):
        self.move(-1)

    def next_sentence(self):
        """Hold "down": the sentence page when the card had to trim the sentence, else the
        next example sentence (back on the card)."""
        word = self.current()
        n = len(word.get("sentences") or []) if word else 0
        if not n:
            return
        if self._trimmed and not self.expanded:
            self.expanded = True
        else:
            self.expanded = False
            if n < 2:
                return
            word["sentence"] = (word.get("sentence", 0) + 1) % n
        self.revealed = ui.REVEAL_STAGES[self.language]
        self.show()

    def toggle_reveal(self, on=None):
        self.settings["reveal"] = (not self.settings["reveal"]) if on is None else on
        self.settings.save()
        self.revealed = 0
        print("reveal mode:", self.settings["reveal"])
        self.show("Reveal mode on: ▲ shows the answer" if self.settings["reveal"] else "Reveal mode off")

    def toggle_language(self):
        self.language = ui.OTHER[self.language]
        self.revealed = 0
        self.expanded = False
        print("language:", self.language)
        if not self.history[self.language]:
            self.fetch()
        self.show(full=True)

    def speak(self, slow=False):
        word = self.current()
        if word is None:
            return
        if self.speaker is None:
            self.show("Speaker unavailable: %s" % self.speaker_error)
            return
        urls = self.clips_of(word, slow)
        note = None
        if slow and not urls:
            note = "No slow audio for this word yet"
            urls = self.clips_of(word)
        if not urls:
            self.show("No audio for this word yet")
            return
        self.speaking = True
        try:
            for i, url in enumerate(urls):
                if self.speaker.cancelled:
                    break
                keep = i < len(urls) - 1  # amp stays on through the 0.5 s pause between clips
                with self.lock:
                    data = self.audio_cache.get(url)
                t = time.ticks_ms()
                if data is not None:
                    n = self.speaker.play_wav_bytes(data, keep_amp=keep)
                    print("played", n, "bytes from RAM in", time.ticks_diff(time.ticks_ms(), t), "ms")
                    continue
                with self.net_lock:
                    body, length, response = api.open_audio(url)
                    try:
                        n = self.speaker.play_wav_stream(body, length, keep_amp=keep)
                        print("streamed", n, "bytes in", time.ticks_diff(time.ticks_ms(), t), "ms")
                    finally:
                        response.close()
        finally:
            self.speaking = False
            self.speaker.amp_off()
        if note:
            self.show(note)

    # --- header status (battery, Wi-Fi) --------------------------------------------

    @staticmethod
    def _status_key(state):
        b = state["battery"]
        return (b["charging"], b["full"], b["percent"] // 10, state["online"])

    def check_status(self):
        """Every 500 ms, read the battery and Wi-Fi state; redraw the card when the USB state,
        the 10 % step or the connection has differed from what is shown for two reads (1 s)."""
        now = time.ticks_ms()
        if time.ticks_diff(now, self._status_checked) < 500:
            return
        self._status_checked = now
        key = self._status_key(self.state())
        if (not self.online and self._woke_at is not None and self._status_shown is not None
                and time.ticks_diff(now, self._woke_at) < 20_000):
            key = key[:3] + (self._status_shown[3],)  # reconnecting after sleep: no cross yet
        if key == self._status_shown:
            self._status_seen = key
            return
        if key == self._status_seen:
            print("status changed:", key)
            self.handle(self.show)
        self._status_seen = key

    # --- buttons -----------------------------------------------------------------

    def poll_buttons(self):
        """Read the buttons once and queue any event. Safe to call from any wait loop."""
        up = self.up.poll()
        down = self.down.poll()
        main = self.main.poll()
        if self.up.pressed() and self.down.pressed():
            if not self._chord:
                self._chord = True
                self.pending.append(SETTINGS)
            up = down = None
        elif self._chord:
            up = down = None  # the releases of a chord are not presses
            if not self.up.pressed() and not self.down.pressed():
                self._chord = False
        if up == buttons.TAP:
            self.pending.append(UP)
        elif up == buttons.HOLD:
            self.pending.append(UP_HOLD)
        if down == buttons.TAP:
            self.pending.append(DOWN)
        elif down == buttons.HOLD:
            self.pending.append(DOWN_HOLD)
        if main == buttons.TAP:
            if self.speaking and self.speaker is not None:
                self.speaker.cancel()
                self._cancelled_at = time.ticks_ms()
                print("speak: cancelled")
            else:
                self.pending.append(SPEAK)
        elif main == buttons.HOLD:
            self.pending.append(TOGGLE)

    def process_pending(self):
        """Handle queued events in order, merging runs of up/down into one move."""
        self._active_at = time.ticks_ms()
        while self.pending:
            event = self.pending.pop(0)
            if self.mode == "info":
                if event == SETTINGS:
                    self.handle(self.close_settings)
                else:
                    self.mode = "settings"
                    self.handle(self.show_settings)
            elif self.mode == "settings":
                self.handle(lambda: self.settings_event(event))
            elif event in (UP, DOWN):
                delta = 1 if event == UP else -1
                while self.pending and self.pending[0] in (UP, DOWN) and not self.settings["reveal"]:
                    delta += 1 if self.pending.pop(0) == UP else -1
                self.handle(lambda: self.move(delta))
            elif event == UP_HOLD:
                self.handle(self.toggle_reveal)
            elif event == DOWN_HOLD:
                self.handle(self.next_sentence)
            elif event == SETTINGS:
                self.handle(self.open_settings)
            elif event == SPEAK:
                now = time.ticks_ms()
                if self._tap_at is not None or (
                        self._cancelled_at is not None and time.ticks_diff(now, self._cancelled_at) < DOUBLE_TAP_MS):
                    self._tap_at = self._cancelled_at = None
                    self.handle(lambda: self.speak(slow=True))
                else:
                    self._tap_at = now  # wait for a possible second tap
            elif event == TOGGLE:
                self.handle(self.toggle_language)

    # --- settings screen ---------------------------------------------------------

    def known_levels(self, lang):
        known = self.levels[lang]
        if known is None:
            known = [{"level": name, "words": None} for name in KNOWN_LEVELS[lang]]
        return known

    def settings_rows(self):
        """[(key, label, value, checked)] for ui.draw_settings, in display order. Only the
        current language's levels are listed; with no filter saved they all show as checked."""
        st = self.settings
        lang = self.language
        rows = [(("info",), "Controls", "open", None),
                (("volume",), "Volume", "%d %%" % st["volume"], None),
                (("reveal",), "Reveal mode", "on" if st["reveal"] else "off", None),
                (("sleep",), "Sleep after", ("%d min" % st["sleep_min"]) if st["sleep_min"] else "never", None),
                (None, "Word levels  (current language: %s)" % ui.LANGUAGE_NAME_EN[lang],
                 None, None)]
        chosen = st.levels(lang)
        for item in self.known_levels(lang):
            name = item["level"]
            label = name
            if item.get("words"):
                label += "  (%d words)" % item["words"]
            rows.append((("level", lang, name), label, None, not chosen or name in chosen))
        return rows

    def toggle_level(self, lang, name):
        """Uncheck or check one level. No saved filter means every level; unchecking one from
        there keeps the others, and checking the last one back clears the filter again."""
        known = [item["level"] for item in self.known_levels(lang)]
        chosen = self.settings.levels(lang) or list(known)
        if name in chosen:
            chosen.remove(name)
        else:
            chosen.append(name)
        if not chosen or all(level in chosen for level in known):
            chosen = []
        self.settings["levels"][lang] = chosen

    HINTS = {"volume": "● louder  hold ● quieter", "sleep": "● longer  hold ● shorter",
             "reveal": "● on / off", "level": "● check / uncheck", "info": "● open"}

    def show_info(self):
        state = self.state()
        self._status_shown = self._status_seen = self._status_key(state)
        ui.draw_info(self.epd, self.fonts[self.language], state)
        self.epd.show()
        gc.collect()

    def show_settings(self):
        all_rows = self.settings_rows()
        rows = [(label, value, checked) for _, label, value, checked in all_rows]
        key = all_rows[self.cursor][0]
        hint = self.HINTS.get(key[0] if key else None, "● change")
        state = self.state()
        self._status_shown = self._status_seen = self._status_key(state)
        ui.draw_settings(self.epd, self.fonts[self.language], rows, self.cursor, state, hint)
        self.epd.show()
        gc.collect()

    def open_settings(self):
        self.mode = "settings"
        self.cursor = 0
        self._levels_before = {lang: self.settings.levels(lang) for lang in LANGUAGES}
        self.show_settings()

    def close_settings(self):
        self.settings.save()
        self.mode = "cards"
        for lang in LANGUAGES:
            if self.settings.levels(lang) != self._levels_before[lang]:
                with self.lock:
                    self.prefetched[lang] = None  # fetched under the old filter
                print("levels:", lang, self.settings.levels(lang) or "all")
        self.show(full=True)

    def settings_event(self, event):
        rows = self.settings_rows()
        if event == SETTINGS:
            self.close_settings()
            return
        if event in (UP, DOWN):
            step = -1 if event == UP else 1
            self.cursor = (self.cursor + step) % len(rows)
            while rows[self.cursor][0] is None:  # skip headings
                self.cursor = (self.cursor + step) % len(rows)
            self.show_settings()
            return
        if event not in (SPEAK, TOGGLE):
            return
        direction = 1 if event == SPEAK else -1
        key = rows[self.cursor][0]
        if key[0] == "info":
            self.mode = "info"
            self.show_info()
            return
        if key[0] == "volume":
            v = self.settings.step_volume(direction)
            if self.speaker is not None:
                self.speaker.set_volume(v)
        elif key[0] == "reveal":
            self.settings["reveal"] = not self.settings["reveal"]
            self.revealed = 0
        elif key[0] == "sleep":
            self.settings.step_sleep(direction)
        elif key[0] == "level":
            self.toggle_level(key[1], key[2])
        self.show_settings()
        if key[0] == "volume" and self.speaker is not None and self.settings["volume"]:
            self.speaker.beep(ms=120)

    # --- power -------------------------------------------------------------------

    def idle_ms(self):
        return time.ticks_diff(time.ticks_ms(), self._active_at)

    def maybe_sleep(self):
        """Light-sleep once idle for the "sleep after" setting, on battery only."""
        minutes = self.settings["sleep_min"]
        if not minutes or self.pending or self._tap_at is not None or self.speaking:
            return
        if self.idle_ms() < minutes * 60_000:
            return
        if self.battery.read()["powered"]:
            self._active_at = time.ticks_ms()  # on USB: check again in a while
            return
        with self.lock:
            busy = any(self.fetching.values()) or self.downloading
        if not busy:
            self.sleep()

    def sleep(self):
        import machine
        print("sleep: idle for %d s on battery" % (self.idle_ms() // 1000))
        self.net_lock.acquire()  # waits for the thread's request, then keeps it off the network
        self.sleeping = True
        try:
            if self.speaker is not None:
                self.speaker.deinit()
                self.speaker = None
            if self.wlan is not None:
                self.wlan.active(False)
            self.online = False
            self._status_seen = self._status_shown  # the Wi-Fi drop is not worth a refresh
            detect = self.battery.detect()
            full = self.battery.full()
            while True:
                machine.lightsleep(SLEEP_SLICE_MS)
                if self.up.pressed() or self.down.pressed() or self.main.pressed():
                    break
                if self.battery.detect() != detect or self.battery.full() != full:
                    print("wake: charger changed")
                    break
                if self.idle_ms() > POWER_OFF_MIN * 60_000:
                    self.power_off()
        finally:
            self.sleeping = False
            self._woke_at = self._active_at = time.ticks_ms()
            self.net_lock.release()  # the thread reconnects Wi-Fi
        print("wake")
        self.init_speaker()

    def power_off(self):
        """Drop the battery latch, as the stock firmware does after five idle minutes. Only
        returns if the unit stays powered (USB plugged in meanwhile), then restarts the app."""
        import machine
        from machine import Pin
        print("power off: idle for %d min" % POWER_OFF_MIN)
        self.mode = "cards"
        self.message("Language cards", ["Powered off after %d idle minutes." % POWER_OFF_MIN,
                                        "Hold ● to turn on."])
        Pin(audio.PIN_AUDIO_PWR, Pin.OUT, value=0, hold=True)
        Pin(note4_epd.PIN_VBAT_PWR, Pin.OUT, value=0, hold=True)
        time.sleep(3)
        machine.reset()

    # --- lifecycle ---------------------------------------------------------------

    def connect(self):
        self.message("Language cards", ["Connecting to Wi-Fi…"])
        while True:
            try:
                self.wlan = wifi.connect()
                self.online = True
                return
            except OSError as e:
                print("Wi-Fi:", e)
                self.message("Language cards", ["Wi-Fi failed:", str(e), "Retrying in 15 s…"])
                time.sleep(15)

    def init_speaker(self):
        try:
            self.speaker = audio.Speaker(volume=self.settings["volume"])
            self.speaker.on_chunk = self.poll_buttons
        except Exception as e:
            sys.print_exception(e)
            self.speaker_error = str(e)

    def start(self):
        self.connect()
        self.init_speaker()
        _thread.stack_size(24 * 1024)  # HTTPS needs more than the default thread stack
        _thread.start_new_thread(self._prefetch_loop, ())
        while self.current() is None:
            try:
                self.fetch()
            except api.ApiError as e:
                print("API:", e)
                self.message("Language cards", ["Could not load a word:", str(e), "Retrying in 15 s…"])
                time.sleep(15)
        self.show()

    def handle(self, action):
        try:
            action()
        except api.ApiError as e:
            print("API:", e)
            self.show("Error: " + str(e))
        except Exception as e:
            sys.print_exception(e)
            self.show("Error: " + str(e))

    def run(self):
        self.start()
        print("ready: up = next, down = back, tap = speak, hold = switch language")
        import machine
        mhz = 0
        while True:
            self.poll_buttons()
            if self.pending:
                self.process_pending()
            elif self._tap_at is not None and time.ticks_diff(time.ticks_ms(), self._tap_at) >= DOUBLE_TAP_MS:
                self._tap_at = None
                self.handle(self.speak)
            else:
                self.warm_glyphs()
                self.check_status()
                self.maybe_sleep()
            # Slow the CPU down while there is nothing to do; the prefetch thread and glyph
            # warming run at full speed so they finish quickly.
            with self.lock:
                thread_busy = any(self.fetching.values()) or self.downloading
            want = BUSY_MHZ if (self.pending or self._tap_at is not None or self._warming or thread_busy) else IDLE_MHZ
            if want != mhz:
                machine.freq(want * 1_000_000)
                mhz = want
            time.sleep_ms(POLL_MS)


def main():
    App().run()
