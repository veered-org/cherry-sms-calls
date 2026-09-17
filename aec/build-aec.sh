#!/usr/bin/env bash
# build-aec.sh - build PipeWire's aec-webrtc SPA plugin against
# webrtc-audio-processing 1.3 (EchoCanceller3), self-contained (static wap +
# abseil), and install it to ~/.local/lib/spa-0.2/aec/.
#
# Nothing system-wide is touched and PipeWire is NOT restarted; the script
# prints the remaining manual steps at the end.
#
# Usage: aec/build-aec.sh [--pipewire-version X.Y.Z] [--workdir DIR] [--no-install]
#
# Deps (Debian/Ubuntu):
#   sudo apt install build-essential pkg-config meson ninja-build cmake curl \
#                    libpipewire-0.3-dev pulseaudio-utils
# (If the distro meson is too old for webrtc-audio-processing, use
#  `python3 -m venv ~/.venvs/meson && ~/.venvs/meson/bin/pip install meson ninja`
#  and put that venv's bin first on PATH.)
set -euo pipefail

WAP_VERSION="${WAP_VERSION:-v1.3}"
PW_VERSION=""
WORKDIR=""
INSTALL=1
while [ $# -gt 0 ]; do
    case "$1" in
        --pipewire-version) PW_VERSION="$2"; shift 2 ;;
        --workdir) WORKDIR="$2"; shift 2 ;;
        --no-install) INSTALL=0; shift ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

if [ -z "$PW_VERSION" ]; then
    # "Linked with libpipewire X.Y.Z" - the plugin must match the RUNNING library.
    PW_VERSION=$(pipewire --version | sed -n 's/.*libpipewire \([0-9.]*\).*/\1/p' | tail -1)
fi
[ -n "$PW_VERSION" ] || { echo "could not detect PipeWire version; pass --pipewire-version" >&2; exit 1; }

WORKDIR="${WORKDIR:-${XDG_CACHE_HOME:-$HOME/.cache}/cosmic-tools/aec-build}"
PREFIX="$WORKDIR/prefix"
MULTIARCH=$(gcc -print-multiarch 2>/dev/null || echo "")
NICE="nice -n 10"
mkdir -p "$WORKDIR" "$PREFIX"
cd "$WORKDIR"
echo "== PipeWire $PW_VERSION, webrtc-audio-processing $WAP_VERSION, workdir $WORKDIR"

fetch() {  # url file
    [ -s "$2" ] || curl -fL --retry 3 -o "$2" "$1"
}

# 1. webrtc-audio-processing (static), abseil comes in as a meson subproject
WAP_TGZ="webrtc-audio-processing-$WAP_VERSION.tar.gz"
fetch "https://gitlab.freedesktop.org/pulseaudio/webrtc-audio-processing/-/archive/$WAP_VERSION/$WAP_TGZ" "$WAP_TGZ"
rm -rf wap-src && mkdir wap-src && tar xf "$WAP_TGZ" -C wap-src --strip-components=1
( cd wap-src
  $NICE meson setup build --prefix="$PREFIX" --buildtype=release \
        -Ddefault_library=static -Db_pie=true -Dcpp_args=-fPIC -Dc_args=-fPIC
  $NICE ninja -C build
  ninja -C build install )

# 2. PipeWire source for the plugin file + SPA headers matching the running version
PW_TGZ="pipewire-$PW_VERSION.tar.gz"
fetch "https://gitlab.freedesktop.org/pipewire/pipewire/-/archive/$PW_VERSION/$PW_TGZ" "$PW_TGZ"
rm -rf pw-src && mkdir pw-src && tar xf "$PW_TGZ" -C pw-src --strip-components=1
PLUGIN_SRC="pw-src/spa/plugins/aec/aec-webrtc.cpp"
[ -f "$PLUGIN_SRC" ] || { echo "no $PLUGIN_SRC in PipeWire $PW_VERSION" >&2; exit 1; }
if ! grep -q HAVE_WEBRTC1 "$PLUGIN_SRC"; then
    echo "WARNING: $PLUGIN_SRC has no HAVE_WEBRTC1 path; this recipe may need a newer" >&2
    echo "         webrtc-audio-processing (set WAP_VERSION) and HAVE_WEBRTC2." >&2
fi

# 3. compile the plugin, statically linking wap + abseil
printf '#define HAVE_WEBRTC1 1\n' > config.h
PCDIR=$(dirname "$(find "$PREFIX" -name 'webrtc-audio-processing-1.pc' | head -1)")
WAP_A=$(find "$PREFIX" -name 'libwebrtc-audio-processing-1.a' | head -1)
[ -n "$WAP_A" ] || { echo "static libwebrtc-audio-processing-1.a not found under $PREFIX" >&2; exit 1; }
mapfile -t ABSL_A < <(find wap-src/build/subprojects -name 'libabsl*.a' -o -name 'libabsl_*.a' | sort -u)
ABSL_INC=$(find wap-src/subprojects -maxdepth 1 -type d -name 'abseil-cpp*' | head -1)
[ "${#ABSL_A[@]}" -gt 0 ] || { echo "abseil static libraries not found in the wap build" >&2; exit 1; }

$NICE g++ -std=c++17 -O2 -shared -fPIC \
    "$PLUGIN_SRC" -o libspa-aec-webrtc.so \
    -I . -I pw-src/spa/include ${ABSL_INC:+-I "$ABSL_INC"} \
    $(PKG_CONFIG_PATH="$PCDIR" pkg-config --cflags webrtc-audio-processing-1) \
    "$WAP_A" -Wl,--start-group "${ABSL_A[@]}" -Wl,--end-group -pthread

echo "== built $WORKDIR/libspa-aec-webrtc.so"
ldd libspa-aec-webrtc.so | grep -v -E 'libc\.|libm\.|libstdc\+\+|libgcc_s|ld-linux|vdso' || true

if [ "$INSTALL" = 1 ]; then
    install -Dm644 libspa-aec-webrtc.so "$HOME/.local/lib/spa-0.2/aec/libspa-aec-webrtc.so"
    echo "== installed to ~/.local/lib/spa-0.2/aec/libspa-aec-webrtc.so"
fi

cat <<MSG

Next steps (manual - this script restarts nothing):
  1. install.sh --with-aec   (drop-in with SPA_PLUGIN_DIR, echo-cancel config,
                              [aec] enabled = true)
     system SPA dir for the drop-in: /usr/lib/${MULTIARCH:-<multiarch>}/spa-0.2
  2. systemctl --user daemon-reload
     systemctl --user restart pipewire pipewire-pulse wireplumber
  3. pactl list short sinks | grep cosmic_aec_sink       # must appear
  4. systemctl --user restart cosmic-phone-baresip
MSG
