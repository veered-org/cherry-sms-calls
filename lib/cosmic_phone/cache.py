"""Small JSON cache + error log helpers. Never raise: a cache or logging
problem must not break a window."""
import datetime
import json
import os

from .config import CACHE_DIR


def read(name):
    try:
        with open(os.path.join(CACHE_DIR, name)) as f:
            return json.load(f)
    except Exception:
        return None


def write(name, data):
    try:
        os.makedirs(CACHE_DIR, mode=0o700, exist_ok=True)
        path = os.path.join(CACHE_DIR, name)
        tmp = path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)       # atomic: a half-written file is never read
    except Exception:
        pass


def log_error(name, text):
    """Append to <cache>/<name>.errors.log (rotated at ~64 KB). The status
    label in a narrow window is one ellipsized line; this keeps the full text."""
    try:
        os.makedirs(CACHE_DIR, mode=0o700, exist_ok=True)
        path = os.path.join(CACHE_DIR, name + ".errors.log")
        if os.path.exists(path) and os.path.getsize(path) > 64000:
            os.replace(path, path + ".1")
        with open(path, "a") as f:
            f.write("%s  %s\n" % (datetime.datetime.now().isoformat(timespec="seconds"), text))
    except Exception:
        pass
