"""Client for the language-learning-api Worker (see ../language-learning-api/README.md).

Only the public, read-only endpoints are used:
    GET /:language/word                 random word with example sentences (the server randomizes);
                                        ?level=N5,N4 limits it to words on any of those levels
    GET /:language/levels               level labels in use, with word counts
    GET /:language/words/:id/audio      16 kHz 16-bit mono WAV, 404 until generated; ?mode=slow
    GET /:language/sentences/:id/audio  same for a sentence
The client never builds audio URLs itself: it uses audio.normal.url / audio.slow.url from the
word JSON.
"""
import requests

BASE_URL = "https://language-learning-api.papper.workers.dev"
TIMEOUT = 20  # seconds, per socket operation

LANGUAGES = ("ja", "zh")


class ApiError(Exception):
    pass


def _get(path):
    # `audio.url` fields are absolute URLs; paths like "/ja/word" are relative to BASE_URL.
    url = path if path.startswith("http") else BASE_URL + path
    try:
        return requests.get(url, headers={"Accept": "application/json, audio/wav"}, timeout=TIMEOUT)
    except OSError as e:
        raise ApiError("network error: %s" % e)


def random_word(language, levels=None):
    """A random word in `language` ("ja" or "zh"), as the API's JSON object. `levels` limits it
    to words on any of those level labels (e.g. ["N5", "N4"]); empty or None means every word."""
    path = "/%s/word" % language
    if levels:
        path += "?level=" + ",".join(levels)
    r = _get(path)
    try:
        if r.status_code == 404:
            raise ApiError("no words on levels %s" % ",".join(levels or []))
        if r.status_code != 200:
            raise ApiError("GET %s returned %d" % (path, r.status_code))
        return r.json()
    finally:
        r.close()


def levels(language):
    """[{"level": "N5", "words": 24}, ...] for the language."""
    r = _get("/%s/levels" % language)
    try:
        if r.status_code != 200:
            raise ApiError("GET /%s/levels returned %d" % (language, r.status_code))
        return r.json().get("levels") or []
    finally:
        r.close()


class _Body:
    """Adapts the response body to readinto(), which audio.Speaker.play_wav_stream() needs."""

    def __init__(self, raw):
        self.raw = raw

    def readinto(self, buf):
        if hasattr(self.raw, "readinto"):
            return self.raw.readinto(buf)
        data = self.raw.read(len(buf))
        n = len(data)
        buf[:n] = data
        return n


def open_audio(url):
    """Open a WAV stream for an `audio.url` from a word or sentence.

    Returns (body, length, response). Read the body with readinto(); close the response after.
    Raises ApiError for a 404 (no audio generated yet) or any other failure.
    """
    r = _get(url)
    if r.status_code == 404:
        r.close()
        raise ApiError("no audio generated yet")
    if r.status_code != 200:
        r.close()
        raise ApiError("GET %s returned %d" % (url, r.status_code))
    length = None
    try:
        length = int(r.headers.get("Content-Length", 0)) or None
    except (AttributeError, ValueError):
        pass
    return _Body(r.raw), length, r


def fetch_audio(url):
    """The whole WAV for an `audio.url`, as bytes (about 20-50 KB for a word)."""
    r = _get(url)
    try:
        if r.status_code == 404:
            raise ApiError("no audio generated yet")
        if r.status_code != 200:
            raise ApiError("GET %s returned %d" % (url, r.status_code))
        return r.content
    finally:
        r.close()
