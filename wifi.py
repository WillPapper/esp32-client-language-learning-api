"""Wi-Fi station helper for the NOTE4 (the ESP32-S3 supports 2.4 GHz only).

Credentials come from wifi_secrets.py on the device. That file is git-ignored on the
Mac; copy wifi_secrets.example.py to create it.
"""
import network
import time

HOSTNAME = "note4"

# Statuses that mean connecting has failed. Names differ between MicroPython versions,
# so only the ones this build defines are used.
_FAILURES = {}
for _name, _why in (
    ("STAT_NO_AP_FOUND", "network not found (it must be 2.4 GHz)"),
    ("STAT_WRONG_PASSWORD", "wrong password"),
    ("STAT_ASSOC_FAIL", "association failed"),
    ("STAT_HANDSHAKE_TIMEOUT", "handshake timed out (often a wrong password)"),
    ("STAT_BEACON_TIMEOUT", "lost contact with the access point"),
):
    if hasattr(network, _name):
        _FAILURES[getattr(network, _name)] = _why


def connect(timeout_ms=20_000):
    """Connect if not already connected, and return the WLAN interface. Raises OSError on failure."""
    import wifi_secrets

    network.hostname(HOSTNAME)
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if wlan.isconnected():
        return wlan
    wlan.connect(wifi_secrets.SSID, wifi_secrets.PASSWORD)
    start = time.ticks_ms()
    while not wlan.isconnected():
        status = wlan.status()
        if status in _FAILURES:
            wlan.disconnect()
            raise OSError("Wi-Fi: " + _FAILURES[status])
        if time.ticks_diff(time.ticks_ms(), start) > timeout_ms:
            wlan.disconnect()
            raise OSError("Wi-Fi: timed out (status %d)" % status)
        time.sleep_ms(200)
    return wlan
