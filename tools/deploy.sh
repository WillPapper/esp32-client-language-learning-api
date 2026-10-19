#!/bin/sh
# Copy the app to the NOTE4 over USB with mpremote, then reset it so main.py starts.
#
#   tools/deploy.sh            # code and wifi_secrets.py only (a few seconds)
#   tools/deploy.sh --fonts    # also the 6.8 MB of fonts (first install, or after rebuilding them)
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

# Stop whatever is running (main.py loops forever) so the file copies go through.
$MP exec --no-follow "pass" >/dev/null 2>&1 || true

CODE="main.py app.py ui.py nfont.py api.py audio.py es8311.py buttons.py note4_epd.py wifi.py wifi_secrets.py"
for f in $CODE; do
    $MP fs cp "$f" ":$f"
done

if [ "${1:-}" = "--fonts" ]; then
    $MP fs mkdir :fonts >/dev/null 2>&1 || true
    for f in fonts/*.nf1; do
        $MP fs cp "$f" ":$f"
    done
fi

$MP reset
echo "deployed; watch it with: mpremote connect $PORT repl"
