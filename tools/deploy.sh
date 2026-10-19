#!/bin/sh
# Copy the app to the NOTE4 over USB with mpremote, then reset it so main.py starts.
#
#   tools/deploy.sh            # code and wifi_secrets.py only (a few seconds)
#   tools/deploy.sh --fonts    # also check fonts/: uploads only files missing on the device or
#                              # of a different size (a full upload is 6.8 MB at about 5 KB/s,
#                              # 25 minutes). Fonts persist in flash, so this is needed once.
#   PORT=/dev/cu.usbmodemXXXX tools/deploy.sh
#
# Needs wifi_secrets.py next to this repo's code (copy wifi_secrets.example.py and fill it in).
# It is git-ignored; this script never prints it.
set -eu
cd "$(dirname "$0")/.."
PORT="${PORT:-/dev/cu.usbmodem2101}"
MP="mpremote connect $PORT"

if [ ! -f wifi_secrets.py ]; then
    echo "wifi_secrets.py is missing: cp wifi_secrets.example.py wifi_secrets.py and fill it in" >&2
    exit 1
fi

# Any exec interrupts main.py (it loops forever) so the file copies go through.
$MP exec "pass" >/dev/null 2>&1 || true

CODE="main.py app.py ui.py nfont.py api.py audio.py es8311.py buttons.py battery.py settings.py note4_epd.py wifi.py wifi_secrets.py"
for f in $CODE; do
    $MP fs cp "$f" ":$f"
done

if [ "${1:-}" = "--fonts" ]; then
    # Sizes of the fonts already on the device, one "name size" per line.
    on_device=$($MP exec "
import os
try:
    os.mkdir('fonts')
except OSError:
    pass
for e in os.ilistdir('fonts'):
    print(e[0], e[3])
")
    for f in fonts/*.nf1; do
        name=$(basename "$f")
        size=$(wc -c < "$f" | tr -d ' ')
        if echo "$on_device" | grep -q "^$name $size$"; then
            echo "fonts/$name already on the device"
        else
            $MP fs cp "$f" ":fonts/$name"
        fi
    done
fi

$MP reset
echo "deployed; watch it with: mpremote connect $PORT repl"
