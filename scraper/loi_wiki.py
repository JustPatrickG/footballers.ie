#!/usr/bin/env python3
"""League of Ireland squad audit against Wikipedia.

Wikipedia's club-season pages (e.g. "2026 Bohemian F.C. season") are kept
current by club volunteers and list every squad member with a nationality -
better First Division coverage than any stats feed. This reads each LOI
club's squad, keeps the Irish players, and compares them to the roster.

    audit          report per club: Irish players Wikipedia has that the
                   roster doesn't, and roster players Wikipedia doesn't list
    add            append the missing Irish players to players_list.csv
                   (skips any slug that already exists - never overwrites)
    --club NAME    limit to one club (canonical roster spelling)
    --nir          include Northern Ireland (NIR) players too

Run from the project root on a home connection. After `add`, run:
    venv/bin/python3 scraper/irish_scraper.py resolve
    venv/bin/python3 scraper/irish_scraper.py scrape --active   (or full)
"""
import argparse
import csv
import json
import os
import re
import sys
import tempfile
import time
import unicodedata
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROSTER = os.path.join(HERE, "players_list.csv")
API = "https://en.wikipedia.org/w/api.php"
UA = "loi-squad-audit/1.1 (League of Ireland squad completeness check)"
YEAR = time.localtime().tm_year

PREM = "League of Ireland Premier Division"
FIRST = "League of Ireland First Division"

# canonical roster club name -> (division, Wikipedia article base)
CLUBS = {
    "Shamrock Rovers":          (PREM, "Shamrock Rovers F.C."),
    "Dundalk":                  (PREM, "Dundalk F.C."),
    "Shelbourne":               (PREM, "Shelbourne F.C."),
    "Derry City":               (PREM, "Derry City F.C."),
    "Sligo Rovers":             (PREM, "Sligo Rovers F.C."),
    "Drogheda United":          (PREM, "Drogheda United F.C."),
    "St. Patrick's Athletic":   (PREM, "St Patrick's Athletic F.C."),
    "Bohemian FC":              (PREM, "Bohemian F.C."),
    "Galway United FC":         (PREM, "Galway United F.C."),
    "Waterford FC":             (PREM, "Waterford F.C."),
    "Cork City":                (FIRST, "Cork City F.C."),
    "UCD":                      (FIRST, "University College Dublin A.F.C."),
    "Bray Wanderers":           (FIRST, "Bray Wanderers F.C."),
    "Longford Town":            (FIRST, "Longford Town F.C."),
    "Cobh Ramblers":            (FIRST, "Cobh Ramblers F.C."),
    "Kerry FC":                 (FIRST, "Kerry F.C."),
    "Athlone Town":             (FIRST, "Athlone Town A.F.C."),
    "Wexford FC":               (FIRST, "Wexford F.C."),
    "Treaty United":            (FIRST, "Treaty United F.C."),
    "Finn Harps":               (FIRST, "Finn Harps F.C."),
}

POS_MAP = {"GK": "GK", "DF": "DEF", "MF": "MID", "FW": "FWD"}


def slugify(name):
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = s.replace("'", "").replace("’", "")
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s


CACHE = os.path.join(tempfile.gettempdir(), "loi_wiki_cache")
_last_req = [0.0]


