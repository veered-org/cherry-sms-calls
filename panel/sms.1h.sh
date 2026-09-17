#!/usr/bin/env bash
# SMS button for a cbar-style panel plugin.
# cbar opens a popup on click (an action on the label line is not dispatched),
# so the popup carries the action as its first item.
set -uo pipefail
BIN="${COSMIC_PHONE_BIN:-$HOME/.local/bin}"

echo "SMS"
echo "---"
echo "Open / close SMS window | shell=${BIN}/cosmic-sms"
echo "Force close SMS window | shell=pkill param1=-f param2=cosmic-sms"
