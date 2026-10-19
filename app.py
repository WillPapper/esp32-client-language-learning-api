"""Vocabulary cards on the Zectrix NOTE4.

    Up button (GPIO 39)     next word; a new random word when at the end of the history
    Down button (GPIO 18)   previous word
    Main button tap (GPIO 0) speak the word, then after a short pause its first example sentence
    Main button double tap  the same, slowly (once the API serves slow audio)
    Main button tap while speaking   stop; a second tap right after plays the slow version
    Main button hold        switch between Japanese and Mandarin (each keeps its own history)

Only one language is shown at a time.

Responsiveness: a background thread keeps one random word prefetched per language and
downloads the audio for the current and the next word into RAM, and the main loop warms the
glyph cache for the next word while idle, so "up" costs only a partial e-paper refresh (about
0.8 s) and a tap starts speaking at once. Buttons are polled while the panel is busy and while
audio plays, and queued presses are coalesced: several "up" presses during one refresh move
several words with a single refresh.
"""
import _thread
import gc
import sys
import time

import api
import battery
import buttons
import note4_epd
import ui
import wifi

LANGUAGES = ("ja", "zh")
START_LANGUAGE = "ja"
POLL_MS = 10
# Words kept per language for the down button. Each is a small JSON object in PSRAM (8 MB);
# the oldest is dropped beyond this. Audio is streamed on each tap and never stored.
HISTORY_LIMIT = 200
PREFETCH_WAIT_MS = 25_000
AUDIO_CACHE_MAX = 10  # WAVs kept in RAM (about 20-50 KB each): word + sentence, current + next

UP, DOWN, SPEAK, TOGGLE = "up", "down", "speak", "toggle"
DOUBLE_TAP_MS = 350  # a second tap within this window of the first makes a double tap
# CPU clock while idle (nothing queued, no prefetch or glyph warming in progress) and while busy.
IDLE_MHZ = 80
BUSY_MHZ = 240


class App:
    def __init__(self):
        self.epd = note4_epd.EPD()
        self.fonts = {lang: ui.Fonts(lang) for lang in LANGUAGES}
        self.language = START_LANGUAGE
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
        word = self.current()
        if word is None:
            self.message("Language cards", ["No word loaded."], status)
            return
        t = time.ticks_ms()
        state = self.state()
        self._status_shown = self._status_seen = self._status_key(state)
        ui.draw_word(self.epd, self.fonts[self.language], word, self.language,
                     self.position[self.language], len(self.history[self.language]), status, state)
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

    def _prefetch_loop(self):
        while True:
            if not self._ensure_online():
                continue
            for lang in (self.language, ui.OTHER[self.language]):
                with self.lock:
                    needed = self.prefetched[lang] is None and not self.fetching[lang]
                    if needed:
                        self.fetching[lang] = True
                if not needed:
                    continue
                try:
                    with self.net_lock:
                        word = api.random_word(lang)
                    with self.lock:
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
        """Audio URLs a tap plays for this word: the word, then its first example sentence.
        With `slow`, the slow versions, where the API provides them (`audio.slowUrl`; the
        endpoint is still being added), else an empty list."""
        urls = []
        sentences = word.get("sentences") or []
        for item in (word, sentences[0] if sentences else None):
            info = item.get("audio") if item else None
            if not info:
                continue
            url = info.get("slowUrl") if slow else info.get("url")
            if url:
                urls.append(url)
        return urls

    def _wanted_audio(self):
        word = self.current()
        with self.lock:
            candidates = [word, self.prefetched[self.language]]
        urls = []
        for w in candidates:
            if w:
                urls.extend(self.clips_of(w))
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
                    return api.random_word(lang)
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

    def toggle_language(self):
        self.language = ui.OTHER[self.language]
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
            note = "Slow audio is not available yet"
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
        if up:
            self.pending.append(UP)
        if down:
            self.pending.append(DOWN)
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
        while self.pending:
            event = self.pending.pop(0)
            if event in (UP, DOWN):
                delta = 1 if event == UP else -1
                while self.pending and self.pending[0] in (UP, DOWN):
                    delta += 1 if self.pending.pop(0) == UP else -1
                self.handle(lambda: self.move(delta))
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
            import audio
            self.speaker = audio.Speaker()
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
