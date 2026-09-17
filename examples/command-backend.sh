#!/usr/bin/env bash
# Example `backend = command` program for the COSMIC phone tools.
#
#   [command]
#   command = ~/bin/command-backend.sh
#
# It is called with one of:
#   sms-list --days N
#   sms-send --to NUMBER --text TEXT
#   calls-recent --limit N
# and must print JSON on stdout and exit 0 (non-zero + stderr = error shown).
#
# Real uses: wrap another provider's API, or run a script on a server that
# holds the credentials instead of this desktop, e.g.
#   exec ssh -o BatchMode=yes myserver python3 sms_tool.py "$@"
set -euo pipefail

case "${1:-}" in
    sms-list)
        cat <<'JSON'
[{"id": "1", "dir": "in", "contact": "2015550100",
  "date": "2030-01-01 09:30:00", "message": "Example inbound text"}]
JSON
        ;;
    sms-send)
        # $3 = number, $5 = text. Do the real send here.
        echo '{"status": "success", "id": "demo"}'
        ;;
    calls-recent)
        cat <<'JSON'
[{"when": "2030-01-01 09:00:00", "peer": "2015550100", "inbound": true,
  "seconds": 75, "disposition": "ANSWERED", "voicemail": false}]
JSON
        ;;
    *)
        echo "unknown command: ${1:-}" >&2
        exit 2
        ;;
esac
