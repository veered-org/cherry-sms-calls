"""contacts - offline name lookup for phone numbers.

Two tiers, best first:

  1. YOUR PHONE BOOK - a JSON file built by `cosmic-contacts-build` from a
     vCard export (.vcf) and/or a CSV of name,number[,company].
  2. AREA CODE - the NANP state/province (or "toll-free"), so an unknown
     number still says something. Screening context only; never presented
     as an identity.

The book is prebuilt rather than parsed while a window opens, so a large
address book never slows the dialer down.
"""
import csv
import io
import json
import os
import re

# NANP area code -> US state / Canadian province. Public numbering-plan data.
AREA = {
    "201": "NJ", "551": "NJ", "609": "NJ", "640": "NJ", "732": "NJ",
    "848": "NJ", "856": "NJ", "862": "NJ", "908": "NJ", "973": "NJ",
    "212": "NY", "332": "NY", "347": "NY", "516": "NY", "518": "NY",
    "585": "NY", "607": "NY", "631": "NY", "646": "NY", "680": "NY",
    "716": "NY", "718": "NY", "838": "NY", "845": "NY", "914": "NY",
    "917": "NY", "929": "NY", "934": "NY",
    "215": "PA", "223": "PA", "267": "PA", "272": "PA", "412": "PA",
    "445": "PA", "484": "PA", "570": "PA", "610": "PA", "717": "PA",
    "724": "PA", "814": "PA", "878": "PA",
    "203": "CT", "475": "CT", "860": "CT", "959": "CT",
    "339": "MA", "351": "MA", "413": "MA", "508": "MA", "617": "MA",
    "774": "MA", "781": "MA", "857": "MA", "978": "MA",
    "302": "DE", "202": "DC", "227": "MD", "240": "MD", "301": "MD",
    "410": "MD", "443": "MD", "667": "MD",
    "276": "VA", "434": "VA", "540": "VA", "571": "VA", "703": "VA",
    "757": "VA", "804": "VA", "826": "VA", "948": "VA",
    "239": "FL", "305": "FL", "321": "FL", "352": "FL", "386": "FL",
    "407": "FL", "561": "FL", "656": "FL", "689": "FL", "727": "FL",
    "754": "FL", "772": "FL", "786": "FL", "813": "FL", "850": "FL",
    "863": "FL", "904": "FL", "941": "FL", "954": "FL",
    "229": "GA", "404": "GA", "470": "GA", "478": "GA", "678": "GA",
    "706": "GA", "762": "GA", "770": "GA", "912": "GA", "943": "GA",
    "205": "AL", "251": "AL", "256": "AL", "334": "AL", "659": "AL",
    "252": "NC", "336": "NC", "704": "NC", "743": "NC", "828": "NC",
    "910": "NC", "919": "NC", "980": "NC", "984": "NC",
    "803": "SC", "839": "SC", "843": "SC", "854": "SC", "864": "SC",
    "423": "TN", "615": "TN", "629": "TN", "731": "TN", "865": "TN",
    "901": "TN", "931": "TN",
    "270": "KY", "364": "KY", "502": "KY", "606": "KY", "859": "KY",
    "216": "OH", "220": "OH", "234": "OH", "326": "OH", "330": "OH",
    "380": "OH", "419": "OH", "440": "OH", "513": "OH", "567": "OH",
    "614": "OH", "740": "OH", "937": "OH",
    "219": "IN", "260": "IN", "317": "IN", "463": "IN", "574": "IN",
    "765": "IN", "812": "IN", "930": "IN",
    "217": "IL", "224": "IL", "309": "IL", "312": "IL", "331": "IL",
    "447": "IL", "464": "IL", "618": "IL", "630": "IL", "708": "IL",
    "730": "IL", "773": "IL", "779": "IL", "815": "IL", "847": "IL",
    "872": "IL",
    "231": "MI", "248": "MI", "269": "MI", "313": "MI", "517": "MI",
    "586": "MI", "616": "MI", "679": "MI", "734": "MI", "810": "MI",
    "906": "MI", "947": "MI", "989": "MI",
    "262": "WI", "274": "WI", "414": "WI", "534": "WI", "608": "WI",
    "715": "WI", "920": "WI",
    "218": "MN", "320": "MN", "507": "MN", "612": "MN", "651": "MN",
    "763": "MN", "952": "MN",
    "319": "IA", "515": "IA", "563": "IA", "641": "IA", "712": "IA",
    "314": "MO", "417": "MO", "557": "MO", "573": "MO", "636": "MO",
    "660": "MO", "816": "MO", "975": "MO",
    "316": "KS", "620": "KS", "785": "KS", "913": "KS",
    "308": "NE", "402": "NE", "531": "NE",
    "210": "TX", "214": "TX", "254": "TX", "281": "TX", "325": "TX",
    "346": "TX", "361": "TX", "409": "TX", "430": "TX", "432": "TX",
    "469": "TX", "512": "TX", "682": "TX", "713": "TX", "726": "TX",
    "737": "TX", "806": "TX", "817": "TX", "830": "TX", "832": "TX",
    "903": "TX", "915": "TX", "936": "TX", "940": "TX", "945": "TX",
    "956": "TX", "972": "TX", "979": "TX",
    "405": "OK", "539": "OK", "572": "OK", "580": "OK", "918": "OK",
    "225": "LA", "318": "LA", "337": "LA", "504": "LA", "985": "LA",
    "228": "MS", "601": "MS", "662": "MS", "769": "MS",
    "479": "AR", "501": "AR", "870": "AR",
    "303": "CO", "719": "CO", "720": "CO", "970": "CO", "983": "CO",
    "385": "UT", "435": "UT", "801": "UT",
    "480": "AZ", "520": "AZ", "602": "AZ", "623": "AZ", "928": "AZ",
    "505": "NM", "575": "NM",
    "702": "NV", "725": "NV", "775": "NV",
    "208": "ID", "986": "ID", "406": "MT", "307": "WY",
    "701": "ND", "605": "SD",
    "209": "CA", "213": "CA", "279": "CA", "310": "CA", "323": "CA",
    "341": "CA", "350": "CA", "408": "CA", "415": "CA", "424": "CA",
    "442": "CA", "510": "CA", "530": "CA", "559": "CA", "562": "CA",
    "619": "CA", "626": "CA", "628": "CA", "650": "CA", "657": "CA",
    "661": "CA", "669": "CA", "707": "CA", "714": "CA", "747": "CA",
    "760": "CA", "805": "CA", "818": "CA", "820": "CA", "831": "CA",
    "840": "CA", "858": "CA", "909": "CA", "916": "CA", "925": "CA",
    "949": "CA", "951": "CA",
    "503": "OR", "541": "OR", "458": "OR", "971": "OR",
    "206": "WA", "253": "WA", "360": "WA", "425": "WA", "509": "WA",
    "564": "WA",
    "907": "AK", "808": "HI",
    "603": "NH", "802": "VT", "207": "ME", "401": "RI",
    "304": "WV", "681": "WV",
    "204": "MB", "226": "ON", "236": "BC", "249": "ON", "289": "ON",
    "343": "ON", "365": "ON", "403": "AB", "416": "ON", "418": "QC",
    "437": "ON", "438": "QC", "450": "QC", "506": "NB", "514": "QC",
    "519": "ON", "579": "QC", "581": "QC", "587": "AB", "604": "BC",
    "613": "ON", "639": "SK", "647": "ON", "705": "ON", "709": "NL",
    "778": "BC", "780": "AB", "807": "ON", "819": "QC", "825": "AB",
    "867": "NT", "873": "QC", "902": "NS", "905": "ON",
    "800": "toll-free", "833": "toll-free", "844": "toll-free",
    "855": "toll-free", "866": "toll-free", "877": "toll-free",
    "888": "toll-free",
}

