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
import datetime as dt
import csv
import html
import json
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

NO_CLUB = ("unknown", "without club", "retired", "career break",
           "ban", "no club")


def _undouble(s):
    """TM writes some titles twice over: 'Without ClubWithout Club'."""
    if s and len(s) % 2 == 0:
        half = len(s) // 2
        if s[:half] == s[half:]:
            return s[:half]
    return s


def _attr(tag, name):
    m = re.search(name + r'="([^"]*)"', tag)
    return _undouble(html.unescape(m.group(1))) if m else ""


def is_no_club(name):
    n = norm(_undouble(name or ""))
    return not n or any(n == b or n.startswith(b) for b in
                        (norm(x) for x in NO_CLUB))


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
            if t and not is_no_club(t):
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
    elif getattr(args, "no_fotmob_only", False):
        fm = SCRAPER_DIR / "fotmob_ids.csv"
        has_id = set()
        if fm.exists():
            with open(fm, newline="", encoding="utf-8") as f:
                has_id = {r["slug"] for r in csv.DictReader(f)
                          if r.get("fotmob_id")}
        todo = [p for p in todo if p["slug"] not in has_id]
        print(f"  (limited to {len(todo)} players with no match-data "
              f"source)")
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


# ---------------------------------------------------------------- sweep

IRELAND_LAND_ID = 72          # from the flag asset id on their pages
POOL_FILE = SCRAPER_DIR / "tm_ireland_pool.csv"
STATE_FILE = SCRAPER_DIR / "tm_sweep_state.json"
LIST_URL = (BASE + "/spieler-statistik/wertvollstespieler/marktwertetop"
            "?land_id={land}&page={page}")
POOL_COLUMNS = ["tm_id", "name", "club", "nat1", "nat2", "age",
                "position", "market_value", "tracked"]


def parse_items_rows(page):
    """Rows out of a <table class="items"> listing. Same nested-table
    trap as the search page, so split on the outer row class."""
    gm = re.search(r'<table class="items".*?</tbody>', page, re.S)
    if not gm:
        return []
    out, seen = [], set()
    for row in re.split(r'<tr class="(?:odd|even)"[^>]*>', gm.group(0))[1:]:
        pm = re.search(r'<a\b([^>]*/profil/spieler/(\d+)[^>]*)>(.*?)</a>',
                       row, re.S)
        if not pm:
            continue
        tid = pm.group(2)
        if tid in seen:
            continue
        seen.add(tid)
        name = _attr(pm.group(1), "title") or strip_tags(pm.group(3))
        club = ""
        for cm in re.finditer(r'<a\b([^>]*/startseite/verein/\d+[^>]*)>',
                              row):
            t = _attr(cm.group(1), "title")
            if t and not is_no_club(t):
                club = t
                break
        nats = []
        for nm in re.finditer(r'<img\b([^>]*class="flaggenrahmen"[^>]*)>',
                              row):
            t = _attr(nm.group(1), "title") or _attr(nm.group(1), "alt")
            if t and t not in nats:
                nats.append(t)
        agem = re.search(r'<td class="zentriert">(\d{1,2})</td>', row)
        posm = re.search(r'<td class="zentriert">([A-Za-z][A-Za-z\- ]{1,24})'
                         r'</td>', row)
        mvm = re.search(r'<td class="rechts hauptlink">(?:<a[^>]*>)?'
                        r'([^<]+)', row)
        out.append({
            "tm_id": tid, "name": name, "club": club,
            "nat1": nats[0] if nats else "",
            "nat2": nats[1] if len(nats) > 1 else "",
            "age": agem.group(1) if agem else "",
            "position": strip_tags(posm.group(1)) if posm else "",
            "market_value": strip_tags(mvm.group(1)) if mvm else "",
        })
    return out


