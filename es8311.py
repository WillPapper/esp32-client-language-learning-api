"""Minimal MicroPython driver for the Everest ES8311 audio codec (playback only).

Register sequence ported from Espressif's es8311 component (esp-bsp, Apache-2.0):
es8311_init(), es8311_clock_config(), es8311_sample_frequency_config(), es8311_fmt_config()
and es8311_voice_volume_set(). Only what the NOTE4 needs is kept: the codec is an I2S
slave, takes its master clock from the bit clock (MicroPython's I2S has no MCLK output), and
drives the speaker amplifier through the headphone output.
"""
import time

ADDR = 0x18

# Clock coefficients from the Espressif table, keyed by (bit clock Hz, sample rate):
# (pre_div, pre_multi, adc_div, dac_div, fs_mode, lrck_h, lrck_l, bclk_div, adc_osr, dac_osr).
# With MCLK taken from the bit clock, mclk = rate * bits * 2 (two slots per frame).
_COEFF = {
    (1024000, 16000): (1, 2, 1, 1, 0, 0x00, 0xFF, 4, 0x10, 0x10),  # 32-bit slots at 16 kHz
    (2048000, 16000): (1, 1, 1, 1, 0, 0x00, 0xFF, 4, 0x10, 0x10),
    (1536000, 24000): (1, 2, 1, 1, 0, 0x00, 0xFF, 4, 0x10, 0x10),
    (1024000, 32000): (1, 3, 1, 1, 0, 0x00, 0xFF, 4, 0x10, 0x10),
    (2048000, 32000): (1, 2, 1, 1, 0, 0x00, 0xFF, 4, 0x10, 0x10),
    (1411200, 44100): (1, 3, 1, 1, 0, 0x00, 0xFF, 4, 0x10, 0x10),
    (2822400, 44100): (1, 2, 1, 1, 0, 0x00, 0xFF, 4, 0x10, 0x10),
    (1536000, 48000): (1, 3, 1, 1, 0, 0x00, 0xFF, 4, 0x10, 0x10),
    (3072000, 48000): (1, 2, 1, 1, 0, 0x00, 0xFF, 4, 0x10, 0x10),
}

_RES = {16: 3, 18: 2, 20: 1, 24: 0, 32: 4}  # SDP resolution field (bits 4:2 of 0x09 / 0x0A)


class ES8311:
    def __init__(self, i2c, addr=ADDR):
        self.i2c = i2c
        self.addr = addr
        self._buf = bytearray(1)

    def write(self, reg, value):
        self.i2c.writeto_mem(self.addr, reg, bytes((value & 0xFF,)))

    def read(self, reg):
        self.i2c.readfrom_mem_into(self.addr, reg, self._buf)
        return self._buf[0]

    def init(self, rate=16000, bits=32):
        """Reset the codec and set it up as an I2S slave at `rate`, with `bits` per slot."""
        bclk = rate * bits * 2
        try:
            (pre_div, pre_multi, adc_div, dac_div, fs_mode,
             lrck_h, lrck_l, bclk_div, adc_osr, dac_osr) = _COEFF[(bclk, rate)]
        except KeyError:
            raise ValueError("no clock coefficients for %d Hz at %d bits" % (rate, bits))
        res = _RES[bits]

        # Reset, then power on
        self.write(0x00, 0x1F)
        time.sleep_ms(20)
        self.write(0x00, 0x00)
        self.write(0x00, 0x80)

        # Clock manager: all clocks on, MCLK taken from the SCLK (bit clock) pin
        self.write(0x01, 0x3F | 0x80)
        self.write(0x06, self.read(0x06) & ~0x20)  # SCLK not inverted

        # Dividers for this bit clock / sample rate
        self.write(0x02, (self.read(0x02) & 0x07) | ((pre_div - 1) << 5) | (pre_multi << 3))
        self.write(0x03, (fs_mode << 6) | adc_osr)
        self.write(0x04, dac_osr)
        self.write(0x05, ((adc_div - 1) << 4) | (dac_div - 1))
        self.write(0x06, (self.read(0x06) & 0xE0) | ((bclk_div - 1) if bclk_div < 19 else bclk_div))
        self.write(0x07, (self.read(0x07) & 0xC0) | lrck_h)
        self.write(0x08, lrck_l)

        # Serial port: slave, I2S format, resolution in and out
        self.write(0x00, self.read(0x00) & 0xBF)
        self.write(0x09, res << 2)
        self.write(0x0A, res << 2)

        # Analog power-up, DAC on, headphone drive on, equalizers bypassed
        self.write(0x0D, 0x01)
        self.write(0x0E, 0x02)
        self.write(0x12, 0x00)
        self.write(0x13, 0x10)
        self.write(0x1C, 0x6A)
        self.write(0x37, 0x08)

    def set_volume(self, percent):
        """DAC volume, 0-100 (same mapping as Espressif's driver)."""
        percent = max(0, min(100, percent))
        self.write(0x32, 0 if percent == 0 else percent * 256 // 100 - 1)

    def mute(self, on=True):
        reg = self.read(0x31)
        self.write(0x31, (reg | 0x60) if on else (reg & ~0x60))

    def standby(self):
        """Power the analog and digital blocks down (same registers as power-up, reversed)."""
        self.write(0x32, 0x00)
        self.write(0x12, 0x02)
        self.write(0x13, 0x00)
        self.write(0x0E, 0xFF)
        self.write(0x0D, 0xFA)
        self.write(0x01, 0x00)

    def dump(self):
        return {reg: self.read(reg) for reg in range(0x00, 0x4A)}