def get_wikitext(title):
    """Fetch a page's wikitext: 6h disk cache, >=1.2s between requests,
    and a backoff-retry on 429 - Wikipedia throttles bursts hard."""
    os.makedirs(CACHE, exist_ok=True)
    cpath = os.path.join(
        CACHE, re.sub(r"[^A-Za-z0-9._-]", "_", title) + ".json")
    data = None
    if (os.path.exists(cpath)
            and time.time() - os.path.getmtime(cpath) < 6 * 3600):
        with open(cpath, encoding="utf-8") as f:
            data = json.load(f)
    if data is None:
        q = urllib.parse.urlencode({
            "action": "parse", "page": title, "prop": "wikitext",
            "format": "json", "formatversion": "2", "redirects": "1"})
        req = urllib.request.Request(f"{API}?{q}",
                                     headers={"User-Agent": UA})
        for attempt in range(4):
            gap = _last_req[0] + 1.2 - time.time()
            if gap > 0:
                time.sleep(gap)
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = json.loads(r.read().decode())
                _last_req[0] = time.time()
                break
            except urllib.error.HTTPError as e:
                _last_req[0] = time.time()
                if e.code == 429 and attempt < 3:
                    ra = (e.headers.get("Retry-After") or "").strip()
                    wait = int(ra) if ra.isdigit() else 15 * (attempt + 1)
                    print(f"      429 - waiting {wait}s")
                    time.sleep(wait)
                    continue
                return None, f"HTTP {e.code}"
            except Exception as e:
                return None, str(e)
        if data is None:
            return None, "rate limited (gave up)"
    if "error" in data:
        return None, data["error"].get("code", "error")
    with open(cpath, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return data["parse"]["wikitext"], None


# {{fs player|no=1|nat=POL|pos=GK|name=[[Kacper Chorazka]]|...}} and the
# older {{football squad player ...}} spelling. Parameters arrive in any
# order; name may be a wikilink with or without a display form.
FS_RE = re.compile(
    r"\{\{\s*(?:fs player|football squad player)\s*\|(.*?)\}\}",
    re.IGNORECASE | re.DOTALL)


def parse_squad(wikitext):
    out = []
    for m in FS_RE.finditer(wikitext):
        params = {}
        for part in re.split(r"\|(?![^\[]*\]\])", m.group(1)):
            if "=" in part:
                k, _, v = part.partition("=")
                params[k.strip().lower()] = v.strip()
        nat = (params.get("nat") or "").upper()[:3]
        raw = params.get("name") or ""
        raw = re.sub(r"<ref[^>]*/>|<ref.*?</ref>", "", raw, flags=re.DOTALL)
        lm = re.match(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", raw)
        name = (lm.group(2) or lm.group(1)) if lm else raw
        name = re.sub(r"\s*\(.*?\)\s*", " ", name).strip()
        name = re.sub(r"\s+", " ", name)
        if not name:
            continue
        out.append({"name": name, "nat": nat,
                    "pos": POS_MAP.get((params.get("pos") or "").upper(), "")})
    return out


# Everything from a women's / academy / underage / reserve heading onward
# is out of scope - the site tracks the senior men's game. Club articles
# often carry those squads right below the first team.
CUT_RE = re.compile(
    r"^=+[^=\n]*(women|ladies|academy|under[- ]?\d|\bu-?\d{2}\b|reserve)"
    r"[^=\n]*=+\s*$", re.IGNORECASE | re.MULTILINE)


def trim_scope(wikitext):
    m = CUT_RE.search(wikitext)
    return wikitext[:m.start()] if m else wikitext


def squad_for(club, art):
    """This season's page first; then the club article (its Current squad
    is kept fresher than a finished season's page); then last season."""
    tried = []
    for title in (f"{YEAR} {art} season", art, f"{YEAR - 1} {art} season"):
        wt, err = get_wikitext(title)
        tried.append((title, err))
        if wt:
            sq = parse_squad(trim_scope(wt))
            if sq:
                return sq, title
    for t, e in tried:
        print(f"      tried {t!r}: {e or 'no squad templates found'}")
    return [], None


# "Danny Mandroiu" and "Daniel Mândroiu" are one player, not two. Compare
# surname (ASCII-folded, doubled letters collapsed, so O'Neill == O'Neil)
# plus a forgiving first-name check: shared prefix, containment
# (Mipo / Ademipo), or a known short form.
NICKS = {"paddy": "patrick", "pat": "patrick", "bobby": "robert",
         "rob": "robert", "robbie": "robert", "bob": "robert",
         "danny": "daniel", "dan": "daniel", "matt": "matthew",
         "sammy": "samuel", "sam": "samuel", "johnny": "jonathan",
         "jonny": "jonathan", "jon": "jonathan", "mick": "michael",
         "mikey": "michael", "mike": "michael", "tommy": "thomas",
         "tom": "thomas", "joey": "joseph", "joe": "joseph",
         "jimmy": "james", "jim": "james", "billy": "william",
         "will": "william", "willie": "william",
         "eddie": "edward", "ted": "edward", "teddy": "edward",
         "ned": "edward", "charlie": "charles", "alfie": "alfred",
         "freddie": "frederick", "fred": "frederick", "ollie": "oliver",
         "harry": "henry", "ricky": "richard",
         "richie": "richard", "andy": "andrew", "tony": "anthony",
         "nick": "nicholas", "chris": "christopher", "gerry": "gerard",
         "ger": "gerard", "dave": "david", "davy": "david",
         "steve": "stephen", "stevie": "stephen", "ben": "benjamin",
         "josh": "joshua", "greg": "gregory", "ronnie": "ronald",
         "kenny": "kenneth", "ken": "kenneth", "denny": "dennis",
         "larry": "laurence", "vinny": "vincent"}


def _letters(x):
    x = unicodedata.normalize("NFKD", x).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", x.lower())


def _dedouble(x):
    return re.sub(r"(.)\1+", r"\1", x)


def _same_first(a, b):
    a, b = _letters(a), _letters(b)
    if not a or not b:
        return False
    a, b = NICKS.get(a, a), NICKS.get(b, b)
    if a == b:
        return True
    if (len(a) >= 3 and b.startswith(a)) or (len(b) >= 3 and a.startswith(b)):
        return True
    return (len(a) >= 4 and a in b) or (len(b) >= 4 and b in a)


def same_person(n1, n2):
    p1, p2 = n1.split(), n2.split()
    if not p1 or not p2:
        return False
    if _dedouble(_letters(p1[-1])) != _dedouble(_letters(p2[-1])):
        return False
    return _same_first(p1[0], p2[0])


def load_roster():
    with open(ROSTER, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["audit", "add"], nargs="?", default="audit")
    ap.add_argument("--club")
    ap.add_argument("--nir", action="store_true",
                    help="treat NIR players as addable too")
    args = ap.parse_args()

    roster = load_roster()
    have = {r["slug"] for r in roster}
    by_club = {}
    for r in roster:
        by_club.setdefault(r.get("club", ""), []).append(r)

    clubs = {k: v for k, v in CLUBS.items()
             if not args.club or k.lower() == args.club.lower()}
    if not clubs:
        sys.exit(f"unknown club {args.club!r} - use the roster spelling")

    wanted_nats = {"IRL"} | ({"NIR"} if args.nir else set())
    to_add, nir_seen = [], []
    for club, (division, art) in clubs.items():
        print(f"== {club} ==")
        squad, page = squad_for(club, art)
        if not squad:
            print("   !! no squad found on Wikipedia - skipped")
            continue
        irish = [p for p in squad if p["nat"] in wanted_nats]
        nir_seen += [f'{p["name"]} ({club})' for p in squad
                     if p["nat"] == "NIR" and not args.nir]
        club_names = [r["name"] for r in by_club.get(club, [])]
        all_names = {r["name"]: r.get("club", "") for r in roster}
        print(f"   {page}: {len(squad)} in squad, {len(irish)} Irish, "
              f"{len(club_names)} on roster at this club")
        for p in irish:
            slug = slugify(p["name"])
            if slug in have or any(same_person(p["name"], n)
                                   for n in club_names):
                continue                       # already ours (any spelling)
            match = next((n for n in all_names
                          if same_person(p["name"], n)), None)
            if match:
                print(f"   ~ probably {match!r} "
                      f"({all_names[match] or 'no club'}) - skipped")
                continue
            print(f"   + MISSING  {p['name']:<28} {p['pos'] or '?'}")
            to_add.append({"slug": slug, "name": p["name"],
                           "club": club, "league": division, "tier": "loi",
                           "pos": p["pos"], "ireland_level": ""})
        for n in club_names:
            if not any(same_person(n, p["name"]) for p in squad):
                print(f"   - on roster, not in wiki squad (moved on?): {n}")
        time.sleep(1)          # be polite to the API

    if nir_seen:
        print(f"\nNIR players seen but NOT added (rerun with --nir to "
              f"include): {', '.join(nir_seen)}")
    print(f"\n{len(to_add)} Irish players missing from the roster.")
    if args.cmd == "add" and to_add:
        with open(ROSTER, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["slug", "name", "club",
                                              "league", "tier", "pos",
                                              "ireland_level"])
            for row in to_add:
                w.writerow(row)
        print(f"appended {len(to_add)} rows to scraper/players_list.csv")
        print("next: venv/bin/python3 scraper/irish_scraper.py resolve && "
              "venv/bin/python3 scraper/irish_scraper.py scrape")
    elif args.cmd == "audit" and to_add:
        print("dry run - rerun with 'add' to append them")


if __name__ == "__main__":
    main()
