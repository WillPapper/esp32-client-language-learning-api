"""Vocabulary cards on the Zectrix NOTE4.

    Up button (GPIO 39)     next word; fetches a new random word when at the end of the history
    Down button (GPIO 18)   previous word
    Main button tap (GPIO 0) speak the word (the API's text-to-speech WAV)
    Main button hold        switch between Japanese and Mandarin (each keeps its own history)

Only one language is shown at a time.
"""
import gc
import sys
import time

import api
import buttons
import note4_epd
import ui
import wifi

LANGUAGES = ("ja", "zh")
START_LANGUAGE = "ja"
POLL_MS = 10


class App:
    def __init__(self):
        self.epd = note4_epd.EPD()
        self.fonts = {lang: ui.Fonts(lang) for lang in LANGUAGES}
        self.language = START_LANGUAGE
        self.history = {lang: [] for lang in LANGUAGES}
        self.position = {lang: -1 for lang in LANGUAGES}
        self.speaker = None
        self.speaker_error = None
        self.up = buttons.Button(buttons.PIN_UP)
        self.down = buttons.Button(buttons.PIN_DOWN)
        self.main = buttons.Button(buttons.PIN_MAIN)

    # --- display -----------------------------------------------------------------

    def message(self, title, lines, footer=None):
        ui.draw_message(self.epd, self.fonts[self.language], title, lines, footer)
        self.epd.show()
        gc.collect()

    def show(self, status=None):
        word = self.current()
        if word is None:
            self.message("Language cards", ["No word loaded."], status)
            return
        ui.draw_word(self.epd, self.fonts[self.language], word, self.language,
                     self.position[self.language], len(self.history[self.language]), status)
        self.epd.show()
        gc.collect()

    # --- words -------------------------------------------------------------------

    def current(self):
        h = self.history[self.language]
        return h[self.position[self.language]] if h else None

    def fetch(self):
        """Append a new random word for the current language and move to it."""
        current = self.current()
        word = api.random_word(self.language)
        if current is not None and word.get("id") == current.get("id"):
            word = api.random_word(self.language)  # the server picked the same word; try once more
        self.history[self.language].append(word)
        self.position[self.language] = len(self.history[self.language]) - 1
        print("word:", self.language, word.get("id"), word.get("characters"), "-", word.get("meaning"))

    def next_word(self):
        if self.position[self.language] < len(self.history[self.language]) - 1:
            self.position[self.language] += 1
        else:
            self.fetch()
        self.show()

    def previous_word(self):
        if self.position[self.language] > 0:
            self.position[self.language] -= 1
            self.show()
        else:
            print("already at the first word")

    def toggle_language(self):
        self.language = ui.OTHER[self.language]
        print("language:", self.language)
        if not self.history[self.language]:
            self.fetch()
        self.show()

    def speak(self):
        word = self.current()
        if word is None:
            return
        if self.speaker is None:
            self.show("Speaker unavailable: %s" % self.speaker_error)
            return
        info = word.get("audio")
        if not info:
            self.show("No audio for this word yet")
            return
        body, length, response = api.open_audio(info["url"])
        try:
            t = time.ticks_ms()
            n = self.speaker.play_wav_stream(body, length)
            print("played", n, "bytes in", time.ticks_diff(time.ticks_ms(), t), "ms")
        finally:
            response.close()

    # --- lifecycle ---------------------------------------------------------------

    def connect(self):
        self.message("Language cards", ["Connecting to Wi-Fi…"])
        while True:
            try:
                wifi.connect()
                return
            except OSError as e:
                print("Wi-Fi:", e)
                self.message("Language cards", ["Wi-Fi failed:", str(e), "Retrying in 15 s…"])
                time.sleep(15)

    def init_speaker(self):
        try:
            import audio
            self.speaker = audio.Speaker()
        except Exception as e:
            sys.print_exception(e)
            self.speaker_error = str(e)

    def start(self):
        self.connect()
        self.init_speaker()
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
        while True:
            up = self.up.poll()
            down = self.down.poll()
            main = self.main.poll()
            if up == buttons.TAP or up == buttons.HOLD:
                self.handle(self.next_word)
            if down == buttons.TAP or down == buttons.HOLD:
                self.handle(self.previous_word)
            if main == buttons.TAP:
                self.handle(self.speak)
            elif main == buttons.HOLD:
                self.handle(self.toggle_language)
            time.sleep_ms(POLL_MS)


def main():
    App().run()
