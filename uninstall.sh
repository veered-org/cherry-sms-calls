#!/usr/bin/env bash
# uninstall.sh - remove the COSMIC phone tools for the current user.
#
#   ./uninstall.sh [--purge] [--dry-run]
#
# Removes programs, library, systemd units, panel entries and the AEC
# PipeWire config/drop-in installed by install.sh.
#
# Kept unless --purge: ~/.config/cosmic-tools/phone.conf, phone.secrets,
# the contacts book and caches.
# Always kept: ~/.baresip (your SIP account) and the locally built AEC plugin
# at ~/.local/lib/spa-0.2/aec/ - remove those by hand if you want them gone.
# PipeWire is never restarted.
set -euo pipefail

BINDIR="$HOME/.local/bin"
LIBDIR="$HOME/.local/lib/cosmic-tools/phone"
CONFDIR="${XDG_CONFIG_HOME:-$HOME/.config}/cosmic-tools"
UNITDIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
APPDIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DATADIR="${XDG_DATA_HOME:-$HOME/.local/share}/cosmic-tools/phone"
CACHEDIR="${XDG_CACHE_HOME:-$HOME/.cache}/cosmic-tools/phone"
PWCONF="${XDG_CONFIG_HOME:-$HOME/.config}/pipewire/pipewire.conf.d/99-cosmic-phone-echo-cancel.conf"

PURGE=0 DRY=0
for a in "$@"; do
    case "$a" in
        --purge) PURGE=1 ;;
        --dry-run) DRY=1 ;;
        -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
        *) echo "unknown option: $a" >&2; exit 2 ;;
    esac
done
run() { if [ "$DRY" = 1 ]; then echo "+ $*"; else "$@"; fi; }

PROGRAMS="cosmic-sms cosmic-dial cosmic-phoned cosmic-phone cosmic-phone-ctl
cosmic-phone-click cosmic-phone-alt cosmic-phone-silence cosmic-audio-duck
cosmic-aec-guard cosmic-contacts-build cosmic-call-merge cosmic-panel-launch"

run systemctl --user disable --now cosmic-phoned.service cosmic-phone-baresip.service 2>/dev/null || true
run rm -f "$UNITDIR/cosmic-phoned.service" "$UNITDIR/cosmic-phone-baresip.service" \
          "$UNITDIR/pipewire.service.d/cosmic-phone-aec.conf"
run rmdir "$UNITDIR/pipewire.service.d" 2>/dev/null || true
run rm -f "$PWCONF"

# Restore any audio this toolset changed before its programs disappear.
[ -x "$BINDIR/cosmic-audio-duck" ] && run "$BINDIR/cosmic-audio-duck" restore || true
[ -x "$BINDIR/cosmic-phone-silence" ] && run "$BINDIR/cosmic-phone-silence" restore || true

for p in $PROGRAMS; do run rm -f "$BINDIR/$p"; done
run rm -rf "$LIBDIR"
run rm -f "$APPDIR"/io.github.veered.CosmicPhone.*.desktop
run rm -rf "$DATADIR/panel"

if [ "$PURGE" = 1 ]; then
    run rm -f "$CONFDIR/phone.conf" "$CONFDIR/phone.secrets" "$CONFDIR/phone.aec-off"
    run rm -rf "$DATADIR" "$CACHEDIR"
fi

run systemctl --user daemon-reload || true
echo "Removed. If you used --with-aec, restart PipeWire when convenient:"
echo "  systemctl --user restart pipewire pipewire-pulse wireplumber"
[ "$PURGE" = 1 ] || echo "Config and secrets kept in $CONFDIR (use --purge to remove)."
