"""Battery level and charging state for the Zectrix NOTE4.

From BatteryGetVoltage() / BatteryGetPercent() and ChargeStatus in Zectrix's board package:
the cell voltage is halved by a divider onto ADC1 channel 3 (GPIO 4); GPIO 2 reads 0 while
charging and GPIO 1 reads 1 when the charger reports full. The percent curve is the stock
firmware's quadratic fit (100 % at about 4.17 V, 50 % at 3.71 V, 0 % at about 3.47 V).
"""
from machine import ADC, Pin

PIN_VBAT_ADC = 4
PIN_CHARGE_DETECT = 2  # 0 = charging
PIN_CHARGE_FULL = 1  # 1 = full
SAMPLES = 10


class Battery:
    def __init__(self):
        self.adc = ADC(Pin(PIN_VBAT_ADC), atten=ADC.ATTN_11DB)
        self.detect = Pin(PIN_CHARGE_DETECT, Pin.IN)
        self.full = Pin(PIN_CHARGE_FULL, Pin.IN)

    def voltage_mv(self):
        total = 0
        for _ in range(SAMPLES):
            total += self.adc.read_uv()
        return total // SAMPLES // 1000 * 2

    @staticmethod
    def percent_for(mv):
        percent = (-mv * mv + 9016 * mv - 19189000) // 10000
        return max(0, min(100, percent))

    def read(self):
        """{"mv", "percent", "charging", "full"}"""
        mv = self.voltage_mv()
        return {
            "mv": mv,
            "percent": self.percent_for(mv),
            "charging": self.detect() == 0,
            "full": self.full() == 1,
        }
