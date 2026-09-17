"""Contacts import tests with fake vCard/CSV data (555-01xx fictional numbers)."""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "lib"))

from cosmic_phone import contacts  # noqa: E402

FAKE_VCF = (
    "BEGIN:VCARD\r\n"
    "VERSION:3.0\r\n"
    "FN:Robin\r\n"
    "  Testperson\r\n"                          # folded line (RFC 6350 unfolding)
    "ORG:Fictional Labs\\, Inc.;R&D\r\n"
    "item1.TEL;TYPE=CELL:+1 (973) 555-0142\r\n"  # grouped property
    "TEL;TYPE=HOME:973.555.0143\r\n"
    "END:VCARD\r\n"
    "BEGIN:VCARD\r\n"
    "VERSION:4.0\r\n"
    "N:Nobody;Nemo;;;\r\n"
    "TEL;VALUE=uri:tel:+1-415-555-0123\r\n"
    "END:VCARD\r\n"
    "BEGIN:VCARD\r\n"
    "VERSION:3.0\r\n"
    "TEL:+1 415 555 0124\r\n"                   # no name, no org: skipped
    "END:VCARD\r\n"
    "BEGIN:VCARD\r\n"
    "VERSION:3.0\r\n"
    "FN:International Friend\r\n"
    "TEL:+44 20 7946 0000\r\n"
    "END:VCARD\r\n"
)

FAKE_CSV = "Name,Phone,Company\nRobin Corrected,973-555-0142,\n# comment,,\nBad Row,12,\n"


class ParseTests(unittest.TestCase):
    def test_vcf(self):
        cards = contacts.parse_vcf(FAKE_VCF)
        self.assertEqual(len(cards), 4)
        name, org, tels = cards[0]
        self.assertEqual(name, "Robin Testperson")
        self.assertEqual(org, "Fictional Labs, Inc.")
        self.assertEqual(len(tels), 2)
        self.assertEqual(cards[1][0], "Nemo Nobody")            # N fallback
        self.assertEqual(cards[1][2], ["+1-415-555-0123"])       # tel: uri stripped

    def test_csv_header_detection(self):
        rows = contacts.parse_csv(FAKE_CSV)
        self.assertEqual(rows[0], ("Robin Corrected", "", ["973-555-0142"]))
        rows = contacts.parse_csv("Pat,(212) 555-0199,Acme Placeholder\n")
        self.assertEqual(rows, [("Pat", "Acme Placeholder", ["(212) 555-0199"])])

    def test_normalise(self):
        self.assertEqual(contacts.normalise("+1 (973) 555-0142"), "9735550142")
        self.assertEqual(contacts.normalise("442079460000"), "442079460000")
        self.assertEqual(contacts.normalise("12"), "")


class BuildTests(unittest.TestCase):
    def test_build_precedence_and_lookup(self):
        book = contacts.build([("vcf", FAKE_VCF), ("csv", FAKE_CSV)])
        self.assertEqual(book["9735550142"]["name"], "Robin Corrected")   # CSV wins
        self.assertEqual(book["9735550143"]["name"], "Robin Testperson")
        self.assertEqual(book["4155550123"]["name"], "Nemo Nobody")
        self.assertNotIn("4155550124", book)
        self.assertIn("442079460000", book)

        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "book.json")
            with open(path, "w") as f:
                json.dump(book, f)
            self.assertEqual(contacts.label("973-555-0143", path),
                             "Robin Testperson - Fictional Labs, Inc.")
            self.assertEqual(contacts.label("+1 973 555 0142", path), "Robin Corrected")
            self.assertEqual(contacts.label("2125550100", path), "NY")       # area code only
            self.assertEqual(contacts.label("8005550100", path), "toll-free")
            self.assertIsNone(contacts.lookup("2125550100", path))
            self.assertEqual(contacts.label("442079460001", path), "")

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            vcf, csvf, out = (os.path.join(d, n) for n in ("a.vcf", "b.csv", "book.json"))
            with open(vcf, "w") as f:
                f.write(FAKE_VCF)
            with open(csvf, "w") as f:
                f.write(FAKE_CSV)
            env = dict(os.environ, COSMIC_PHONE_CONF=os.path.join(d, "none.conf"))
            r = subprocess.run([sys.executable, os.path.join(ROOT, "bin", "cosmic-contacts-build"),
                                "--vcf", vcf, "--csv", csvf, "--out", out],
                               env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(stat.S_IMODE(os.stat(out).st_mode), 0o600)
            with open(out) as f:
                self.assertEqual(len(json.load(f)), 4)

    def test_example_files(self):
        with open(os.path.join(ROOT, "examples", "contacts.vcf")) as f:
            v = f.read()
        with open(os.path.join(ROOT, "examples", "contacts.csv")) as f:
            c = f.read()
        book = contacts.build([("vcf", v), ("csv", c)])
        self.assertEqual(book["9735550142"]["company"], "Sample Co")
        self.assertEqual(book["4155550123"]["name"], "Pat Placeholder")
        self.assertEqual(book["2015550100"]["name"], "Jane Example")


if __name__ == "__main__":
    unittest.main()
