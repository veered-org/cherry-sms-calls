#!/usr/bin/env bash
# install.sh - install the COSMIC phone tools for the current user.
#
#   ./install.sh [--enable] [--panel] [--with-aec [--no-aec-plugin]] [--dry-run]
#
#   --enable          enable and start the baresip + cosmic-phoned user units
#   --panel           install COSMIC panel-button desktop entries (and copy the
#                     cbar plugins to ~/.local/share/cosmic-tools/phone/panel)
#   --with-aec        install the PipeWire echo-cancel config, set [aec] enabled
#                     = true, and (if aec/build-aec.sh has installed the plugin)
#                     the pipewire.service drop-in that loads it
#   --no-aec-plugin   with --with-aec: your distro plugin is already AEC3, so
#                     skip the SPA_PLUGIN_DIR drop-in
#   --dry-run         print what would be done
#
# Never overwrites an existing phone.conf, phone.secrets, ~/.baresip/config or
# ~/.baresip/accounts. Never restarts PipeWire. Needs no root.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
BINDIR="$HOME/.local/bin"
LIBDIR="$HOME/.local/lib/cosmic-tools/phone"
CONFDIR="${XDG_CONFIG_HOME:-$HOME/.config}/cosmic-tools"
UNITDIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
APPDIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DATADIR="${XDG_DATA_HOME:-$HOME/.local/share}/cosmic-tools/phone"
PWCONFDIR="${XDG_CONFIG_HOME:-$HOME/.config}/pipewire/pipewire.conf.d"
BARESIP_DIR="$HOME/.baresip"

ENABLE=0 PANEL=0 AEC=0 AEC_PLUGIN=1 DRY=0
for a in "$@"; do
    case "$a" in
        --enable) ENABLE=1 ;;
        --panel) PANEL=1 ;;
        --with-aec) AEC=1 ;;
        --no-aec-plugin) AEC_PLUGIN=0 ;;
        --dry-run) DRY=1 ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "unknown option: $a" >&2; exit 2 ;;
    esac
done

run() { if [ "$DRY" = 1 ]; then echo "+ $*"; else "$@"; fi; }
note() { echo "==> $*"; }
warn() { echo "WARNING: $*" >&2; }

# ---------------------------------------------------------------- deps (warn only)
missing=()
command -v python3 >/dev/null || missing+=(python3)
python3 -c 'import gi; gi.require_version("Gtk","3.0"); from gi.repository import Gtk' 2>/dev/null \
    || missing+=(python3-gi gir1.2-gtk-3.0)
python3 -c 'import gi; gi.require_version("GtkLayerShell","0.1")' 2>/dev/null \
    || missing+=(gir1.2-gtklayershell-0.1)
command -v baresip >/dev/null || missing+=(baresip)
command -v pactl >/dev/null || missing+=(pulseaudio-utils)
command -v notify-send >/dev/null || missing+=(libnotify-bin)
if [ "${#missing[@]}" -gt 0 ]; then
    warn "missing packages: sudo apt install ${missing[*]}"
fi