def next_page_link(page_html, current_url):
    """Find the pager's 'next' link on the page itself, rather than
    assuming a ?page=N format."""
    cands = []
    for m in re.finditer(r'<a\b([^>]*)>(.*?)</a>', page_html, re.S):
        attrs, label = m.group(1), strip_tags(m.group(2)).lower()
        href = _attr(attrs, "href")
        if not href or "spieler" not in href and "suche" not in href \
                and "statistik" not in href:
            continue
        title = _attr(attrs, "title").lower()
        cls = _attr(attrs, "class").lower()
        nm = re.search(r"(?:page[=/]|seite[=/])(\d+)", href)
        if not nm:
            continue
        num = int(nm.group(1))
        score = 0
        if "next" in title or "next" in label or "n\u00e4chste" in title:
            score = 3
        elif "arrow" in cls or ">" == label.strip():
            score = 2
        cands.append((score, num, href))
    if not cands:
        return None, None
    cur = re.search(r"(?:page[=/]|seite[=/])(\d+)", current_url)
    curnum = int(cur.group(1)) if cur else 1
    nxt = [c for c in cands if c[1] == curnum + 1]
    pick = max(nxt, key=lambda c: c[0]) if nxt else \
        max((c for c in cands if c[1] > curnum), key=lambda c: -c[1],
            default=None)
    if not pick:
        return None, None
    href = pick[2]
    return (href if href.startswith("http") else BASE + href), pick[1]


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"page": 1, "done": False}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state))


