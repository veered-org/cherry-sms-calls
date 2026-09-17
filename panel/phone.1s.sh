#!/usr/bin/env bash
# Phone button for a cbar-style panel plugin (BitBar/Argos line format).
# State comes from cosmic-phoned via a one-line file, so this does no SIP work
# and a 1 s refresh is cheap.
set -uo pipefail

STATE_FILE="${XDG_RUNTIME_DIR:-/tmp}/cosmic-phone.state"
BIN="${COSMIC_PHONE_BIN:-$HOME/.local/bin}"
state=$(cat "$STATE_FILE" 2>/dev/null || echo IDLE)
kind=${state%%|*}
num=${state#*|}
[ "$num" = "$state" ] && num=""

case "$kind" in
    RINGING)
        echo "📞 ANSWER ${num} | color=#7CFC00"
        echo "---"
        echo "Answer ${num} | shell=${BIN}/cosmic-phone-ctl param1=accept refresh=true"
        echo "Hang up | shell=${BIN}/cosmic-phone-ctl param1=hangup refresh=true"
        ;;
    INCALL)
        echo "● ${num} | color=#ff8a8a"
        echo "---"
        echo "Hang up | shell=${BIN}/cosmic-phone-ctl param1=hangup refresh=true"
        ;;
    *)
        echo "☎"
        echo "---"
        # cosmic-dial toggles itself, so this one item opens and closes it.
        echo "Open / close dialer | shell=${BIN}/cosmic-dial"
        echo "Force close dialer | shell=pkill param1=-f param2=cosmic-dial"
        ;;
esac