_book = None
_book_path = None


def normalise(s):
    """Any format -> lookup key.

    NANP numbers become bare 10 digits (a leading 1 is dropped). Anything else
    with at least 7 digits is kept as its full digit string, so international
    numbers still match themselves. Returns '' for junk."""
    d = re.sub(r"\D", "", str(s or ""))
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    if len(d) == 10:
        return d
    return d if len(d) >= 7 else ""


def load_book(path):
    global _book, _book_path
    if _book is None or _book_path != path:
        try:
            with open(path, encoding="utf-8") as f:
                _book = json.load(f)
        except Exception:
            _book = {}               # not built yet: degrade to area codes
        _book_path = path
    return _book


def default_book_path():
    try:
        from . import config
        return config.expand(config.load().get("contacts", "book"))
    except Exception:
        return os.path.expanduser("~/.local/share/cosmic-tools/phone/contacts.json")


def lookup(number, path=None):
    """-> {'name', 'company', 'src'} for a known number, else None."""
    return load_book(path or default_book_path()).get(normalise(number))


def region(number):
    n = normalise(number)
    return AREA.get(n[:3], "") if len(n) == 10 else ""


def label(number, path=None):
    """Best one-line description, or '' if nothing at all is known."""
    hit = lookup(number, path)
    if hit:
        name = (hit.get("name") or "").strip()
        company = (hit.get("company") or "").strip()
        if name and company and company.lower() not in name.lower():
            return "%s - %s" % (name, company)
        return name or company
    return region(number)