def load_pool():
    pool = {}
    if POOL_FILE.exists():
        with open(POOL_FILE, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                pool[row["tm_id"]] = row
    return pool


def save_pool(pool):
    with open(POOL_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=POOL_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for tid in sorted(pool, key=lambda x: int(x)):
            w.writerow(pool[tid])


def sweep(args):
    """Walk every page of the Ireland nationality listing.

    Checkpointed: a 403 saves progress and exits, and the next run picks
    up on the same page. Expect to run this several times.
    """
    pool = load_pool()
    state = load_state()
    if args.restart:
        state = {"page": 1, "done": False}
    if state.get("done") and not args.restart:
        print(f"sweep already complete ({len(pool)} players in "
              f"{POOL_FILE.name}). --restart to redo it.")
        return

    tracked = {p["slug"] for p in read_player_list()}
    known_tm = {v.get("tm_id") for v in read_cache().values() if v.get("tm_id")}

    page = state["page"]
    url = state.get("url") or LIST_URL.format(land=args.land_id, page=page)
    added_this_run = 0
    empty_pages = 0
    prev_ids = None
    page_size = 0
    while page <= args.max_page:
        try:
            html_page = get(url,
                            debug_name=f"tmlist_{page}" if args.debug else None)
        except Exception as e:
            save_state({"page": page, "url": url, "done": False})
            save_pool(pool)
            print(f"\npage {page}: {e}")
            print(f"progress saved - {len(pool)} players so far. "
                  f"Rerun later and it continues from page {page}.")
            return

        rows = parse_items_rows(html_page)
        if not rows:
            empty_pages += 1
            print(f"  page {page}: no rows")
            if empty_pages >= 2:
                state = {"page": page, "done": True}
                break
        else:
            empty_pages = 0
            new = 0
            for r in rows:
                if r["tm_id"] in pool:
                    continue
                r["tracked"] = ("yes" if (r["tm_id"] in known_tm
                                          or slugify_name(r["name"])
                                          in tracked) else "")
                pool[r["tm_id"]] = r
                new += 1
            added_this_run += new
            print(f"  page {page}: {len(rows)} rows, {new} new "
                  f"(pool {len(pool)})")
            ids = {r["tm_id"] for r in rows}
            page_size = max(page_size, len(rows))
            if prev_ids is not None and ids == prev_ids:
                # the site re-serves the last page past the end of the list
                print("\nsame rows as the previous page - end of list.")
                state = {"page": page, "url": url, "done": True}
                break
            prev_ids = ids
            if page_size and len(rows) < page_size:
                print(f"  (short page - {len(rows)} of {page_size}, "
                      f"this is the last one)")
                state = {"page": page + 1, "url": url, "done": True}
                break

        nxt, nxtnum = next_page_link(html_page, url)
        if nxt:
            url, page = nxt, nxtnum
        else:
            page += 1
            url = LIST_URL.format(land=args.land_id, page=page)
        state = {"page": page, "url": url, "done": False}
        save_state(state)
        if page % 10 == 0:
            save_pool(pool)
        time.sleep(args.sleep)

    save_state(state)
    save_pool(pool)
    untracked = [r for r in pool.values() if not r.get("tracked")]
    print(f"\n{len(pool)} players in the pool "
          f"({added_this_run} new this run)")
    print(f"{len(untracked)} of them are not in players_list.csv yet")
    print(f"pool written to {POOL_FILE}")
    if state.get("done"):
        print("sweep complete.")
    else:
        print(f"stopped at page {page} - rerun to continue.")


def slugify_name(name):
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("'", "").replace("\u2019", "")
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s.lower())
    return s.strip("-")


def pool_add(args):
    """Add pool players into players_list.csv + tm_ids.csv."""
    pool = load_pool()
    if not pool:
        sys.exit(f"{POOL_FILE.name} not found - run `sweep` first.")
    players = read_player_list()
    cache = read_cache()
    known_slugs = {p["slug"] for p in players}
    known_tm = {v.get("tm_id") for v in cache.values() if v.get("tm_id")}

    def eligible(r):
        nats = [norm(r.get("nat1", "")), norm(r.get("nat2", ""))]
        if "ireland" not in nats:
            return False
        if args.declared_only and norm(r.get("nat1", "")) != "ireland":
            return False
        if args.max_age and r.get("age"):
            try:
                if int(r["age"]) > args.max_age:
                    return False
            except ValueError:
                pass
        if args.clubs_only and is_no_club(r.get("club", "")):
            return False
        return True

    added = []
    for tid, r in sorted(pool.items(), key=lambda x: int(x[0])):
        if tid in known_tm or not eligible(r):
            continue
        slug = slugify_name(r["name"])
        if slug in known_slugs:
            continue
        players.append({"slug": slug, "name": r["name"],
                        "club": r.get("club", ""), "league": "",
                        "tier": "", "pos": r.get("position", ""),
                        "ireland_level": ""})
        cache[slug] = {"tm_id": tid, "tm_name": r["name"],
                       "tm_club": r.get("club", ""), "note": "from sweep"}
        known_slugs.add(slug)
        added.append(f"{slug} ({r.get('club') or 'no club'}, "
                     f"{r.get('nat1','')}/{r.get('nat2','')})")

    if added:
        cols = ["slug", "name", "club", "league", "tier", "pos",
                "ireland_level"]
        players.sort(key=lambda p: p["slug"])
        with open(PLAYER_LIST, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(players)
        write_cache(cache)
    print(f"added {len(added)} players")
    for a in added[:60]:
        print(f"  + {a}")
    if len(added) > 60:
        print(f"  ... and {len(added) - 60} more")
    if added:
        print("\nthey have transfermarkt ids but no match data yet - run "
              "the other scraper's `resolve` then `scrape` for that.")


def pool_undo(args):
    """Remove sweep-added players who have no real club."""
    players = read_player_list()
    cache = read_cache()
    drop = set()
    for p in players:
        note = cache.get(p["slug"], {}).get("note", "")
        if note != "from sweep":
            continue
        if is_no_club(p.get("club", "")):
            drop.add(p["slug"])
    if not drop:
        print("nothing to remove")
        return
    kept = [p for p in players if p["slug"] not in drop]
    cols = ["slug", "name", "club", "league", "tier", "pos",
            "ireland_level"]
    with open(PLAYER_LIST, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(kept)
    for s in drop:
        cache.pop(s, None)
    write_cache(cache)
    print(f"removed {len(drop)} clubless players added by the sweep:")
    for s in sorted(drop):
        print(f"  - {s}")


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
    """One pass. Returns True when every targeted player is done."""
    cache = read_cache()
    if not cache:
        sys.exit("no tm_ids.csv - run `resolve` first.")
    players = read_player_list()
    if args.only:
        want = set(args.only.split(","))
        players = [p for p in players if p["slug"] in want]
    elif args.no_fotmob_only:
        fm = SCRAPER_DIR / "fotmob_ids.csv"
        if not fm.exists():
            sys.exit("fotmob_ids.csv not found")
        with open(fm, newline="", encoding="utf-8") as f:
            has_id = {r["slug"] for r in csv.DictReader(f)
                      if r.get("fotmob_id")}
        players = [p for p in players if p["slug"] not in has_id]
        print(f"{len(players)} players with no match-data source - "
              f"filling their bio from here instead")

    out_path = Path(args.out) / "data/api/tm.csv"
    existing = {}
    if out_path.exists() and not args.rebuild:
        with open(out_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                existing[row["slug"]] = row

    if args.skip:
        players = players[args.skip:]
    if args.limit:
        players = players[:args.limit]
    targeted = list(players)
    if not args.rebuild:
        players = [p for p in players if p["slug"] not in existing]
        if len(players) != len(targeted):
            print(f"{len(targeted) - len(players)} already done, "
                  f"{len(players)} to go")

    done, failed, skipped, blocked = 0, [], 0, False
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
            if "403" in str(e) or "429" in str(e):
                blocked = True
                print("  blocked - stopping; finished players are kept")
                break
            time.sleep(args.sleep)
            continue
        row = {"slug": slug, "tm_id": tid}
        row.update(data)
        existing[slug] = row
        done += 1
        print(f"  [{i+1}/{len(players)}] {slug}: {data['tm_club'] or '?'} "
              f"/ {data['contract_expires'] or 'no contract date'}")
        time.sleep(args.sleep)

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
    remaining = [p for p in targeted
                 if cache.get(p["slug"], {}).get("tm_id")
                 and p["slug"] not in existing]
    print(f"{len(remaining)} still to do")
    return (not blocked) and not remaining


def profiles_loop(args):
    """Keep going until everything's done, backing off when blocked.

    Transfermarkt limits per IP, so running several of these at once on
    one connection gets you blocked faster, not slower. Use --skip/--limit
    to split the list across genuinely different networks instead.
    """
    wait = args.cooldown
    round_no = 1
    while True:
        print(f"\n=== pass {round_no} "
              f"(sleep {args.sleep}s between players) ===")
        try:
            finished = profiles(args)
        except KeyboardInterrupt:
            print("\nstopped by hand - progress is saved")
            return
        if finished:
            print("\nall done.")
            return
        if round_no >= args.max_passes:
            print(f"\nstopping after {round_no} passes - rerun when "
                  f"you like, it continues where it left off")
            return
        mins = wait / 60
        print(f"\ncooling off for {mins:.0f} min, then continuing "
              f"(Ctrl+C to stop - nothing is lost)")
        try:
            time.sleep(wait)
        except KeyboardInterrupt:
            print("\nstopped by hand - progress is saved")
            return
        wait = min(wait * 1.5, args.max_cooldown)
        round_no += 1


def merge_shards(args):
    """Merge shard CSVs (from parallel runs) into data/api/tm.csv."""
    merged = {}
    dest = Path(args.out) / "data/api/tm.csv"
    if dest.exists():
        with open(dest, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                merged[row["slug"]] = row
    found = 0
    for path in sorted(Path(args.shards).rglob("*.csv")):
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        for row in rows:
            if row.get("slug"):
                merged[row["slug"]] = row      # newest wins
        found += len(rows)
        print(f"  {path.name}: {len(rows)} rows")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        for slug in sorted(merged):
            w.writerow([merged[slug].get(c, "") for c in COLUMNS])
    print(f"merged {found} shard rows -> {dest} ({len(merged)} players)")


def count_players(args):
    """Print the shard matrix for the workflow to consume."""
    n = len(read_player_list())
    size = args.size
    shards = [{"skip": i, "limit": size} for i in range(0, n, size)]
    print(json.dumps(shards))


TRANSFER_COLUMNS = ["slug", "date", "season", "from_club", "to_club",
                    "fee", "market_value", "kind"]

TRANSFER_API = "https://www.transfermarkt.co.uk/ceapi/transferHistory/list/{tid}"


def transfer_kind(fee):
    """loan / loan end / free / fee / unknown, from the fee cell."""
    f = norm(fee or "").lower()
    if "end of loan" in f:
        return "loan end"
    if "loan" in f:
        return "loan"
    if "free" in f:
        return "free"
    if f in ("", "-", "?"):
        return ""
    return "fee"


def transfer_rows(tid, slug, debug_name=None):
    """One player's transfer history, newest first, as csv rows.

    This is a json endpoint rather than the profile page - the history is
    lazily loaded on the site, so it isn't in the profile html to be scraped.
    """
    r = SESSION.get(TRANSFER_API.format(tid=tid), timeout=25)
    r.raise_for_status()
    if debug_name:
        DEBUG_DIR.mkdir(exist_ok=True)
        (DEBUG_DIR / f"{debug_name}.json").write_text(r.text[:1_500_000])
    data = r.json()
    out = []
    for t in (data.get("transfers") or []):
        if t.get("upcoming") or t.get("futureTransfer"):
            continue                       # a move that hasn't happened yet
        frm = (t.get("from") or {}).get("clubName") or ""
        to = (t.get("to") or {}).get("clubName") or ""
        if not to:
            continue
        out.append([slug,
                    (t.get("dateUnformatted") or "").strip(),
                    (t.get("season") or "").strip(),
                    frm.strip(), to.strip(),
                    (t.get("fee") or "").strip(),
                    (t.get("marketValue") or "").strip(),
                    transfer_kind(t.get("fee"))])
    return out


TRANSFER_STATE = SCRAPER_DIR / "tm_transfers_state.json"


def _transfer_state():
    """slug -> the day we last read their history. Kept beside the scraper
       rather than in the csv so the file's shape never changes."""
    try:
        return json.loads(TRANSFER_STATE.read_text())
    except Exception:
        return {}


def transfers(args):
    """data/api/transfers.csv - every club move we can see, per player."""
    cache = read_cache()
    if not cache:
        sys.exit("no tm_ids.csv - run `resolve` first.")
    players = read_player_list()
    if args.only:
        want = set(args.only.split(","))
        players = [p for p in players if p["slug"] in want]

    out_path = Path(args.out) / "data/api/transfers.csv"
    have = {}
    if out_path.exists() and not args.rebuild:
        with open(out_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                have.setdefault(row["slug"], []).append(
                    [row.get(c, "") for c in TRANSFER_COLUMNS])

    # A player already in the file used to be settled forever, so a move
    # made after their first read could never appear. --age re-reads anyone
    # whose history has not been checked in that many days, oldest first,
    # which is what keeps a transfer window honest.
    state = _transfer_state()
    stale = set()
    if args.age:
        cut = (dt.date.today() - dt.timedelta(days=args.age)).isoformat()
        for p in players:
            if state.get(p["slug"], "") < cut:
                stale.add(p["slug"])

    def rank(p):
        return (state.get(p["slug"], ""), p["slug"])

    todo = [p for p in players
            if cache.get(p["slug"], {}).get("tm_id")
            and (args.rebuild or p["slug"] not in have
                 or p["slug"] in stale)]
    todo.sort(key=rank)                      # least recently checked first
    if args.limit:
        todo = todo[:args.limit]
    fresh = sum(1 for p in todo if p["slug"] not in have)
    print(f"{len(todo)} players to read ({fresh} new, "
          f"{len(todo) - fresh} being refreshed; "
          f"{len(have)} already have a history)")

    blocked = 0
    for i, p in enumerate(todo):
        slug = p["slug"]
        tid = cache[slug]["tm_id"]
        try:
            rows = transfer_rows(tid, slug,
                                 f"tm_transfers_{tid}" if args.debug else None)
        except Exception as e:
            blocked += 1
            print(f"  [{i+1}/{len(todo)}] {slug}: failed ({e})")
            if blocked >= 5:
                print("  five failures in a row - stopping so we don't get "
                      "blocked harder. Run it again in a while; it picks up "
                      "where it left off.")
                break
            time.sleep(args.sleep * 4)
            continue
        blocked = 0
        have[slug] = rows
        state[slug] = dt.date.today().isoformat()
        print(f"  [{i+1}/{len(todo)}] {slug}: {len(rows)} moves"
              + (f", now at {rows[0][4]}" if rows else ""))
        time.sleep(args.sleep)

    flat = []
    for slug in sorted(have):
        flat.extend(have[slug])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(TRANSFER_COLUMNS)
        w.writerows(flat)
    TRANSFER_STATE.write_text(json.dumps(state, indent=0, sort_keys=True))
    print(f"wrote {out_path} ({len(flat)} rows, {len(have)} players)")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("resolve", help="map slugs to transfermarkt ids")
    r.add_argument("--force", action="store_true")
    r.add_argument("--only", default="", help="comma-separated slugs")
    r.add_argument("--no-fotmob-only", action="store_true",
                   help="only players missing from the match-data source")
    r.add_argument("--debug", action="store_true",
                   help="dump search HTML to scraper/debug/")

    a = sub.add_parser("add", help="add ids by hand: add <slug> <id> ...")
    a.add_argument("pairs", nargs="+")

    sw = sub.add_parser("sweep",
                        help="walk every Ireland-nationality listing page")
    sw.add_argument("--land-id", type=int, default=IRELAND_LAND_ID)
    sw.add_argument("--max-page", type=int, default=400)
    sw.add_argument("--sleep", type=float, default=SLEEP)
    sw.add_argument("--restart", action="store_true")
    sw.add_argument("--debug", action="store_true")

    pa = sub.add_parser("pool-add",
                        help="add swept players into players_list.csv")
    pa.add_argument("--declared-only", action="store_true",
                    help="only players whose FIRST nationality is Ireland")
    pa.add_argument("--max-age", type=int, default=0,
                    help="skip players older than this")
    pa.add_argument("--clubs-only", action="store_true",
                    help="skip players with no current club")

    mg = sub.add_parser("merge-shards",
                        help="combine parallel-run CSVs into tm.csv")
    mg.add_argument("shards", help="folder holding the shard CSVs")
    mg.add_argument("--out", default=".")

    ct = sub.add_parser("shard-matrix",
                        help="print the shard list as JSON")
    ct.add_argument("--size", type=int, default=20)

    pu = sub.add_parser("pool-undo",
                        help="remove sweep-added players with no club")

    tf = sub.add_parser("transfers", help="write data/api/transfers.csv")
    tf.add_argument("--out", default=".")
    tf.add_argument("--only", default="", help="comma-separated slugs")
    tf.add_argument("--sleep", type=float, default=SLEEP,
                    help=f"seconds between players (default {SLEEP})")
    tf.add_argument("--limit", type=int, default=0,
                    help="only do N players (to split the work)")
    tf.add_argument("--age", type=int, default=0,
                    help="also re-read anyone whose history has not been "
                         "checked in this many days (0 = only new players)")
    tf.add_argument("--rebuild", action="store_true",
                    help="ignore the existing file instead of updating it")
    tf.add_argument("--debug", action="store_true",
                    help="dump the raw json to scraper/debug/")

    p = sub.add_parser("profiles", help="write data/api/tm.csv")
    p.add_argument("--out", default=".")
    p.add_argument("--only", default="", help="comma-separated slugs")
    p.add_argument("--no-fotmob-only", action="store_true",
                   help="only players missing from the match-data source")
    p.add_argument("--sleep", type=float, default=SLEEP,
                   help=f"seconds between players (default {SLEEP})")
    p.add_argument("--loop", action="store_true",
                   help="keep retrying with a cooldown until finished")
    p.add_argument("--cooldown", type=float, default=900,
                   help="seconds to wait after a block (default 900)")
    p.add_argument("--max-cooldown", type=float, default=3600)
    p.add_argument("--max-passes", type=int, default=40)
    p.add_argument("--skip", type=int, default=0,
                   help="skip the first N players (to split the work)")
    p.add_argument("--limit", type=int, default=0,
                   help="only do N players (to split the work)")
    p.add_argument("--rebuild", action="store_true",
                   help="ignore the existing file instead of updating it")
    p.add_argument("--debug", action="store_true",
                   help="dump raw HTML to scraper/debug/")

    args = ap.parse_args()
    if args.cmd == "profiles" and getattr(args, "loop", False):
        return profiles_loop(args)
    {"resolve": resolve, "add": add_ids, "profiles": profiles,
     "transfers": transfers,
     "sweep": sweep, "pool-add": pool_add,
     "pool-undo": pool_undo, "merge-shards": merge_shards,
     "shard-matrix": count_players}[args.cmd](args)


if __name__ == "__main__":
    main()
