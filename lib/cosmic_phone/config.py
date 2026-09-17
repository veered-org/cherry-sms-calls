"""Configuration and secrets.

    ~/.config/cosmic-tools/phone.conf      settings (INI, not secret)
    ~/.config/cosmic-tools/phone.secrets   API credentials (key = value, chmod 600)

Both paths can be overridden with $COSMIC_PHONE_CONF and $COSMIC_PHONE_SECRETS,
which is also how the tests point the code at throwaway files.
"""
import configparser
import os
import stat
import sys

CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
    "cosmic-tools")
CONF_PATH = os.environ.get("COSMIC_PHONE_CONF") or os.path.join(CONFIG_DIR, "phone.conf")
SECRETS_PATH = (os.environ.get("COSMIC_PHONE_SECRETS")
                or os.path.join(CONFIG_DIR, "phone.secrets"))

RUNTIME_DIR = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
STATE_FILE = os.path.join(RUNTIME_DIR, "cosmic-phone.state")
CACHE_DIR = os.path.join(
    os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
    "cosmic-tools", "phone")
DATA_DIR = os.path.join(
    os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"),
    "cosmic-tools", "phone")

DEFAULTS = {
    "phone": {
        "backend": "voipms",
        "did": "",
        "did_label": "",
        "sms_days": "30",
        "recent_calls": "3",
        "country_code": "1",
    },
    "voipms": {
        "api_url": "https://voip.ms/api/v1/rest.php",
        "http_method": "GET",
        "timezone": "-1",
        "timezone_is_utc": "true",
        "timeout": "30",
        "user_agent": "cosmic-tools-phone/0.1 (+https://github.com/veered-org/redshift-cosmic-tools)",
    },
    "twilio": {
        "api_url": "https://api.twilio.com",
        "timeout": "30",
    },
    "signalwire": {
        "space": "",
        "api_url": "",
        "timeout": "30",
    },
    "kdeconnect": {
        "cli": "kdeconnect-cli",
        "device": "",
    },
    "command": {
        "command": "",
    },
    "baresip": {
        "config_dir": "~/.baresip",
        "ctrl_host": "127.0.0.1",
        "ctrl_port": "4444",
    },
    "aec": {
        "enabled": "false",
        "sink": "cosmic_aec_sink",
        "source": "cosmic_aec_source",
    },
    "audio": {
        "duck_during_calls": "false",
    },
    "recording": {
        "dir": "",
    },
    "contacts": {
        "book": os.path.join(DATA_DIR, "contacts.json"),
        "vcf": "",
        "csv": "",
    },
    "panel": {
        "solo_family": "",
    },
}


class ConfigError(RuntimeError):
    pass


def load(path=None):
    """Parsed phone.conf with defaults filled in. A missing file is not an
    error: every tool must still start (and say what is missing) without it."""
    cp = configparser.ConfigParser(interpolation=None)
    cp.read_dict(DEFAULTS)
    cp.read(path or CONF_PATH)
    return cp


def expand(p):
    return os.path.expanduser(os.path.expandvars(p)) if p else p


def check_secrets_mode(path, warn=None):
    """Return a warning string if the secrets file is readable by anyone but
    its owner, else ''. Group-readable is warned about; world-readable loudly."""
    try:
        st = os.stat(path)
    except OSError:
        return ""
    mode = stat.S_IMODE(st.st_mode)
    msg = ""
    if mode & stat.S_IROTH:
        msg = ("WARNING: %s is WORLD-READABLE (mode %o). Anyone on this machine "
               "can read your API password. Run: chmod 600 %s" % (path, mode, path))
    elif mode & (stat.S_IRGRP | stat.S_IWGRP | stat.S_IWOTH):
        msg = ("warning: %s is accessible to other users (mode %o); "
               "run: chmod 600 %s" % (path, mode, path))
    if msg and warn is not None:
        warn(msg)
    return msg


def load_secrets(path=None, warn=None):
    """key = value lines; '#' starts a comment. Values are taken verbatim
    after the first '=' (surrounding whitespace stripped)."""
    path = path or SECRETS_PATH
    if warn is None:
        def warn(m):
            print(m, file=sys.stderr)
    if not os.path.exists(path):
        raise ConfigError("secrets file not found: %s (see phone.secrets.example)" % path)
    check_secrets_mode(path, warn)
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def dids(cfg):
    """All configured DIDs as bare digit strings. The first is the SMS sender."""
    raw = cfg.get("phone", "did", fallback="")
    return ["".join(c for c in part if c.isdigit())
            for part in raw.split(",") if part.strip()]
