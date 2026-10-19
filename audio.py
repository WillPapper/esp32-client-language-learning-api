"""Speaker output for the Zectrix NOTE4: ES8311 codec on I2C, PCM over I2S, class-D amp enable.

Pins are from config.h in Zectrix's board package (xiaozhi-esp32). GPIO 42 powers the codec
and GPIO 46 enables the amplifier; the stock firmware pad-holds both, so `hold=` is passed
explicitly (a leftover hold otherwise makes writes silently no-ops).

The API serves 16 kHz, 16-bit, mono WAV. MicroPython's I2S cannot output MCLK, so the codec
derives its master clock from the bit clock; to make that clock fast enough (1.024 MHz) the
I2S bus runs 32-bit mono slots and each 16-bit sample is widened to 32 bits in `_widen()`.
"""
import micropython
import struct
import time
from machine import I2C, I2S, Pin

import es8311

PIN_I2C_SDA = 47
PIN_I2C_SCL = 48
PIN_I2S_BCLK = 15
PIN_I2S_WS = 38
PIN_I2S_DOUT = 45  # ESP32 -> codec DAC
PIN_AUDIO_PWR = 42  # high = codec powered
PIN_AMP_EN = 46  # high = speaker amplifier on

RATE = 16000
CHUNK = 2048  # bytes of 16-bit PCM per I2S write (64 ms)
IBUF = 32768  # I2S DMA buffer, in bytes of 32-bit samples (0.5 s)


@micropython.viper
def _widen(src: ptr8, dst: ptr8, n: int):
    # 16-bit little-endian samples -> 32-bit left-aligned samples (n = source bytes)
    i = 0
    j = 0
    while i < n:
        dst[j] = 0
        dst[j + 1] = 0
        dst[j + 2] = src[i]
        dst[j + 3] = src[i + 1]
        i += 2
        j += 4


class WavFormatError(ValueError):
    pass


def parse_wav_header(header):
    """Check a 44-byte canonical WAV header. Returns (rate, channels, bits, data_bytes)."""
    if len(header) < 44 or header[:4] != b"RIFF" or header[8:12] != b"WAVE" or header[12:16] != b"fmt ":
        raise WavFormatError("not a canonical WAV file")
    fmt, channels, rate, _, _, bits = struct.unpack_from("<HHIIHH", header, 20)
    if fmt != 1:
        raise WavFormatError("not PCM")
    if header[36:40] != b"data":
        raise WavFormatError("unexpected chunk before data")
    data_bytes = struct.unpack_from("<I", header, 40)[0]
    return rate, channels, bits, data_bytes


class Speaker:
    def __init__(self, volume=75):
        Pin(PIN_AUDIO_PWR, Pin.OUT, value=1, hold=True)
        self.amp = Pin(PIN_AMP_EN, Pin.OUT, value=0, hold=False)
        time.sleep_ms(20)
        self.i2c = I2C(0, sda=Pin(PIN_I2C_SDA), scl=Pin(PIN_I2C_SCL), freq=400_000)
        self.codec = es8311.ES8311(self.i2c)
        self.codec.init(rate=RATE, bits=32)
        self.volume = volume
        self.codec.set_volume(volume)
        self.i2s = None
        self._in = bytearray(CHUNK)
        self._out = bytearray(CHUNK * 2)

    def set_volume(self, percent):
        self.volume = percent
        self.codec.set_volume(percent)

    def _start(self):
        self.i2s = I2S(0, sck=Pin(PIN_I2S_BCLK), ws=Pin(PIN_I2S_WS), sd=Pin(PIN_I2S_DOUT),
                       mode=I2S.TX, bits=32, format=I2S.MONO, rate=RATE, ibuf=IBUF)
        self.amp(1)

    def _stop(self):
        # Push half a second of silence so the last real samples leave the DMA buffer
        # before the amplifier is switched off.
        self.i2s.write(bytearray(IBUF))
        self.amp(0)
        self.i2s.deinit()
        self.i2s = None

    def play_pcm(self, read):
        """Play 16-bit mono PCM at 16 kHz. `read(buf)` fills buf and returns the byte count, 0 at end."""
        self._start()
        total = 0
        try:
            while True:
                n = read(self._in)
                if not n:
                    break
                if n & 1:  # odd byte count: drop the half sample
                    n -= 1
                _widen(self._in, self._out, n)
                self.i2s.write(memoryview(self._out)[: n * 2])
                total += n
        finally:
            self._stop()
        return total

    def play_wav_stream(self, stream, length=None):
        """Play a WAV from a file-like object with `readinto()` (a file or an HTTP body)."""
        header = bytearray(44)
        got = 0
        while got < 44:
            n = stream.readinto(memoryview(header)[got:])
            if not n:
                raise WavFormatError("short WAV header")
            got += n
        rate, channels, bits, data_bytes = parse_wav_header(header)
        if rate != RATE or channels != 1 or bits != 16:
            raise WavFormatError("need 16 kHz 16-bit mono, got %d Hz %d ch %d bit" % (rate, channels, bits))
        if length is not None:
            data_bytes = min(data_bytes, length - 44)
        remaining = [data_bytes]

        def read(buf):
            if remaining[0] <= 0:
                return 0
            want = min(len(buf), remaining[0])
            n = stream.readinto(memoryview(buf)[:want])
            if n:
                remaining[0] -= n
            return n or 0

        return self.play_pcm(read)

    def beep(self, freq=880, ms=200, amplitude=8000):
        """Short sine tone, for checking the speaker without the network."""
        import math
        samples = RATE // freq
        wave = bytearray(samples * 2)
        for i in range(samples):
            struct.pack_into("<h", wave, i * 2, int(amplitude * math.sin(2 * math.pi * i / samples)))
        total = RATE * 2 * ms // 1000
        sent = [0]

        def read(buf):
            n = min(len(buf) // len(wave) * len(wave), total - sent[0])
            if n <= 0:
                return 0
            for k in range(0, n, len(wave)):
                buf[k:k + len(wave)] = wave
            sent[0] += n
            return n

        self.play_pcm(read)