# ---------------------------------------------------------------- program files
note "programs -> $BINDIR"
run mkdir -p "$BINDIR" "$LIBDIR"
for f in "$SRC"/bin/*; do
    run install -m755 "$f" "$BINDIR/$(basename "$f")"
done
note "library -> $LIBDIR/cosmic_phone"
run rm -rf "$LIBDIR/cosmic_phone"
run mkdir -p "$LIBDIR/cosmic_phone"
for f in "$SRC"/lib/cosmic_phone/*.py; do
    run install -m644 "$f" "$LIBDIR/cosmic_phone/"
done

# ---------------------------------------------------------------- config + secrets
run mkdir -p "$CONFDIR"
if [ ! -e "$CONFDIR/phone.conf" ]; then
    note "creating $CONFDIR/phone.conf (edit: set did =)"
    run install -m644 "$SRC/examples/phone.conf.example" "$CONFDIR/phone.conf"
fi
if [ ! -e "$CONFDIR/phone.secrets" ]; then
    note "creating $CONFDIR/phone.secrets (mode 600; edit: API username/password)"
    run install -m600 "$SRC/examples/phone.secrets.example" "$CONFDIR/phone.secrets"
else
    mode=$(stat -c %a "$CONFDIR/phone.secrets")
    [ "$mode" = 600 ] || [ "$mode" = 400 ] || warn "$CONFDIR/phone.secrets is mode $mode; run: chmod 600 $CONFDIR/phone.secrets"
fi

# ---------------------------------------------------------------- baresip
run mkdir -p "$BARESIP_DIR"
run chmod 700 "$BARESIP_DIR"
if [ ! -e "$BARESIP_DIR/config" ]; then
    note "creating $BARESIP_DIR/config from template"
    run install -m644 "$SRC/baresip/config.template" "$BARESIP_DIR/config"
else
    note "$BARESIP_DIR/config exists; template saved as config.cosmic-phone-template for comparison"
    run install -m644 "$SRC/baresip/config.template" "$BARESIP_DIR/config.cosmic-phone-template"
    if ! grep -qE '^[[:space:]]*ctrl_tcp_listen[[:space:]]+127\.0\.0\.1:' "$BARESIP_DIR/config"; then
        warn "$BARESIP_DIR/config does not bind ctrl_tcp_listen to 127.0.0.1 - its default is 0.0.0.0, which lets anyone on your network place calls"
    fi
    if grep -qE '^[[:space:]]*(http_listen|cons_listen)[[:space:]]+0\.0\.0\.0' "$BARESIP_DIR/config"; then
        warn "$BARESIP_DIR/config has http_listen/cons_listen on 0.0.0.0 - bind them to 127.0.0.1 or disable those modules"
    fi
fi
run install -m600 "$SRC/baresip/accounts.example" "$BARESIP_DIR/accounts.example"
[ -e "$BARESIP_DIR/accounts" ] || note "next: copy $BARESIP_DIR/accounts.example to $BARESIP_DIR/accounts (chmod 600) and fill it in"

# ---------------------------------------------------------------- systemd user units
note "systemd user units -> $UNITDIR"
run mkdir -p "$UNITDIR"
run install -m644 "$SRC/systemd/cosmic-phone-baresip.service" "$UNITDIR/"
run install -m644 "$SRC/systemd/cosmic-phoned.service" "$UNITDIR/"

# ---------------------------------------------------------------- AEC (optional)
if [ "$AEC" = 1 ]; then
    note "echo-cancel module config -> $PWCONFDIR"
    run mkdir -p "$PWCONFDIR"
    run install -m644 "$SRC/aec/99-cosmic-phone-echo-cancel.conf.in" "$PWCONFDIR/99-cosmic-phone-echo-cancel.conf"
    if [ "$AEC_PLUGIN" = 1 ]; then
        if [ -f "$HOME/.local/lib/spa-0.2/aec/libspa-aec-webrtc.so" ]; then
            multiarch=$(gcc -print-multiarch 2>/dev/null || dpkg-architecture -qDEB_HOST_MULTIARCH 2>/dev/null || echo x86_64-linux-gnu)
            sysdir="/usr/lib/$multiarch/spa-0.2"
            [ -d "$sysdir" ] || warn "system SPA dir $sysdir not found; edit the drop-in by hand"
            note "pipewire.service drop-in (SPA_PLUGIN_DIR)"
            run mkdir -p "$UNITDIR/pipewire.service.d"
            if [ "$DRY" = 1 ]; then
                echo "+ write $UNITDIR/pipewire.service.d/cosmic-phone-aec.conf"
            else
                sed "s|@SPA_SYSTEM_DIR@|$sysdir|" "$SRC/systemd/pipewire-aec.conf.in" \
                    > "$UNITDIR/pipewire.service.d/cosmic-phone-aec.conf"
            fi
        else
            warn "no ~/.local/lib/spa-0.2/aec/libspa-aec-webrtc.so - run aec/build-aec.sh first (or use --no-aec-plugin)"
        fi
    fi
    if [ "$DRY" = 1 ]; then
        echo "+ set [aec] enabled = true in $CONFDIR/phone.conf"
    elif [ -e "$CONFDIR/phone.conf" ]; then
        sed -i '/^\[aec\]/,/^\[/ s/^enabled[[:space:]]*=.*/enabled = true/' "$CONFDIR/phone.conf"
    fi
    note "restart PipeWire yourself when convenient: systemctl --user restart pipewire pipewire-pulse wireplumber"
fi

# ---------------------------------------------------------------- panel (optional)
if [ "$PANEL" = 1 ]; then
    note "panel-button desktop entries -> $APPDIR"
    run mkdir -p "$APPDIR" "$DATADIR/panel"
    for f in "$SRC"/panel/*.desktop.in; do
        out="$APPDIR/$(basename "${f%.in}")"
        if [ "$DRY" = 1 ]; then echo "+ write $out"; else sed "s|@BINDIR@|$BINDIR|g" "$f" > "$out"; fi
    done
    for f in "$SRC"/panel/*.sh; do
        run install -m755 "$f" "$DATADIR/panel/"
    done
    note "add io.github.veered.CosmicPhone.Button.Phone / .SMS to a panel (see README 'Panel buttons')"
fi

run systemctl --user daemon-reload || true

if [ "$ENABLE" = 1 ]; then
    run systemctl --user enable --now cosmic-phone-baresip.service cosmic-phoned.service
fi

cat <<MSG

Installed. Remaining steps (see README):
  1. edit $CONFDIR/phone.conf       (backend = voipms|twilio|signalwire|kdeconnect, did = your number)
  2. edit $CONFDIR/phone.secrets    (API credentials for that backend; not needed for kdeconnect)
  3. cosmic-phone check              (tests the backend; for voip.ms, prints the IP to allowlist)
  4. fill in $BARESIP_DIR/accounts   (SIP sub-account)
  5. systemctl --user enable --now cosmic-phone-baresip cosmic-phoned
MSG
case ":$PATH:" in *":$BINDIR:"*) ;; *) warn "$BINDIR is not on your PATH" ;; esac
