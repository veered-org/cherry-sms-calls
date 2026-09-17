"""SMS and call-history backends.

Every backend returns the same plain data, so the windows never know where it
came from:

    sms_list(days)      -> [{"id", "dir": "in"|"out", "contact", "date", "message"}]
                           newest first; date is local "YYYY-MM-DD HH:MM:SS"
    sms_send(to, text)  -> {"status": "success", "id": ...}   (raises on failure)
    calls_recent(limit) -> [{"when", "peer", "inbound", "seconds",
                             "disposition", "voicemail"}]    newest first

Backends:

    voipms      talks to the voip.ms REST API directly (default)
    twilio      Twilio REST API (SMS, call history)
    signalwire  SignalWire's Twilio-compatible API (SMS, call history)
    kdeconnect  sends SMS through your own Android phone with kdeconnect-cli
                (send only: no message or call history)
    command     runs a program you supply and reads JSON from its stdout
"""
import base64
import datetime
import email.utils
import json
import shlex
import subprocess
import urllib.error
import urllib.parse
import urllib.request

from . import config as _config


class BackendError(RuntimeError):
    pass


def digits(s):
    return "".join(c for c in str(s or "") if c.isdigit())


def nanp10(s):
    """Strip a leading NANP country code 1 from an 11-digit number."""
    d = digits(s)
    return d[1:] if len(d) == 11 and d.startswith("1") else d


# --------------------------------------------------------------------------
# voip.ms
# --------------------------------------------------------------------------

# Statuses that mean "the query matched nothing", not an error.
EMPTY_STATUSES = {"no_sms", "no_cdr", "no_messages"}

HINTS = {
    "ip_not_enabled": "this machine's public IP is not on the API allowlist "
                      "(voip.ms portal > Main Menu > SOAP and REST/JSON API)",
    "invalid_credentials": "check api_username / api_password in phone.secrets "
                           "(the API password is set separately from the portal password)",
    "api_not_enabled": "enable the API in the voip.ms portal (SOAP and REST/JSON API)",
    "missing_credentials": "phone.secrets is missing api_username or api_password",
}


