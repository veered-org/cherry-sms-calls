"""Twilio / SignalWire / KDE Connect backend tests. Mock server on 127.0.0.1
and a fake kdeconnect-cli script; no real API calls, no real phone."""
import base64
import configparser
import http.server
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "lib"))

from cosmic_phone import backend, config  # noqa: E402

SID, TOKEN = "AC00000000000000000000000000000000", "not-a-real-token-456"
DID = "2015550100"


class Mock(http.server.BaseHTTPRequestHandler):
    requests = []
    routes = {}

    def log_message(self, *a):
        pass

    def _go(self, verb, form):
        u = urllib.parse.urlsplit(self.path)
        Mock.requests.append((verb, u.path, dict(urllib.parse.parse_qsl(u.query)), form,
                              dict(self.headers)))
        want = "Basic " + base64.b64encode(("%s:%s" % (SID, TOKEN)).encode()).decode()
        if self.headers.get("Authorization") != want:
            return self._json(401, {"code": 20003, "message": "Authenticate"})
        for suffix, (code, body) in Mock.routes.items():
            if u.path.endswith(suffix):
                return self._json(code, body(u, form) if callable(body) else body)
        self._json(404, {"message": "not found"})

    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        self._go("GET", None)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self._go("POST", dict(urllib.parse.parse_qsl(self.rfile.read(n).decode())))


def cfg(kind, url, **extra):
    cp = configparser.ConfigParser(interpolation=None)
    cp.read_dict(config.DEFAULTS)
    cp.set("phone", "backend", kind)
    cp.set("phone", "did", DID)
    if kind == "twilio":
        cp.set("twilio", "api_url", url)
    elif kind == "signalwire":
        cp.set("signalwire", "space", "example")
        cp.set("signalwire", "api_url", url + "/api/laml")
    for k, v in extra.items():
        s, o = k.split("__")
        cp.set(s, o, v)
    return cp


class E164Tests(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(backend.e164("201-555-0100"), "+12015550100")
        self.assertEqual(backend.e164("1 (201) 555-0100"), "+12015550100")
        self.assertEqual(backend.e164("+44 20 7946 0000"), "+442079460000")
        self.assertEqual(backend.e164("0044 20 7946 0000"), "+442079460000")
        self.assertEqual(backend.e164("020 7946 0000", "44"), "+442079460000")
        self.assertEqual(backend.e164(""), "")


class TwilioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Mock)
        cls.url = "http://127.0.0.1:%d" % cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        Mock.requests.clear()
        Mock.routes = {}

    def be(self, kind="twilio", secrets=None, **extra):
        if secrets is None:
            secrets = ({"twilio_account_sid": SID, "twilio_auth_token": TOKEN} if kind == "twilio"
                       else {"signalwire_project_id": SID, "signalwire_api_token": TOKEN})
        return backend.get(cfg(kind, self.url, **extra), secrets)

    def test_get_dispatch(self):
        self.assertIsInstance(self.be(), backend.TwilioBackend)
        self.assertIsInstance(self.be("signalwire"), backend.TwilioBackend)

    def test_missing_secrets(self):
        with self.assertRaises(backend.BackendError):
            self.be(secrets={})

    def test_send(self):
        Mock.routes["/Messages.json"] = (201, {"sid": "SM1", "status": "queued"})
        r = self.be().sms_send("201 555 0199", "hello")
        self.assertEqual(r, {"status": "success", "id": "SM1"})
        verb, path, _, form, _ = Mock.requests[0]
        self.assertEqual(verb, "POST")
        self.assertEqual(path, "/2010-04-01/Accounts/%s/Messages.json" % SID)
        self.assertEqual(form, {"From": "+12015550100", "To": "+12015550199", "Body": "hello"})

    def test_send_international_keeps_plus(self):
        Mock.routes["/Messages.json"] = (201, {"sid": "SM2"})
        self.be().sms_send("+972 50 555 0100", "shalom")
        self.assertEqual(Mock.requests[0][3]["To"], "+972505550100")

    def test_signalwire_path(self):
        Mock.routes["/Messages.json"] = (201, {"sid": "SW1"})
        self.be("signalwire").sms_send("2015550199", "hi")
        self.assertEqual(Mock.requests[0][1],
                         "/api/laml/2010-04-01/Accounts/%s/Messages.json" % SID)

    def test_signalwire_needs_space(self):
        c = cfg("signalwire", self.url)
        c.set("signalwire", "space", "")
        with self.assertRaises(backend.BackendError):
            backend.get(c, {"signalwire_project_id": SID, "signalwire_api_token": TOKEN})

    def test_http_error_message_no_token(self):
        b = self.be(secrets={"twilio_account_sid": SID, "twilio_auth_token": "wrong"})
        with self.assertRaises(backend.BackendError) as cm:
            b.sms_send("2015550199", "x")
        self.assertIn("HTTP 401", str(cm.exception))
        self.assertIn("Authenticate", str(cm.exception))
        self.assertNotIn("wrong", str(cm.exception))

    def test_length_limit(self):
        with self.assertRaises(backend.BackendError):
            self.be().sms_send("2015550199", "x" * 1601)

    def test_sms_list_merges_both_directions(self):
        def msgs(u, form):
            q = dict(urllib.parse.parse_qsl(u.query))
            self.assertIn("DateSent>", q)
            if "To" in q:
                return {"messages": [
                    {"sid": "SM9", "direction": "inbound", "from": "+12015550111",
                     "to": "+12015550100", "body": "in", "date_sent": "Wed, 16 Sep 2026 10:00:00 +0000"}]}
            return {"messages": [
                {"sid": "SM8", "direction": "outbound-api", "from": "+12015550100",
                 "to": "+442079460000", "body": "out", "date_sent": "Wed, 16 Sep 2026 11:00:00 +0000"},
                {"sid": "SM9", "direction": "inbound", "from": "+12015550111",
                 "to": "+12015550100", "body": "in", "date_sent": "Wed, 16 Sep 2026 10:00:00 +0000"}]}
        Mock.routes["/Messages.json"] = (200, msgs)
        out = self.be().sms_list(7)
        self.assertEqual([m["id"] for m in out], ["SM8", "SM9"])
        self.assertEqual(out[0]["dir"], "out")
        self.assertEqual(out[0]["contact"], "+442079460000")
        self.assertEqual(out[1]["contact"], "2015550111")
        self.assertRegex(out[1]["date"], r"^2026-09-1[67] \d\d:\d\d:\d\d$")

    def test_calls_recent(self):
        Mock.routes["/Calls.json"] = (200, {"calls": [
            {"direction": "inbound", "from": "+12015550122", "to": "+12015550100",
             "duration": "65", "status": "completed", "start_time": "Tue, 15 Sep 2026 09:00:00 +0000"},
            {"direction": "outbound-dial", "from": "+12015550100", "to": "+12015550133",
             "duration": "0", "status": "no-answer", "start_time": "Wed, 16 Sep 2026 09:00:00 +0000"}]})
        out = self.be().calls_recent(5)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["peer"], "2015550133")
        self.assertFalse(out[0]["inbound"])
        self.assertEqual(out[0]["disposition"], "NO ANSWER")
        self.assertTrue(out[1]["inbound"])
        self.assertEqual(out[1]["seconds"], 65)


class KdeConnectTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.log = os.path.join(self.dir, "args.json")
        self.cli = os.path.join(self.dir, "kdeconnect-cli")
        self.devices = os.path.join(self.dir, "devices")
        with open(self.devices, "w") as f:
            f.write("abc123\n")
        with open(self.cli, "w") as f:
            f.write("#!%s\nimport json,sys\nd=%r\nif '--list-available' in sys.argv:\n"
                    "    print(open(d).read(), end='')\nelse:\n"
                    "    json.dump(sys.argv[1:], open(%r,'w'))\n"
                    % (sys.executable, self.devices, self.log))
        os.chmod(self.cli, stat.S_IRWXU)

    def be(self, device=""):
        c = cfg("kdeconnect", "", kdeconnect__cli=self.cli, kdeconnect__device=device)
        return backend.get(c)

    def test_send_autodetects_single_device(self):
        self.assertEqual(self.be().sms_send("+1 201 555 0100", "hi"), {"status": "success", "id": None})
        self.assertEqual(json.load(open(self.log)),
                         ["--device", "abc123", "--destination", "+1 201 555 0100", "--send-sms", "hi"])

    def test_configured_device(self):
        self.be("phone9").sms_send("2015550100", "yo")
        self.assertEqual(json.load(open(self.log))[1], "phone9")

    def test_several_devices_need_config(self):
        with open(self.devices, "w") as f:
            f.write("a\nb\n")
        with self.assertRaises(backend.BackendError):
            self.be().sms_send("2015550100", "x")

    def test_no_history(self):
        with self.assertRaises(backend.BackendError):
            self.be().sms_list()
        self.assertEqual(self.be().calls_recent(), [])

    def test_missing_cli(self):
        c = cfg("kdeconnect", "", kdeconnect__cli=os.path.join(self.dir, "nope"))
        with self.assertRaises(backend.BackendError):
            backend.get(c).sms_send("2015550100", "x")


if __name__ == "__main__":
    unittest.main()
