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
import time
import unicodedata
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROSTER = os.path.join(HERE, "players_list.csv")
API = "https://en.wikipedia.org/w/api.php"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")
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


def get_wikitext(title):
    q = urllib.parse.urlencode({
        "action": "parse", "page": title, "prop": "wikitext",
        "format": "json", "formatversion": "2", "redirects": "1"})
    req = urllib.request.Request(f"{API}?{q}", headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode())
    except Exception as e:
        return None, str(e)
    if "error" in data:
        return None, data["error"].get("code", "error")
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


def squad_for(club, art):
    """Try this season's page first, then last season's, then the club page."""
    tried = []
    for title in (f"{YEAR} {art} season", f"{YEAR - 1} {art} season", art):
        wt, err = get_wikitext(title)
        tried.append((title, err))
        if wt:
            sq = parse_squad(wt)
            if sq:
                return sq, title
    for t, e in tried:
        print(f"      tried {t!r}: {e or 'no squad templates found'}")
    return [], None


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
        ours = {r["slug"] for r in by_club.get(club, [])}
        missing = [p for p in irish if slugify(p["name"]) not in have]
        elsewhere = [p for p in irish
                     if slugify(p["name"]) in have
                     and slugify(p["name"]) not in ours]
        gone = [r["name"] for r in by_club.get(club, [])
                if slugify(r["name"]) not in {slugify(p["name"])
                                              for p in squad}]
        print(f"   {page}: {len(squad)} in squad, {len(irish)} Irish, "
              f"{len(ours)} on roster at this club")
        for p in missing:
            print(f"   + MISSING  {p['name']:<28} {p['pos'] or '?'}")
            to_add.append({"slug": slugify(p["name"]), "name": p["name"],
                           "club": club, "league": division, "tier": "loi",
                           "pos": p["pos"], "ireland_level": ""})
        for p in elsewhere:
            print(f"   ~ name clash (slug exists at another club, skipped): "
                  f"{p['name']}")
        for n in gone:
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
