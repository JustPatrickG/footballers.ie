#!/usr/bin/env python3
"""
Transfermarkt profile scraper for footballers.ie.

Bio/contract data only - matches, results and stats stay with the other
scraper. Keyed on the same slugs as players_list.csv.

  python3 scraper/tm_scraper.py resolve      # slug -> transfermarkt id
  python3 scraper/tm_scraper.py profiles     # write data/api/tm.csv
  python3 scraper/tm_scraper.py add 12345    # add ids by hand

Transfermarkt rate-limits hard. SLEEP is deliberately slow; don't lower
it. A full pass over ~490 players takes roughly 40 minutes.
"""

import argparse
import csv
import html
import re
import sys
import time
import unicodedata
from pathlib import Path

import requests

SCRAPER_DIR = Path(__file__).resolve().parent
PLAYER_LIST = SCRAPER_DIR / "players_list.csv"
ID_CACHE = SCRAPER_DIR / "tm_ids.csv"
DEBUG_DIR = SCRAPER_DIR / "debug"

BASE = "https://www.transfermarkt.co.uk"
SEARCH_URL = BASE + "/schnellsuche/ergebnis/schnellsuche?query={q}"
PROFILE_URL = BASE + "/x/profil/spieler/{tid}"

SLEEP = 2.5

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
})

COLUMNS = ["slug", "tm_id", "tm_name", "dob", "age", "birthplace",
           "height_cm", "citizenship", "position", "foot", "agent",
           "tm_club", "joined", "contract_expires", "contract_option",
           "last_extension", "market_value"]


# ---------------------------------------------------------------- helpers

def norm(s):
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9 ]", "", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def strip_tags(fragment):
    fragment = re.sub(r"<br\s*/?>", " | ", fragment, flags=re.I)
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    text = html.unescape(fragment)
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s*\|\s*", " | ", text)
    return re.sub(r"\s+", " ", text).strip(" |,")


def get(url, debug_name=None):
    r = SESSION.get(url, timeout=30)
    if r.status_code == 403:
        raise RuntimeError("403 blocked - slow down or try later")
    r.raise_for_status()
    if debug_name:
        DEBUG_DIR.mkdir(exist_ok=True)
        (DEBUG_DIR / f"{debug_name}.html").write_text(r.text[:1_500_000])
    return r.text