class VoipMsBackend:
    max_len = 160
    def __init__(self, cfg, secrets):
        self.cfg = cfg
        self.user = secrets.get("api_username", "")
        self.password = secrets.get("api_password", "")
        self.url = cfg.get("voipms", "api_url")
        self.method_http = cfg.get("voipms", "http_method").upper()
        self.tz = cfg.get("voipms", "timezone")
        self.tz_is_utc = cfg.getboolean("voipms", "timezone_is_utc")
        self.timeout = cfg.getfloat("voipms", "timeout")
        # voip.ms answers HTTP 403 to urllib's default "Python-urllib/x.y"
        # User-Agent, so a custom one is REQUIRED, not cosmetic.
        self.user_agent = cfg.get("voipms", "user_agent") or "cosmic-tools-phone"
        self.dids = _config.dids(cfg)

    # -- transport ---------------------------------------------------------
    def call(self, method, **params):
        if not self.user or not self.password:
            raise BackendError("%s: missing_credentials: %s"
                               % (method, HINTS["missing_credentials"]))
        q = {"api_username": self.user, "api_password": self.password,
             "method": method}
        q.update({k: str(v) for k, v in params.items() if v is not None})
        body = urllib.parse.urlencode(q)
        headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
        if self.method_http == "POST":
            req = urllib.request.Request(
                self.url, data=body.encode(), headers=dict(
                    headers, **{"Content-Type": "application/x-www-form-urlencoded"}))
        else:
            req = urllib.request.Request(self.url + "?" + body, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            # Never include the URL: in GET mode it carries the password.
            hint = (" (blocked User-Agent or IP; see README)" if e.code == 403 else "")
            raise BackendError("%s: HTTP %d%s" % (method, e.code, hint)) from None
        except (urllib.error.URLError, OSError) as e:
            reason = getattr(e, "reason", e)
            raise BackendError("%s: network error: %s" % (method, reason)) from None
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            raise BackendError("%s: response was not JSON" % method) from None
        status = str(data.get("status", ""))
        if status == "success" or status in EMPTY_STATUSES:
            return data
        msg = data.get("message") or HINTS.get(status, "")
        raise BackendError("%s: %s%s" % (method, status or "unknown error",
                                          (": " + msg) if msg else ""))

    # -- helpers -----------------------------------------------------------
    def local_time(self, stamp):
        """voip.ms timestamps are in the zone requested via `timezone`. When
        that zone is UTC, convert to this machine's local time for display."""
        if not stamp or not self.tz_is_utc:
            return stamp or ""
        try:
            t = datetime.datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return stamp
        t = t.replace(tzinfo=datetime.timezone.utc).astimezone()
        return t.strftime("%Y-%m-%d %H:%M:%S")

    def _did(self):
        if not self.dids:
            raise BackendError("no DID configured: set did = in phone.conf")
        return self.dids[0]

    # -- API ---------------------------------------------------------------
    def sms_list(self, days=30):
        today = datetime.date.today()
        start = today - datetime.timedelta(days=int(days))
        data = self.call("getSMS", did=self._did(),
                         **{"from": start.isoformat(),
                            "to": (today + datetime.timedelta(days=1)).isoformat()},
                         timezone=self.tz, limit=1000)
        out = []
        for m in data.get("sms") or []:
            out.append({
                "id": str(m.get("id", "")),
                # voip.ms: type "1" = received, "0" = sent
                "dir": "in" if str(m.get("type")) == "1" else "out",
                "contact": nanp10(m.get("contact")),
                "date": self.local_time(m.get("date", "")),
                "message": m.get("message", "") or "",
            })
        out.sort(key=lambda m: m["date"], reverse=True)
        return out

    def sms_send(self, to, text):
        to = nanp10(to)
        if not to:
            raise BackendError("sendSMS: no destination number")
        if not text:
            raise BackendError("sendSMS: empty message")
        if len(text) > 160:
            raise BackendError("sendSMS: message is %d characters; voip.ms SMS "
                               "allows 160" % len(text))
        data = self.call("sendSMS", did=self._did(), dst=to, message=text)
        return {"status": "success", "id": data.get("sms")}

    def calls_recent(self, limit=3, days=30):
        today = datetime.date.today()
        start = today - datetime.timedelta(days=int(days))
        data = self.call("getCDR", date_from=start.isoformat(),
                         date_to=today.isoformat(), timezone=self.tz,
                         # All four default to false: omit them and voip.ms
                         # returns no calls at all.
                         answered=1, noanswer=1, busy=1, failed=1)
        ours = {nanp10(d) for d in self.dids}
        out = []
        for c in data.get("cdr") or []:
            dest = nanp10(c.get("destination"))
            callerid = str(c.get("callerid") or "")
            if "<" in callerid and ">" in callerid:
                callerid = callerid[callerid.rfind("<") + 1:callerid.rfind(">")]
            inbound = bool(ours) and dest in ours
            peer = nanp10(callerid) if inbound else dest
            logs = json.dumps(c.get("call_logs", "")) if c.get("call_logs") else ""
            try:
                secs = int(c.get("seconds") or 0)
            except (TypeError, ValueError):
                secs = 0
            out.append({
                "when": self.local_time(c.get("date", "")),
                "peer": peer,
                "inbound": inbound,
                "seconds": secs,
                "disposition": c.get("disposition", ""),
                # voip.ms reports ANSWERED when voicemail picks up, so the
                # disposition alone cannot tell a missed call.
                "voicemail": "voicemail" in (logs + " " + str(c.get("description", ""))).lower(),
            })
        out.sort(key=lambda c: c["when"], reverse=True)
        return out[:int(limit)]

    def whoami(self):
        """getIP: the public IP voip.ms sees - what goes on the API allowlist."""
        return self.call("getIP").get("ip", "")


# --------------------------------------------------------------------------
# command
# --------------------------------------------------------------------------

class CommandBackend:
    """Runs a program of your own. It is invoked as

        <command> sms-list --days N
        <command> sms-send --to NUMBER --text TEXT
        <command> calls-recent --limit N

    and must print the JSON shapes documented at the top of this module on
    stdout and exit 0. Anything on stderr with a non-zero exit is shown as the
    error. Useful for other SIP providers or a server that holds the
    credentials instead of this machine (e.g. `ssh host my-sms-script`)."""

    def __init__(self, cfg, timeout=60):
        cmd = cfg.get("command", "command", fallback="")
        if not cmd:
            raise BackendError("backend = command but [command] command is empty")
        self.argv = [_config.expand(a) for a in shlex.split(cmd)]
        self.timeout = timeout

    def _run(self, *args):
        try:
            r = subprocess.run(self.argv + list(args), capture_output=True,
                               text=True, timeout=self.timeout)
        except FileNotFoundError:
            raise BackendError("command not found: %s" % self.argv[0]) from None
        except subprocess.TimeoutExpired:
            raise BackendError("command timed out after %ds" % self.timeout) from None
        if r.returncode != 0:
            raise BackendError((r.stderr or r.stdout or "command failed").strip()[:300])
        try:
            return json.loads(r.stdout or "null")
        except ValueError:
            raise BackendError("command did not print JSON") from None

    def sms_list(self, days=30):
        return self._run("sms-list", "--days", str(int(days))) or []

    def sms_send(self, to, text):
        to = str(to).strip()
        r = self._run("sms-send", "--to", to if to.startswith("+") else nanp10(to),
                      "--text", text) or {}
        if r.get("status") != "success":
            raise BackendError("send failed: %s" % r.get("status"))
        return r

    def calls_recent(self, limit=3):
        return self._run("calls-recent", "--limit", str(int(limit))) or []


# --------------------------------------------------------------------------
# Twilio and SignalWire (same REST API)
# --------------------------------------------------------------------------

def e164(number, country_code="1"):
    """+CCNNN... for a number typed as 10 national digits, 11 with the NANP 1,
    or already international (leading + or 00)."""
    s = str(number or "").strip()
    d = digits(s)
    if not d:
        return ""
    if s.startswith("+"):
        return "+" + d
    if s.startswith("00"):
        return "+" + d[2:]
    cc = digits(country_code) or "1"
    if cc == "1" and len(d) == 11 and d.startswith("1"):
        return "+" + d
    return "+" + cc + d.lstrip("0")


class TwilioBackend:
    """Twilio's 2010-04-01 REST API. SignalWire exposes the same API under
    https://SPACE.signalwire.com/api/laml, so one class serves both."""

    name = "twilio"
    # Twilio message bodies may be up to 1600 characters (split by the carrier).
    max_len = 1600

    def __init__(self, cfg, secrets, section="twilio"):
        self.name = section
        if section == "signalwire":
            space = cfg.get("signalwire", "space", fallback="").strip()
            if not space:
                raise BackendError("signalwire: set [signalwire] space = in phone.conf "
                                   "(the NAME in NAME.signalwire.com)")
            base = cfg.get("signalwire", "api_url", fallback="") or \
                "https://%s.signalwire.com/api/laml" % space
            self.user = secrets.get("signalwire_project_id", "")
            self.password = secrets.get("signalwire_api_token", "")
        else:
            base = cfg.get("twilio", "api_url", fallback="") or "https://api.twilio.com"
            self.user = secrets.get("twilio_account_sid", "")
            self.password = secrets.get("twilio_auth_token", "")
        if not self.user or not self.password:
            raise BackendError("%s: phone.secrets is missing the account id or token "
                               "(see phone.secrets.example)" % section)
        self.base = base.rstrip("/") + "/2010-04-01/Accounts/%s" % urllib.parse.quote(self.user)
        self.cc = cfg.get("phone", "country_code", fallback="1")
        self.dids = _config.dids(cfg)
        self.timeout = cfg.getfloat(section, "timeout", fallback=30)
        self.user_agent = cfg.get("voipms", "user_agent", fallback="cosmic-tools-phone")

    def _req(self, path, params=None, form=None):
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        token = base64.b64encode(("%s:%s" % (self.user, self.password)).encode()).decode()
        headers = {"Authorization": "Basic " + token, "Accept": "application/json",
                   "User-Agent": self.user_agent}
        data = None
        if form is not None:
            data = urllib.parse.urlencode(form).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = json.loads(e.read().decode("utf-8", "replace")).get("message", "")
            except (ValueError, AttributeError, OSError):
                pass
            raise BackendError("%s: HTTP %d%s" % (self.name, e.code,
                                                  (": " + detail) if detail else "")) from None
        except (urllib.error.URLError, OSError) as e:
            raise BackendError("%s: network error: %s"
                               % (self.name, getattr(e, "reason", e))) from None
        try:
            return json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            raise BackendError("%s: response was not JSON" % self.name) from None

    def _did(self):
        if not self.dids:
            raise BackendError("no number configured: set did = in phone.conf")
        return e164(self.dids[0], self.cc)

    @staticmethod
    def _local(stamp):
        try:
            return email.utils.parsedate_to_datetime(stamp).astimezone() \
                .strftime("%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            return stamp or ""

    def _display(self, number):
        n = e164(number, self.cc)
        cc = digits(self.cc) or "1"
        return n[1 + len(cc):] if n.startswith("+" + cc) else n

    def sms_list(self, days=30):
        since = (datetime.date.today() - datetime.timedelta(days=int(days))).isoformat()
        me = self._did()
        seen, out = set(), []
        for side in ("To", "From"):
            data = self._req("/Messages.json", {side: me, "DateSent>": since, "PageSize": 1000})
            for m in data.get("messages") or []:
                if m.get("sid") in seen:
                    continue
                seen.add(m.get("sid"))
                inbound = str(m.get("direction", "")).startswith("inbound")
                out.append({
                    "id": m.get("sid", ""),
                    "dir": "in" if inbound else "out",
                    "contact": self._display(m.get("from") if inbound else m.get("to")),
                    "date": self._local(m.get("date_sent") or m.get("date_created")),
                    "message": m.get("body", "") or "",
                })
        out.sort(key=lambda m: m["date"], reverse=True)
        return out

    def sms_send(self, to, text):
        dst = e164(to, self.cc)
        if not dst:
            raise BackendError("send: no destination number")
        if not text:
            raise BackendError("send: empty message")
        if len(text) > self.max_len:
            raise BackendError("send: message is %d characters; the limit is %d"
                               % (len(text), self.max_len))
        data = self._req("/Messages.json", form={"From": self._did(), "To": dst, "Body": text})
        return {"status": "success", "id": data.get("sid")}

    def calls_recent(self, limit=3, days=30):
        since = (datetime.date.today() - datetime.timedelta(days=int(days))).isoformat()
        data = self._req("/Calls.json", {"StartTime>": since, "PageSize": max(int(limit) * 4, 20)})
        out = []
        for c in data.get("calls") or []:
            inbound = str(c.get("direction", "")).startswith("inbound")
            try:
                secs = int(c.get("duration") or 0)
            except (TypeError, ValueError):
                secs = 0
            status = str(c.get("status", ""))
            out.append({
                "when": self._local(c.get("start_time") or c.get("date_created")),
                "peer": self._display(c.get("from") if inbound else c.get("to")),
                "inbound": inbound,
                "seconds": secs,
                "disposition": {"completed": "ANSWERED", "no-answer": "NO ANSWER",
                                "busy": "BUSY", "failed": "FAILED",
                                "canceled": "NO ANSWER"}.get(status, status.upper()),
                "voicemail": False,
            })
        out.sort(key=lambda c: c["when"], reverse=True)
        return out[:int(limit)]

    def whoami(self):
        data = self._req(".json")
        return data.get("friendly_name") or data.get("sid") or ""


# --------------------------------------------------------------------------
# KDE Connect (your own phone's SMS plan)
# --------------------------------------------------------------------------

class KdeConnectBackend:
    """Sends texts from your paired Android phone with `kdeconnect-cli`
    (KDE Connect's command-line client). It can only send: KDE Connect has
    no command-line access to message history or the call log, so those
    views stay empty with a note."""

    def __init__(self, cfg, timeout=30):
        self.cli = _config.expand(cfg.get("kdeconnect", "cli", fallback="kdeconnect-cli")) \
            or "kdeconnect-cli"
        self.device = cfg.get("kdeconnect", "device", fallback="").strip()
        self.timeout = timeout

    def _run(self, *args):
        try:
            r = subprocess.run([self.cli] + list(args), capture_output=True,
                               text=True, timeout=self.timeout)
        except FileNotFoundError:
            raise BackendError("kdeconnect-cli not found (sudo apt install kdeconnect)") from None
        except subprocess.TimeoutExpired:
            raise BackendError("kdeconnect-cli timed out") from None
        if r.returncode != 0:
            raise BackendError("kdeconnect-cli: %s"
                               % (r.stderr or r.stdout or "failed").strip()[:300])
        return r.stdout

    def _device(self):
        if self.device:
            return self.device
        ids = [l.strip() for l in self._run("--list-available", "--id-only").splitlines()
               if l.strip()]
        if not ids:
            raise BackendError("kdeconnect: no paired phone is reachable")
        if len(ids) > 1:
            raise BackendError("kdeconnect: several devices available; set "
                               "[kdeconnect] device = in phone.conf (kdeconnect-cli -a)")
        return ids[0]

    def sms_list(self, days=30):
        raise BackendError("kdeconnect backend can send texts but cannot read "
                           "message history; read replies on your phone")

    def sms_send(self, to, text):
        if not digits(to):
            raise BackendError("send: no destination number")
        if not text:
            raise BackendError("send: empty message")
        self._run("--device", self._device(), "--destination", str(to).strip(),
                  "--send-sms", text)
        return {"status": "success", "id": None}

    def calls_recent(self, limit=3):
        return []

    def whoami(self):
        return self._device()


BACKENDS = ("voipms", "twilio", "signalwire", "kdeconnect", "command")


def get(cfg=None, secrets=None):
    cfg = cfg or _config.load()
    kind = cfg.get("phone", "backend", fallback="voipms").strip().lower()
    if kind in ("voipms", "twilio", "signalwire") and secrets is None:
        secrets = _config.load_secrets()
    if kind == "voipms":
        return VoipMsBackend(cfg, secrets)
    if kind in ("twilio", "signalwire"):
        return TwilioBackend(cfg, secrets, section=kind)
    if kind == "kdeconnect":
        return KdeConnectBackend(cfg)
    if kind == "command":
        return CommandBackend(cfg)
    raise BackendError("unknown backend %r (one of: %s)" % (kind, ", ".join(BACKENDS)))
