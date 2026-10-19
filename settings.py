"""User settings, kept in settings.json on the device's flash so they survive resets.

    levels     per language, the level labels to draw words from ([] = every word)
    volume     speaker volume, 0-100
    reveal     reveal mode: cards show only the headword until "up" is pressed
    sleep_min  minutes of inactivity before light sleep (0 = never)
"""
import json

FILE = "settings.json"
DEFAULTS = {"levels": {"ja": [], "zh": []}, "volume": 100, "reveal": False, "sleep_min": 2}
SLEEP_CHOICES = (1, 2, 5, 10, 30, 0)
VOLUME_STEP = 10


class Settings:
    def __init__(self, path=FILE):
        self.path = path
        self.data = json.loads(json.dumps(DEFAULTS))  # a deep copy
        self.load()

    def load(self):
        try:
            with open(self.path) as f:
                saved = json.load(f)
        except (OSError, ValueError):
            return
        for key in DEFAULTS:
            if key in saved:
                self.data[key] = saved[key]
        for lang in DEFAULTS["levels"]:
            self.data["levels"].setdefault(lang, [])

    def save(self):
        try:
            with open(self.path, "w") as f:
                json.dump(self.data, f)
        except OSError as e:
            print("settings: could not save:", e)

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value

    def levels(self, language):
        return list(self.data["levels"].get(language) or [])

    def step_volume(self, direction):
        v = self.data["volume"] + VOLUME_STEP * direction
        self.data["volume"] = max(0, min(100, v))
        return self.data["volume"]

    def step_sleep(self, direction):
        choices = SLEEP_CHOICES
        try:
            i = choices.index(self.data["sleep_min"])
        except ValueError:
            i = 0
        self.data["sleep_min"] = choices[(i + direction) % len(choices)]
        return self.data["sleep_min"]
