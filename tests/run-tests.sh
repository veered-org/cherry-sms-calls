#!/usr/bin/env bash
# Offline test suite: syntax checks + unit tests. Makes no network calls
# beyond 127.0.0.1 and touches no live configuration.
set -euo pipefail
cd "$(dirname "$0")/.."
export COSMIC_PHONE_CONF=/nonexistent/phone.conf COSMIC_PHONE_SECRETS=/nonexistent/phone.secrets
export XDG_CACHE_HOME="$(mktemp -d)"
for f in install.sh uninstall.sh aec/build-aec.sh panel/*.sh examples/*.sh bin/*; do
    head -1 "$f" | grep -qE '^#!.*(ba)?sh' && bash -n "$f" && echo "bash -n ok  $f"
done
for f in bin/* lib/cosmic_phone/*.py tests/*.py; do
    head -1 "$f" | grep -q python || [[ "$f" == *.py ]] || continue
    python3 - "$f" <<'PY'
import py_compile, sys, tempfile, os
d = tempfile.mkdtemp()
py_compile.compile(sys.argv[1], cfile=os.path.join(d, "x.pyc"), doraise=True)
print("py_compile ok ", sys.argv[1])
PY
done
python3 -m unittest discover -s tests -v