# --------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------

def _unfold(text):
    """RFC 6350 line unfolding: a line starting with space/tab continues the
    previous one."""
    return re.sub(r"\r?\n[ \t]", "", text)


def _vunescape(v):
    return (v.replace("\\n", " ").replace("\\N", " ").replace("\\,", ",")
             .replace("\\;", ";").replace("\\\\", "\\")).strip()


def parse_vcf(text):
    """-> [(name, company, [numbers])] from vCard 2.1/3.0/4.0 text."""
    cards, cur = [], None
    for line in _unfold(text).splitlines():
        if not line.strip():
            continue
        if ":" not in line:
            continue
        head, value = line.split(":", 1)
        prop = head.split(";", 1)[0].split(".")[-1].upper()   # drop item1. groups
        if prop == "BEGIN" and value.strip().upper() == "VCARD":
            cur = {"fn": "", "n": "", "org": "", "tel": []}
        elif prop == "END" and value.strip().upper() == "VCARD":
            if cur is not None:
                name = cur["fn"] or cur["n"]
                cards.append((name, cur["org"], cur["tel"]))
            cur = None
        elif cur is None:
            continue
        elif prop == "FN":
            cur["fn"] = _vunescape(value)
        elif prop == "N" and not cur["n"]:
            parts = [_vunescape(p) for p in value.split(";")]
            family = parts[0] if parts else ""
            given = parts[1] if len(parts) > 1 else ""
            cur["n"] = " ".join(p for p in (given, family) if p)
        elif prop == "ORG":
            cur["org"] = _vunescape(value.split(";", 1)[0])
        elif prop == "TEL":
            v = value.strip()
            if v.lower().startswith("tel:"):
                v = v[4:]
            cur["tel"].append(v)
    return cards


def parse_csv(text):
    """-> [(name, company, [numbers])] from 'name,number[,company]' CSV.

    A header row is optional; it is detected (and its column names honoured)
    when the first row's second field has no digits."""
    rows = list(csv.reader(io.StringIO(text)))
    rows = [r for r in rows if r and any(c.strip() for c in r)]
    if not rows:
        return []
    ix = {"name": 0, "number": 1, "company": 2}
    first = rows[0]
    if len(first) > 1 and not re.search(r"\d", first[1]):
        hdr = [h.strip().lower() for h in first]
        for key, alts in (("name", ("name", "full name", "display name")),
                          ("number", ("number", "phone", "phone number", "tel", "mobile")),
                          ("company", ("company", "organization", "org"))):
            for a in alts:
                if a in hdr:
                    ix[key] = hdr.index(a)
                    break
        rows = rows[1:]
    out = []
    for r in rows:
        def col(k):
            i = ix[k]
            return r[i].strip() if i < len(r) else ""
        if col("number").lstrip().startswith("#") or col("name").startswith("#"):
            continue
        out.append((col("name"), col("company"), [col("number")]))
    return out


def build(sources):
    """sources: [(kind, text)] with kind 'vcf' or 'csv', lowest authority
    first - later sources overwrite earlier ones for the same number.
    -> {normalised number: {'name', 'company', 'src'}}"""
    book = {}
    for kind, text in sources:
        entries = parse_vcf(text) if kind == "vcf" else parse_csv(text)
        for name, company, numbers in entries:
            if not (name or company):
                continue
            for num in numbers:
                k = normalise(num)
                if k:
                    book[k] = {"name": name, "company": company, "src": kind}
    return book
