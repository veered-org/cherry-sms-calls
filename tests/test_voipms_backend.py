"""voip.ms backend tests against a local mock HTTP server. No real API calls."""
import configparser
import datetime
import http.server
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "lib"))

from cosmic_phone import backend, baresip, config  # noqa: E402

USER = "tester@example.com"
PASSWORD = "not-a-real-password-123"
DID = "2015550100"


class Mock(http.server.BaseHTTPRequestHandler):
    requests = []          # (http_method, path_without_query, params, headers)
    responses = {}         # api method -> dict or ("http", code)

    def log_message(self, *a):
        pass

    def _handle(self, params, verb):
        ua = self.headers.get("User-Agent", "")
        Mock.requests.append((verb, self.path.split("?", 1)[0], params, dict(self.headers)))
        # Emulate voip.ms: the default urllib User-Agent is refused.
        if ua.startswith("Python-urllib"):
            self.send_response(403)
            self.end_headers()
            return
        if params.get("api_username") != USER or params.get("api_password") != PASSWORD:
            return self._json({"status": "invalid_credentials"})
        r = Mock.responses.get(params.get("method"), {"status": "invalid_method"})
        if isinstance(r, tuple):
            self.send_response(r[1])
            self.end_headers()
            return
        self._json(r)

    def _json(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        q = urllib.parse.urlsplit(self.path).query
        self._handle(dict(urllib.parse.parse_qsl(q)), "GET")

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self._handle(dict(urllib.parse.parse_qsl(self.rfile.read(n).decode())), "POST")


def make_cfg(url, **overrides):
    cp = configparser.ConfigParser(interpolation=None)
    cp.read_dict(config.DEFAULTS)
    cp.set("phone", "did", DID)
    cp.set("voipms", "api_url", url)
    cp.set("voipms", "timezone_is_utc", "false")
    for key, val in overrides.items():
        sec, opt = key.split("__")
        cp.set(sec, opt, val)
    return cp


class VoipMsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Mock)
        cls.url = "http://127.0.0.1:%d/api/v1/rest.php" % cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        Mock.requests.clear()
        Mock.responses = {}

    def be(self, **kw):
        return backend.VoipMsBackend(make_cfg(self.url, **kw),
                                     {"api_username": USER, "api_password": PASSWORD})

    # -- transport ---------------------------------------------------------
    def test_custom_user_agent_is_sent(self):
        Mock.responses["getIP"] = {"status": "success", "ip": "203.0.113.7"}
        self.assertEqual(self.be().whoami(), "203.0.113.7")
        ua = Mock.requests[0][3].get("User-Agent")
        self.assertTrue(ua and not ua.startswith("Python-urllib"), ua)

    def test_default_urllib_agent_would_be_refused(self):
        Mock.responses["getIP"] = {"status": "success", "ip": "203.0.113.7"}
        b = self.be(voipms__user_agent="Python-urllib/3.12")
        with self.assertRaises(backend.BackendError) as cm:
            b.whoami()
        self.assertIn("HTTP 403", str(cm.exception))
        self.assertNotIn(PASSWORD, str(cm.exception))

    def test_post_keeps_credentials_out_of_url(self):
        Mock.responses["getIP"] = {"status": "success", "ip": "203.0.113.7"}
        self.be(voipms__http_method="POST").whoami()
        verb, path, params, _ = Mock.requests[0]
        self.assertEqual(verb, "POST")
        self.assertNotIn("api_password", path)
        self.assertEqual(params["api_password"], PASSWORD)

    def test_error_status_raised_without_password(self):
        b = backend.VoipMsBackend(make_cfg(self.url),
                                  {"api_username": USER, "api_password": "wrong"})
        with self.assertRaises(backend.BackendError) as cm:
            b.sms_list()
        msg = str(cm.exception)
        self.assertIn("invalid_credentials", msg)
        self.assertNotIn("wrong", msg)

    def test_ip_not_enabled_hint(self):
        Mock.responses["getSMS"] = {"status": "ip_not_enabled"}
        with self.assertRaises(backend.BackendError) as cm:
            self.be().sms_list()
        self.assertIn("allowlist", str(cm.exception))

    def test_http_500(self):
        Mock.responses["getCDR"] = ("http", 500)
        with self.assertRaises(backend.BackendError):
            self.be().calls_recent()

    def test_missing_credentials_no_request(self):
        b = backend.VoipMsBackend(make_cfg(self.url), {})
        with self.assertRaises(backend.BackendError):
            b.sms_list()
        self.assertEqual(Mock.requests, [])

    # -- SMS ---------------------------------------------------------------
    def test_sms_list(self):
        Mock.responses["getSMS"] = {"status": "success", "sms": [
            {"id": "11", "date": "2030-01-01 09:00:00", "type": "1", "did": DID,
             "contact": "12125550199", "message": "hello"},
            {"id": "12", "date": "2030-01-02 10:00:00", "type": "0", "did": DID,
             "contact": "2125550199", "message": "reply"},
        ]}
        msgs = self.be().sms_list(7)
        self.assertEqual([m["id"] for m in msgs], ["12", "11"])     # newest first
        self.assertEqual(msgs[1]["dir"], "in")
        self.assertEqual(msgs[0]["dir"], "out")
        self.assertEqual(msgs[1]["contact"], "2125550199")            # leading 1 stripped
        p = Mock.requests[0][2]
        self.assertEqual(p["method"], "getSMS")
        self.assertEqual(p["did"], DID)
        self.assertEqual(p["from"], (datetime.date.today() - datetime.timedelta(days=7)).isoformat())
        self.assertEqual(p["timezone"], "-1")

    def test_sms_list_empty(self):
        Mock.responses["getSMS"] = {"status": "no_sms"}
        self.assertEqual(self.be().sms_list(), [])

    def test_sms_utc_conversion(self):
        Mock.responses["getSMS"] = {"status": "success", "sms": [
            {"id": "1", "date": "2030-06-01 12:00:00", "type": "1", "contact": "2125550199", "message": "x"}]}
        got = self.be(voipms__timezone_is_utc="true").sms_list()[0]["date"]
        want = (datetime.datetime(2030, 6, 1, 12, 0, tzinfo=datetime.timezone.utc)
                .astimezone().strftime("%Y-%m-%d %H:%M:%S"))
        self.assertEqual(got, want)

    def test_sms_send(self):
        Mock.responses["sendSMS"] = {"status": "success", "sms": 987}
        r = self.be().sms_send("+1 (212) 555-0199", "hi there")
        self.assertEqual(r, {"status": "success", "id": 987})
        p = Mock.requests[0][2]
        self.assertEqual((p["method"], p["did"], p["dst"], p["message"]),
                         ("sendSMS", DID, "2125550199", "hi there"))

    def test_sms_send_too_long_makes_no_request(self):
        with self.assertRaises(backend.BackendError):
            self.be().sms_send("2125550199", "x" * 161)
        self.assertEqual(Mock.requests, [])

    def test_sms_send_failure_status(self):
        Mock.responses["sendSMS"] = {"status": "invalid_dst", "message": "not a valid destination"}
        with self.assertRaises(backend.BackendError) as cm:
            self.be().sms_send("2125550199", "hi")
        self.assertIn("invalid_dst", str(cm.exception))

    def test_no_did(self):
        b = self.be(phone__did="")
        with self.assertRaises(backend.BackendError):
            b.sms_list()

    # -- CDR ---------------------------------------------------------------
    def test_calls_recent(self):
        Mock.responses["getCDR"] = {"status": "success", "cdr": [
            {"date": "2030-01-01 09:00:00", "callerid": '"CALLER" <2125550199>',
             "destination": "1" + DID, "disposition": "ANSWERED", "seconds": "75",
             "description": "Inbound DID"},
            {"date": "2030-01-03 11:00:00", "callerid": DID, "destination": "14155550123",
             "disposition": "ANSWERED", "seconds": "30", "description": "Outbound"},
            {"date": "2030-01-02 10:00:00", "callerid": "<2125550198>", "destination": DID,
             "disposition": "ANSWERED", "seconds": "40", "call_logs": "Routing to voicemail"},
            {"date": "2029-12-01 10:00:00", "callerid": "2125550197", "destination": DID,
             "disposition": "NO ANSWER", "seconds": "0"},
        ]}
        calls = self.be().calls_recent(limit=3)
        self.assertEqual(len(calls), 3)
        self.assertEqual([c["when"][:10] for c in calls], ["2030-01-03", "2030-01-02", "2030-01-01"])
        out, vm, inb = calls
        self.assertFalse(out["inbound"])
        self.assertEqual(out["peer"], "4155550123")
        self.assertTrue(vm["inbound"] and vm["voicemail"])
        self.assertEqual(vm["peer"], "2125550198")
        self.assertTrue(inb["inbound"])
        self.assertFalse(inb["voicemail"])
        self.assertEqual((inb["peer"], inb["seconds"]), ("2125550199", 75))
        p = Mock.requests[0][2]
        for flag in ("answered", "noanswer", "busy", "failed"):
            self.assertEqual(p[flag], "1")
        self.assertIn("date_from", p)
        self.assertIn("date_to", p)

    def test_calls_empty(self):
        Mock.responses["getCDR"] = {"status": "no_cdr"}
        self.assertEqual(self.be().calls_recent(), [])

    # -- CLI end to end ----------------------------------------------------
    def test_cli_check_and_list(self):
        Mock.responses["getIP"] = {"status": "success", "ip": "203.0.113.7"}
        Mock.responses["getSMS"] = {"status": "no_sms"}
        with tempfile.TemporaryDirectory() as d:
            conf = os.path.join(d, "phone.conf")
            sec = os.path.join(d, "phone.secrets")
            with open(conf, "w") as f:
                f.write("[phone]\ndid = %s\n[voipms]\napi_url = %s\n" % (DID, self.url))
            with open(sec, "w") as f:
                f.write("# comment\napi_username = %s\napi_password = %s\n" % (USER, PASSWORD))
            os.chmod(sec, 0o600)
            env = dict(os.environ, COSMIC_PHONE_CONF=conf, COSMIC_PHONE_SECRETS=sec)
            cli = os.path.join(ROOT, "bin", "cosmic-phone")
            r = subprocess.run([sys.executable, cli, "check"], env=env,
                               capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("203.0.113.7", r.stdout)
            r = subprocess.run([sys.executable, cli, "sms", "list"], env=env,
                               capture_output=True, text=True, timeout=30)
            self.assertEqual((r.returncode, json.loads(r.stdout)), (0, []))
            os.chmod(sec, 0o644)
            r = subprocess.run([sys.executable, cli, "check"], env=env,
                               capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 1)
            self.assertIn("WORLD-READABLE", r.stdout)


class SecretsTests(unittest.TestCase):
    def test_modes(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s")
            with open(p, "w") as f:
                f.write("api_username = a@example.com\napi_password = p=w=d\n")
            os.chmod(p, 0o600)
            self.assertEqual(config.check_secrets_mode(p), "")
            warnings = []
            s = config.load_secrets(p, warn=warnings.append)
            self.assertEqual(s["api_password"], "p=w=d")
            self.assertEqual(warnings, [])
            os.chmod(p, 0o640)
            self.assertIn("other users", config.check_secrets_mode(p))
            os.chmod(p, 0o644)
            config.load_secrets(p, warn=warnings.append)
            self.assertTrue(warnings and "WORLD-READABLE" in warnings[0])
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o644)   # never changed for you

    def test_missing(self):
        with self.assertRaises(config.ConfigError):
            config.load_secrets("/nonexistent/phone.secrets")


class CommandBackendTests(unittest.TestCase):
    def cfg(self, cmd):
        cp = configparser.ConfigParser(interpolation=None)
        cp.read_dict(config.DEFAULTS)
        cp.set("phone", "backend", "command")
        cp.set("command", "command", cmd)
        return cp

    def test_example_script(self):
        b = backend.get(self.cfg(os.path.join(ROOT, "examples", "command-backend.sh")))
        self.assertIsInstance(b, backend.CommandBackend)
        self.assertEqual(b.sms_list(3)[0]["dir"], "in")
        self.assertEqual(b.sms_send("2125550199", "hi")["status"], "success")
        self.assertTrue(b.calls_recent(1)[0]["inbound"])

    def test_failure(self):
        b = backend.get(self.cfg("sh -c 'echo boom >&2; exit 3' --"))
        with self.assertRaises(backend.BackendError) as cm:
            b.sms_list()
        self.assertIn("boom", str(cm.exception))


class NetstringTests(unittest.TestCase):
    def test_roundtrip(self):
        import socket
        a, b = socket.socketpair()
        payloads = [json.dumps({"event": True, "type": "CALL_INCOMING"}).encode(),
                    json.dumps({"token": "x", "data": "no active calls"}).encode()]
        a.sendall(b"".join(baresip.netstring(p) for p in payloads))
        a.close()
        got = [p for p in baresip.read_netstrings(b) if p is not None]
        b.close()
        self.assertEqual(got, payloads)
        cmd = baresip.command_bytes("dial", "2125550199")
        self.assertTrue(cmd.endswith(b",") and b'"dial"' in cmd)


if __name__ == "__main__":
    unittest.main()
