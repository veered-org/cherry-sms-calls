# Optional: AEC3 echo cancellation for calls

Skip this if you use a headset. It is for open speakers plus a desk microphone,
where the far end otherwise hears its own voice come back.

## Why a local build

PipeWire's `libpipewire-module-echo-cancel` loads the `aec/libspa-aec-webrtc` SPA
plugin. Several distributions (Ubuntu 24.04 included) build that plugin against
**webrtc-audio-processing 0.3.x**, the 2016 AECM-era canceller, which can cancel
almost nothing when the mic and speakers are separate USB devices with independent
clocks. Building the same plugin against **webrtc-audio-processing 1.3** gets
**EchoCanceller3**, the generation browsers use for video calls.

Expect a real but partial improvement (a few dB of converged cancellation on
split-clock USB hardware). A headset is still the complete fix.

Check what your distro links first:

```sh
ldd /usr/lib/*/spa-0.2/aec/libspa-aec-webrtc.so | grep webrtc
```

If it already says `webrtc-audio-processing-1` or `-2`, skip the build and only
install the module config (`install.sh --with-aec --no-aec-plugin`).

## How it is wired

| Piece | Where |
|-------|-------|
| Plugin (built here) | `~/.local/lib/spa-0.2/aec/libspa-aec-webrtc.so` (wap + abseil linked statically) |
| Plugin search path | `~/.config/systemd/user/pipewire.service.d/cosmic-phone-aec.conf` sets `SPA_PLUGIN_DIR=~/.local/lib/spa-0.2:<system spa dir>`. First match wins, so only this one plugin is shadowed. |
| Module config | `~/.config/pipewire/pipewire.conf.d/99-cosmic-phone-echo-cancel.conf` (mono nodes `cosmic_aec_sink` / `cosmic_aec_source`) |
| baresip devices | `cosmic-aec-guard` (ExecStartPre of `cosmic-phone-baresip.service`) points baresip at the nodes when they exist, otherwise at `pulse,default` with a critical notification |
| Escape hatch | `touch ~/.config/cosmic-tools/phone.aec-off` |

## Build

```sh
sudo apt install build-essential pkg-config meson ninja-build cmake curl \
                 libpipewire-0.3-dev pulseaudio-utils
./aec/build-aec.sh                 # detects the running PipeWire version
./install.sh --with-aec
systemctl --user daemon-reload
systemctl --user restart pipewire pipewire-pulse wireplumber
pactl list short sinks | grep cosmic_aec_sink
systemctl --user restart cosmic-phone-baresip
```

`build-aec.sh` downloads the webrtc-audio-processing and PipeWire source tarballs
from gitlab.freedesktop.org into `~/.cache/cosmic-tools/aec-build`, builds with
`nice`, and installs only the single `.so`. It never restarts anything.

## After a PipeWire upgrade

Usually nothing breaks: apt does not touch `~/.local` or the drop-in. If the SPA ABI
changed, the plugin fails to load, the nodes never appear, and `cosmic-aec-guard`
falls back and notifies you. Re-run `./aec/build-aec.sh` (it picks up the new
version) and restart PipeWire.

If a future PipeWire drops the `HAVE_WEBRTC1` code path, build a newer
webrtc-audio-processing (`WAP_VERSION=v2.0 ./aec/build-aec.sh`) and adjust the
`config.h` define in the script to `HAVE_WEBRTC2`.

## Tuning notes

* Keep `webrtc.gain_control = false`. With AEC3 it enables both WebRTC AGCs, which
  wreck delay estimation; residual echo can come out louder than the raw mic.
* Keep the nodes mono. In stereo the canceller tends to attenuate everything
  uniformly rather than cancel.
* The plugin hard-codes a high noise-suppression level. If your outgoing voice
  sounds quiet on a test recording, first make sure someone was actually talking.

## Verifying it cancels

Play 25 s of band-limited noise (300 to 3400 Hz) into `cosmic_aec_sink` while
recording both the raw mic and `cosmic_aec_source` with `parecord`, in a silent
room. Compare in-band energy of the two recordings: the difference is the
cancellation. Around 0 dB means you are on the old canceller or the plugin did
not load.