def read_player_list():
    with open(PLAYER_LIST, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def read_cache():
    cache = {}
    if ID_CACHE.exists():
        with open(ID_CACHE, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                cache[row["slug"]] = row
    return cache


def write_cache(cache):
    cols = ["slug", "tm_id", "tm_name", "tm_club", "note"]
    with open(ID_CACHE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for slug in sorted(cache):
            row = {c: cache[slug].get(c, "") for c in cols}
            row["slug"] = slug
            w.writerow(row)


# ---------------------------------------------------------------- resolve

def _attr(tag, name):
    m = re.search(name + r'="([^"]*)"', tag)
    return html.unescape(m.group(1)) if m else ""


def search(name, debug_name=None):
    """[(tm_id, name, club, nationality)] from the player results grid.

    Result rows are <tr class="odd|even"> and contain a NESTED
    <table class="inline-table"> - the name and the club live in
    different inner <tr>s, so rows must be split on the outer class
    attribute, not on <tr> generally.
    """
    page = get(SEARCH_URL.format(q=requests.utils.quote(name)),
               debug_name=debug_name)

    # to </tbody>, not </table> - the first </table> closes the nested
    # inline-table inside the very first row
    gm = re.search(r'id="player-grid".*?</tbody>', page, re.S)
    grid = gm.group(0) if gm else ""
    if not grid:
        return []

    out, seen = [], set()
    for row in re.split(r'<tr class="(?:odd|even)"[^>]*>', grid)[1:]:
        pm = re.search(r'class="hauptlink"[^>]*>\s*<a\b([^>]*)>(.*?)</a>',
                       row, re.S)
        if not pm or "/profil/spieler/" not in pm.group(1):
            pm = re.search(r'<a\b([^>]*/profil/spieler/\d+[^>]*)>(.*?)</a>',
                           row, re.S)
        if not pm:
            continue
        idm = re.search(r"/profil/spieler/(\d+)", pm.group(1))
        if not idm:
            continue
        tid = idm.group(1)
        if tid in seen:
            continue
        pname = _attr(pm.group(1), "title") or strip_tags(pm.group(2))

        club = ""
        for cm in re.finditer(r'<a\b([^>]*/startseite/verein/\d+[^>]*)>',
                              row):
            t = _attr(cm.group(1), "title")
            if t and norm(t) not in ("unknown", "unknownunknown",
                                     "without club", "retired"):
                club = t
                break

        nat = ""
        nm = re.search(r'<img\b([^>]*class="flaggenrahmen"[^>]*)>', row)
        if nm:
            nat = _attr(nm.group(1), "title") or _attr(nm.group(1), "alt")

        seen.add(tid)
        out.append((tid, pname, club, nat))
    return out


def resolve(args):
    players = read_player_list()
    cache = read_cache()
    todo = [p for p in players
            if args.force or not cache.get(p["slug"], {}).get("tm_id")]
    if args.only:
        want = set(args.only.split(","))
        todo = [p for p in players if p["slug"] in want]
    print(f"resolving {len(todo)} of {len(players)} players")

    unresolved, unsure = [], []
    for i, p in enumerate(todo):
        slug, name, club = p["slug"], p["name"], p.get("club", "")
        try:
            cands = search(name,
                           debug_name=f"tmsearch_{slug}" if args.debug else None)
        except Exception as e:
            print(f"  [{i+1}/{len(todo)}] {name}: search failed ({e})")
            unresolved.append(slug)
            time.sleep(SLEEP)
            continue

        nname, nclub = norm(name), norm(club)
        surname = nname.split()[-1] if nname else ""
        hits = [c for c in cands
                if norm(c[1]) == nname or nname in norm(c[1])
                or (surname and surname in norm(c[1]).split())]
        pick = None
        if len(hits) == 1:
            pick = hits[0]
        elif hits:
            for h in hits:                       # 1. same club
                if nclub and norm(h[2]) and (nclub in norm(h[2])
                                             or norm(h[2]) in nclub):
                    pick = h
                    break
            if not pick:                         # 2. Irish, exact name
                irish = [h for h in hits
                         if norm(h[1]) == nname
                         and norm(h[3]) in ("ireland",
                                            "republic of ireland")]
                if len(irish) == 1:
                    pick = irish[0]
            if not pick:                         # 3. only one exact name
                exact = [h for h in hits if norm(h[1]) == nname]
                pick = exact[0] if len(exact) == 1 else None
            if not pick:
                unsure.append(
                    f"{name} ({club or 'no club'}) -> " +
                    ", ".join(f"{h[1]}/{h[2] or 'no club'}"
                              f"/{h[3] or '?'}#{h[0]}" for h in hits[:4]))
        if pick:
            tid, cname, cteam = pick[0], pick[1], pick[2]
            cache[slug] = {"tm_id": tid, "tm_name": cname,
                           "tm_club": cteam, "note": ""}
            print(f"  [{i+1}/{len(todo)}] {name} -> {tid} "
                  f"({cteam or 'club unknown'})")
        else:
            cache[slug] = {"tm_id": "", "note": "not found"}
            unresolved.append(slug)
            got = ", ".join(f"{c[1]}/{c[2] or 'no club'}" for c in cands[:3])
            print(f"  [{i+1}/{len(todo)}] {name}: not found"
                  + (f" (search returned: {got})" if got
                     else " (search returned nothing)"))
        time.sleep(SLEEP)

    write_cache(cache)
    print(f"\nid cache saved to {ID_CACHE}")
    if unsure:
        print(f"\n{len(unsure)} ambiguous - use `add <slug> <id>`:")
        for u in unsure:
            print(f"  ? {u}")
    if unresolved:
        print(f"\n{len(unresolved)} unresolved")


def add_ids(args):
    if len(args.pairs) % 2:
        sys.exit("give pairs: add <slug> <id> [<slug> <id> ...]")
    cache = read_cache()
    for slug, raw in zip(args.pairs[::2], args.pairs[1::2]):
        m = re.search(r"(\d{3,})", raw)
        if not m:
            print(f"  no id in '{raw}'")
            continue
        cache[slug] = {"tm_id": m.group(1), "note": "added by hand"}
        print(f"  {slug} -> {m.group(1)}")
    write_cache(cache)


# ---------------------------------------------------------------- profile

def info_pairs(page):
    """{label: value} from the profile info table."""
    pairs = {}
    for m in re.finditer(
            r'class="info-table__content info-table__content--regular"[^>]*>'
            r'(.*?)</span>\s*<span[^>]*'
            r'class="info-table__content info-table__content--bold"[^>]*>'
            r'(.*?)</span>', page, re.S):
        label = strip_tags(m.group(1)).rstrip(":").strip()
        value = strip_tags(m.group(2))
        if label and value and label not in pairs:
            pairs[label] = value
    # older layout: <th>Label:</th><td>Value</td>
    if not pairs:
        for m in re.finditer(r"<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>",
                             page, re.S):
            label = strip_tags(m.group(1)).rstrip(":").strip()
            value = strip_tags(m.group(2))
            if label and value and label not in pairs:
                pairs[label] = value
    return pairs


def pick(pairs, *labels):
    for want in labels:
        for k, v in pairs.items():
            if norm(k) == norm(want):
                return v
    for want in labels:
        for k, v in pairs.items():
            if norm(want) in norm(k):
                return v
    return ""


def height_cm(raw):
    """'1,88 m' / '6 ft 2 in' / '188 cm' -> '188'."""
    if not raw:
        return ""
    raw = raw.replace(",", ".")
    m = re.search(r"(\d+\.\d+)\s*m\b", raw)
    if m:
        return str(int(round(float(m.group(1)) * 100)))
    m = re.search(r"(\d{3})\s*cm", raw)
    if m:
        return m.group(1)
    m = re.search(r"(\d+)\s*ft\s*(\d+)?", raw)
    if m:
        ft = int(m.group(1))
        inch = int(m.group(2) or 0)
        return str(int(round((ft * 12 + inch) * 2.54)))
    return ""


def split_dob(raw):
    """'18/10/2000 (25)' -> ('2000-10-18', '25')."""
    if not raw:
        return "", ""
    age = ""
    m = re.search(r"\((\d{1,2})\)", raw)
    if m:
        age = m.group(1)
    d = re.search(r"(\d{1,2})[/.](\d{1,2})[/.](\d{4})", raw)
    if d:
        return f"{d.group(3)}-{int(d.group(2)):02d}-{int(d.group(1)):02d}", age
    d = re.search(r"([A-Za-z]{3,9})\s+(\d{1,2}),\s*(\d{4})", raw)
    if d:
        months = {m_: i + 1 for i, m_ in enumerate(
            ["jan", "feb", "mar", "apr", "may", "jun",
             "jul", "aug", "sep", "oct", "nov", "dec"])}
        mo = months.get(d.group(1)[:3].lower())
        if mo:
            return (f"{d.group(3)}-{mo:02d}-{int(d.group(2)):02d}", age)
    return raw.split("(")[0].strip(), age


def market_value(page):
    m = re.search(r'class="[^"]*data-header__market-value-wrapper[^"]*"[^>]*>'
                  r'(.*?)</a>', page, re.S)
    if not m:
        m = re.search(r'"marketValue"\s*:\s*"([^"]+)"', page)
        return html.unescape(m.group(1)) if m else ""
    txt = strip_tags(m.group(1))
    txt = re.sub(r"Last update.*$", "", txt, flags=re.I).strip()
    return txt


def profile(tid, debug_name=None):
    page = get(PROFILE_URL.format(tid=tid), debug_name=debug_name)
    pairs = info_pairs(page)
    if not pairs:
        raise RuntimeError("no info table found (layout changed?)")
    dob, age = split_dob(pick(pairs, "Date of birth/Age", "Date of birth"))
    name = pick(pairs, "Name in home country", "Full name")
    if not name:
        m = re.search(r'<h1[^>]*class="data-header__headline-wrapper[^"]*"'
                      r'[^>]*>(.*?)</h1>', page, re.S)
        name = re.sub(r"^#\d+\s*", "", strip_tags(m.group(1))) if m else ""
    return {
        "tm_name": name,
        "dob": dob,
        "age": age,
        "birthplace": pick(pairs, "Place of birth"),
        "height_cm": height_cm(pick(pairs, "Height")),
        "citizenship": pick(pairs, "Citizenship"),
        "position": pick(pairs, "Position"),
        "foot": pick(pairs, "Foot"),
        "agent": pick(pairs, "Player agent"),
        "tm_club": pick(pairs, "Current club"),
        "joined": pick(pairs, "Joined"),
        "contract_expires": pick(pairs, "Contract expires"),
        "contract_option": pick(pairs, "Contract option"),
        "last_extension": pick(pairs, "Last contract extension"),
        "market_value": market_value(page),
    }


def profiles(args):
    cache = read_cache()
    if not cache:
        sys.exit("no tm_ids.csv - run `resolve` first.")
    players = read_player_list()
    if args.only:
        want = set(args.only.split(","))
        players = [p for p in players if p["slug"] in want]

    out_path = Path(args.out) / "data/api/tm.csv"
    existing = {}
    if out_path.exists() and not args.rebuild:
        with open(out_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                existing[row["slug"]] = row

    done, failed, skipped = 0, [], 0
    for i, p in enumerate(players):
        slug = p["slug"]
        tid = cache.get(slug, {}).get("tm_id")
        if not tid:
            skipped += 1
            continue
        try:
            data = profile(tid, debug_name=f"tm_{slug}" if args.debug else None)
        except Exception as e:
            print(f"  [{i+1}/{len(players)}] {slug}: FAILED ({e})")
            failed.append(slug)
            if "403" in str(e):
                print("  blocked - stopping; rerun later to continue "
                      "(finished players are kept)")
                break
            time.sleep(SLEEP)
            continue
        row = {"slug": slug, "tm_id": tid}
        row.update(data)
        existing[slug] = row
        done += 1
        print(f"  [{i+1}/{len(players)}] {slug}: {data['tm_club'] or '?'} "
              f"/ {data['contract_expires'] or 'no contract date'}")
        time.sleep(SLEEP)

    rows = [[existing[s].get(c, "") for c in COLUMNS]
            for s in sorted(existing)]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        w.writerows(rows)
    print(f"\nwrote {out_path} ({len(rows)} rows; {done} refreshed this run)")
    if skipped:
        print(f"{skipped} players have no transfermarkt id yet")
    if failed:
        print(f"{len(failed)} failed: {', '.join(failed[:20])}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("resolve", help="map slugs to transfermarkt ids")
    r.add_argument("--force", action="store_true")
    r.add_argument("--only", default="", help="comma-separated slugs")
    r.add_argument("--debug", action="store_true",
                   help="dump search HTML to scraper/debug/")

    a = sub.add_parser("add", help="add ids by hand: add <slug> <id> ...")
    a.add_argument("pairs", nargs="+")

    p = sub.add_parser("profiles", help="write data/api/tm.csv")
    p.add_argument("--out", default=".")
    p.add_argument("--only", default="", help="comma-separated slugs")
    p.add_argument("--rebuild", action="store_true",
                   help="ignore the existing file instead of updating it")
    p.add_argument("--debug", action="store_true",
                   help="dump raw HTML to scraper/debug/")

    args = ap.parse_args()
    {"resolve": resolve, "add": add_ids, "profiles": profiles}[args.cmd](args)


if __name__ == "__main__":
    main()
