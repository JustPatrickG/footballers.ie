#!/usr/bin/env python3
"""
footballers.ie FotMob scraper.

Usage (from project root):
  python3 scraper/irish_scraper.py resolve   # match players -> FotMob IDs (once, then cached)
  python3 scraper/irish_scraper.py scrape    # write the 4 CSVs
  python3 scraper/irish_scraper.py all       # resolve missing, then scrape

Outputs:
  data/api/matches.csv, data/api/players.csv
  data/manual/results.csv, data/manual/fixtures.csv
Never touches data/manual/players.csv.
"""

import argparse
import csv
import datetime as dt
import json
import re
import sys
import time
import unicodedata
from pathlib import Path

import requests

SCRAPER_DIR = Path(__file__).resolve().parent
PLAYER_LIST = SCRAPER_DIR / "players_list.csv"
ID_CACHE = SCRAPER_DIR / "fotmob_ids.csv"
DEBUG_DIR = SCRAPER_DIR / "debug"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA, "Accept-Language": "en"})

SEARCH_URL = "https://apigw.fotmob.com/searchapi/suggest?term={term}&lang=en"
PLAYER_URL = "https://www.fotmob.com/players/{pid}/{slug}"
IMAGE_URL = "https://images.fotmob.com/image_resources/playerimages/{pid}.png"

SLEEP = 0.6  # politeness delay between requests
MATCH_PAST_DAYS = 7
MATCH_FUTURE_DAYS = 14


# ---------------------------------------------------------------- helpers

def norm(s):
    """lowercase, strip accents + punctuation, for fuzzy comparisons."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9 ]", "", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def find_all(obj, key):
    """Recursively yield every value for `key` anywhere in nested JSON."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                yield v
            yield from find_all(v, key)
    elif isinstance(obj, list):
        for item in obj:
            yield from find_all(item, key)


def first(iterable, default=None):
    for x in iterable:
        return x
    return default


def get_json(url, debug_name=None):
    last = None
    for _attempt in range(5):
        try:
            r = SESSION.get(url, timeout=25)
        except requests.RequestException as e:
            last = e; time.sleep(2 * (_attempt + 1)); continue
        if r.status_code >= 500:
            last = RuntimeError(f"{r.status_code} server error at {url}")
            time.sleep(2 * (_attempt + 1)); continue
        r.raise_for_status()
        data = r.json()
        if debug_name:
            DEBUG_DIR.mkdir(exist_ok=True)
            (DEBUG_DIR / f"{debug_name}.json").write_text(
                json.dumps(data, indent=1)[:2_000_000])
        return data
    raise last if last else RuntimeError(f"failed to fetch {url}")


MATCH_API = "https://www.fotmob.com/api/data/matchDetails?matchId={mid}"
# The league OVERVIEW PAGE embeds the full standings in its page JSON -
# the old /api/leagues endpoint is gone, but get_next_data on the page
# still works (discover-loi rides the same route).

# League name (as our data spells it) -> source league id, for the leagues
# players actually sit in. Anything not here is discovered by name from the
# allLeagues listing and then VALIDATED: a candidate table is only accepted
# if it contains at least one club our players play at, so "Premiership"
# (Scotland) can never be confused with another country's "Premiership".
CURATED_LEAGUE_IDS = {
    "premier league": 47, "championship": 48, "league one": 108,
    "league two": 109, "national league": 117,
    "premier division": 126, "first division": 218,
    "league of ireland premier division": 126,
    "league of ireland first division": 218,
    "laliga": 87, "serie a": 55, "bundesliga": 54, "ligue 1": 53,
    "major league soccer": 130, "eredivisie": 57,
}
MATCH_BY_ID = "https://www.fotmob.com/match/{mid}"


def leg_id(url):
    """The match id fotmob puts in the url fragment, if there is one."""
    frag = (url or "").rsplit("#", 1)
    return frag[1] if len(frag) == 2 and frag[1].isdigit() else ""


def match_page(url, debug_name=None):
    """__NEXT_DATA__ for one specific match.

    Every match_index url carries the id in its fragment:
        /matches/kups-vs-shamrock-rovers/a3ngj#5988052
    The path on its own identifies the *tie*, not the leg. For an ordinary
    fixture that's the same thing, but on a two-legged European tie fotmob
    serves whichever leg is current - so a first leg comes back either with no
    events at all (the return leg hasn't kicked off) or carrying the return
    leg's goals. Both were happening.

    So: fetch the page, check the id we actually got, and re-ask by id if it's
    the wrong leg. /api/data/matchDetails answers per match rather than per tie
    (and is ~40KB against ~630KB for the page); /match/<id> is the same thing
    as a page, kept as a second string. Returns None rather than the wrong
    match - a timeline carrying the other leg's goals is worse than none."""
    want = leg_id(url)
    data = get_next_data("https://www.fotmob.com" + url.split("#")[0],
                         debug_name)
    if not want or page_match_id(data) in ("", want):
        return data
    got = page_match_id(data)

    try:
        api = get_json(MATCH_API.format(mid=want), debug_name)
        if isinstance(api, dict) and (api.get("general") or api.get("content")):
            return {"props": {"pageProps": api}}
    except Exception as e:
        print(f"    api by id failed for {want}: {e}")

    try:
        page = get_next_data(MATCH_BY_ID.format(mid=want), debug_name)
        if page_match_id(page) == want:
            return page
    except Exception as e:
        print(f"    /match/{want} failed: {e}")

    print(f"    wrong leg: page gave {got}, wanted {want} - skipping")
    return None


def page_match_id(data):
    """The match a __NEXT_DATA__ blob is actually about."""
    if not isinstance(data, dict):
        return ""
    gen = (data.get("props", {}).get("pageProps", {}) or {}).get("general") or {}
    return str(gen.get("matchId") or "")


def get_next_data(url, debug_name=None):
    """Fetch a fotmob page and return the embedded __NEXT_DATA__ JSON."""
    last = None
    for _attempt in range(5):
        try:
            r = SESSION.get(url, timeout=25)
        except requests.RequestException as e:
            last = e; time.sleep(2 * (_attempt + 1)); continue
        if r.status_code >= 500:
            last = RuntimeError(f"{r.status_code} server error at {url}")
            time.sleep(2 * (_attempt + 1)); continue
        r.raise_for_status()
        m = re.search(
            r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
        if not m:
            raise RuntimeError(f"no __NEXT_DATA__ found at {url}")
        data = json.loads(m.group(1))
        if debug_name:
            DEBUG_DIR.mkdir(exist_ok=True)
            (DEBUG_DIR / f"{debug_name}.json").write_text(
                json.dumps(data, indent=1)[:5_000_000])
        return data
    raise last if last else RuntimeError(f"failed to fetch {url}")


def read_player_list():
    with open(PLAYER_LIST, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def read_id_cache():
    cache = {}
    if ID_CACHE.exists():
        with open(ID_CACHE, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                cache[row["slug"]] = row
    return cache


def write_id_cache(cache):
    cols = ["slug", "fotmob_id", "fotmob_name", "fotmob_team", "note"]
    with open(ID_CACHE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for slug in sorted(cache):
            row = {c: cache[slug].get(c, "") for c in cols}
            row["slug"] = slug
            w.writerow(row)


def write_csv(path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        w.writerow(header)
        w.writerows(rows)
    print(f"  wrote {path}  ({len(rows)} rows)")


def slugify(name):
    """His slug rules: lowercase ASCII hyphens, apostrophes stripped
    (O'Shea -> oshea), accents stripped."""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("'", "").replace("\u2019", "")
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s.lower())
    return s.strip("-")


# ---------------------------------------------------------------- resolve

def search_candidates(term, debug_name=None):
    """Return [(id, name, team)] player suggestions from fotmob search."""
    data = get_json(SEARCH_URL.format(term=requests.utils.quote(term)),
                    debug_name=debug_name)
    out, seen = [], set()

    def walk(obj):
        if isinstance(obj, dict):
            # player suggestion shapes seen over time:
            #  {"type":"player","id":...,"name":...,"teamName":...}
            #  {"options":[{"text":"Name|id", "payload":...}]}  (aws suggester)
            t = str(obj.get("type", "")).lower()
            oid = obj.get("id") or obj.get("playerId")
            name = obj.get("name") or obj.get("text", "")
            if obj.get("isCoach"):
                return                       # Stephen Kenny the manager is not
                                             # Stephen Kenny the forward
            if oid and name and ("player" in t or "teamName" in obj
                                 or "teamId" in obj):
                team = obj.get("teamName") or obj.get("team", "")
                key = str(oid)
                if key not in seen:
                    seen.add(key)
                    out.append((str(oid), str(name).split("|")[0], str(team)))
            if "text" in obj and "|" in str(obj.get("text", "")):
                text = obj["text"]
                nm, _, oid2 = text.rpartition("|")
                payload = obj.get("payload") or {}
                team = ""
                if isinstance(payload, dict):
                    if payload.get("isCoach"):
                        nm = None            # a manager, not a player
                    team = payload.get("teamName", "") or ""
                    if str(payload.get("type", "")).lower() not in (
                            "", "player"):
                        nm = None
                if nm and oid2.isdigit() and oid2 not in seen:
                    seen.add(oid2)
                    out.append((oid2, nm, team))
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(data)
    return out


NICKNAMES = {
    "robbie": ["robert"], "mikey": ["michael"], "mike": ["michael"],
    "tayo": ["omotayo"], "tommy": ["thomas"], "tom": ["thomas"],
    "danny": ["daniel"], "dan": ["daniel"], "jimmy": ["james"],
    "jim": ["james"], "jamie": ["james"], "willie": ["william"],
    "will": ["william"], "billy": ["william"], "bill": ["william"],
    "harry": ["harold", "henry"], "paddy": ["patrick"],
    "pat": ["patrick"], "podge": ["patrick"], "joe": ["joseph"],
    "joey": ["joseph"], "charlie": ["charles"], "chris": ["christopher"],
    "matt": ["matthew"], "matty": ["matthew"], "nick": ["nicholas"],
    "tony": ["anthony"], "andy": ["andrew"], "steve": ["stephen"],
    "stevie": ["stephen"], "davy": ["david"], "dave": ["david"],
    "eddie": ["edward"], "ed": ["edward"], "ollie": ["oliver"],
    "sam": ["samuel"], "ben": ["benjamin"], "alex": ["alexander"],
    "greg": ["gregory"], "mick": ["michael"], "micky": ["michael"],
    "johnny": ["john", "jonathan"], "jonny": ["jonathan", "john"],
    "ray": ["raymond"], "ronnie": ["ronald"], "gerry": ["gerard"],
    "ger": ["gerard"], "seamie": ["seamus"], "cathal": ["charles"],
    "zak": ["zachary"], "zach": ["zachary"], "kev": ["kevin"],
    "rob": ["robert"], "bobby": ["robert"], "nat": ["nathan"],
    "freddie": ["frederick"], "archie": ["archibald"],
}


def tm_full_names():
    """slug -> the full name Transfermarkt has. FotMob often files a player
    under it when our roster uses the short version: 'Vinnie Leonard' finds
    nothing, 'Vincent Leonard' finds him."""
    out = {}
    for rel in ("data/api/tm.csv",):
        path = Path(rel)
        if not path.exists():
            continue
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                nm = (r.get("tm_name") or "").strip()
                if r.get("slug") and nm:
                    out[r["slug"]] = re.sub(r'"[^"]*"', " ", nm).strip()
    return out


def name_variants(name, full_name=""):
    """'Robbie Brady' -> ['Robbie Brady', 'Robert Brady', 'Brady']."""
    out = [name]
    if full_name and norm(full_name) != norm(name):
        out.append(full_name)
        fp = full_name.split()
        if len(fp) > 2:                     # 'Ramon David Martos Pugh'
            out.append(" ".join([fp[0], fp[-1]]))
    parts = name.split()
    if len(parts) >= 2:
        first = norm(parts[0])
        for alt in NICKNAMES.get(first, []):
            out.append(" ".join([alt.capitalize()] + parts[1:]))
        out.append(" ".join(parts[1:]))     # surname only, last resort
    seen, uniq = set(), []
    for v in out:
        k = norm(v)
        if k and k not in seen:
            seen.add(k); uniq.append(v)
    return uniq


def same_person(feed_name, our_name):
    """Is the search hit the same footballer as ours? Short names and spelling
    vary a lot (Mikey/Michael, Tayo/Omotayo, Umeh/Umeh-Chibueze), so this is
    generous - but it will not accept a different first name on a shared
    surname. That is how Desmond Armstrong ended up holding Harrison
    Armstrong's id, and with it his photo, his club and his season."""
    def words(n):
        return [w for w in norm(str(n or "").replace("-", " ")).split() if w]
    A, B = words(feed_name), words(our_name)
    if not A or not B:
        return False
    if A == B:
        return True
    # surnames: every word after the first, so a double-barrel still matches
    sa, sb = set(A[1:]) or {A[-1]}, set(B[1:]) or {B[-1]}
    if not (sa & sb or any(x in y or y in x for x in sa for y in sb)):
        return False
    fa, fb = A[0], B[0]
    if fa == fb or fa.startswith(fb) or fb.startswith(fa):
        return True
    for short, longs in NICKNAMES.items():
        if (fa == short and fb in longs) or (fb == short and fa in longs):
            return True
    if fa in B or fb in A:
        return True
    return False


def resolve(args):
    players = read_player_list()
    cache = read_id_cache()
    todo = [p for p in players
            if args.force or not cache.get(p["slug"], {}).get("fotmob_id")]
    print(f"resolving {len(todo)} of {len(players)} players "
          f"({len(players) - len(todo)} already cached)")

    unresolved = []
    full_names = tm_full_names()
    for i, p in enumerate(todo):
        slug, name, club = p["slug"], p["name"], p["club"]
        try:
            cands, used_name = [], name
            for variant in name_variants(name, full_names.get(slug, "")):
                cands = search_candidates(
                    variant,
                    debug_name=f"search_{slug}" if args.debug else None)
                if cands:
                    used_name = variant
                    if variant != name:
                        print(f"      (searched as '{variant}')")
                    break
                time.sleep(SLEEP)
        except Exception as e:
            print(f"  [{i+1}/{len(todo)}] {name}: search failed ({e})")
            unresolved.append(slug)
            cache[slug] = {"fotmob_id": "", "note": f"search error: {e}"}
            time.sleep(SLEEP)
            continue

        nclub = norm(club)
        nname = norm(used_name)
        roster_name = norm(name)
        nsurname = nname.split()[-1] if nname else ""
        name_hits = []
        for oid, cname, cteam in cands:
            nc = norm(cname)
            if nc in (nname, roster_name) or nname in nc or nc in nname \
                    or (nsurname and nsurname in nc.split()):
                name_hits.append((oid, cname, cteam))
        # a shared surname is a lead, not a match
        sure_hits = [h for h in name_hits if same_person(h[1], name)]

        surname_only = (used_name != name
                        and len(used_name.split()) < len(name.split()))
        best = None
        pool = sure_hits or name_hits or cands
        if len(pool) == 1 and not surname_only:
            # only one option -> obviously them
            oid, cname, cteam = pool[0]
            best = (2, oid, cname, cteam)
        elif surname_only:
            # a surname alone is not enough - the club must agree, or we
            # end up copying one McGrath's season onto another
            for oid, cname, cteam in pool:
                if nclub and norm(cteam) and (nclub in norm(cteam)
                                              or norm(cteam) in nclub):
                    best = (2, oid, cname, cteam)
                    break
        else:
            for oid, cname, cteam in name_hits:
                club_match = (nclub and norm(cteam) and
                              (nclub in norm(cteam) or norm(cteam) in nclub))
                score = 2 if club_match else 1
                if best is None or score > best[0]:
                    best = (score, oid, cname, cteam)

        if best and not same_person(best[2], name):
            # the only candidate is somebody else. Record who, so it is easy to
            # see what happened, but leave the id blank: no stats beats another
            # player's stats.
            _, oid, cname, cteam = best
            cache[slug] = {"fotmob_id": "", "fotmob_name": cname,
                           "fotmob_team": cteam,
                           "note": f"name mismatch ({cname}) - not used"}
            unresolved.append(slug)
            print(f"  [{i+1}/{len(todo)}] {name}: closest is {cname} "
                  f"({cteam or 'team unknown'}) - different player, skipped")
        elif best:
            score, oid, cname, cteam = best
            note = "" if score == 2 else "club mismatch - CHECK"
            cache[slug] = {"fotmob_id": oid, "fotmob_name": cname,
                           "fotmob_team": cteam, "note": note}
            flag = "" if score == 2 else "  <-- check"
            print(f"  [{i+1}/{len(todo)}] {name} -> {oid} "
                  f"({cteam or 'team unknown'}){flag}")
        else:
            cache[slug] = {"fotmob_id": "", "note": "not found"}
            unresolved.append(slug)
            print(f"  [{i+1}/{len(todo)}] {name}: NOT FOUND")
        time.sleep(SLEEP)

    write_id_cache(cache)
    print(f"\nID cache saved to {ID_CACHE}")
    if unresolved:
        print(f"{len(unresolved)} unresolved (left blank, will be skipped "
              f"by scrape):")
        for s in unresolved:
            print(f"  - {s}")
        print("You can fill fotmob_id in the cache by hand: it's the number "
              "in the player's fotmob.com URL.")


# ---------------------------------------------------------------- discover

TEAM_URL = "https://www.fotmob.com/teams/{tid}/squad/{tslug}"
LEAGUE_URL = "https://www.fotmob.com/leagues/{lid}/overview"


def find_league_id(division):
    """division: 'premier division' or 'first division' (Ireland)."""
    data = get_json(SEARCH_URL.format(
        term=requests.utils.quote(f"{division} Ireland")))
    found = []

    def walk(obj):
        if isinstance(obj, dict):
            t = str(obj.get("type", "")).lower()
            name = str(obj.get("name") or obj.get("text") or "")
            oid = obj.get("id") or obj.get("leagueId")
            country = str(obj.get("countryCode") or obj.get("ccode") or "")
            if oid and division in name.lower() and (
                    "league" in t or "IRL" in country.upper()
                    or "ireland" in name.lower()):
                found.append(str(oid).split("|")[-1])
            if "|" in name and division in name.lower():
                tail = name.rsplit("|", 1)[-1]
                if tail.isdigit():
                    found.append(tail)
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for i in obj:
                walk(i)

    walk(data)
    return found[0] if found else None


def league_teams(lid, debug=False):
    data = get_next_data(LEAGUE_URL.format(lid=lid),
                         debug_name=f"league_{lid}" if debug else None)
    teams = {}

    def walk(obj):
        if isinstance(obj, dict):
            oid = obj.get("id")
            name = obj.get("name")
            url = str(obj.get("pageUrl") or "")
            if oid and name and "/teams/" in url:
                teams[str(oid)] = str(name)
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for i in obj:
                walk(i)

    walk(data)
    return teams


STAFF_WORDS = ("coach", "manager", "staff", "assistant", "physio",
               "analyst", "director", "keeper coach", "head coach")


def looks_like_staff(*labels):
    for lab in labels:
        n = norm(str(lab or ""))
        if any(w in n for w in STAFF_WORDS):
            return True
    return False


def team_irish_players(tid, tname="", debug=False):
    url = TEAM_URL.format(tid=tid, tslug=slugify(tname or "team"))
    try:
        data = get_next_data(url, debug_name=f"team_{tid}" if debug else None)
    except Exception as _e:
        # some club squad pages keep 500ing - fall back to the JSON API, which
        # the shape-agnostic walk below reads just the same
        data = None
        for _api in (f"https://www.fotmob.com/api/teams?id={tid}",
                     f"https://www.fotmob.com/api/data/teams?id={tid}"):
            try:
                data = get_json(_api, debug_name=f"teamapi_{tid}" if debug else None)
                break
            except Exception:
                data = None
        if data is None:
            raise _e
    role_map = {"keeper": "GK", "goalkeeper": "GK", "defender": "DEF",
                "midfielder": "MID", "attacker": "FWD", "forward": "FWD"}
    players = {}

    def is_irish(d):
        cc = str(d.get("ccode") or d.get("countryCode") or "").upper()
        cn = norm(str(d.get("cname") or d.get("countryName") or ""))
        return cc == "IRL" or cn == "ireland" or cn == "republic of ireland"

    def walk(obj, role=""):
        if isinstance(obj, dict):
            r = obj.get("title") or obj.get("role") or role
            if isinstance(r, dict):
                r = r.get("fallback") or r.get("key") or ""
            oid, name = obj.get("id"), obj.get("name")
            if looks_like_staff(r, obj.get("role"), obj.get("title"),
                                obj.get("positionLabel")):
                oid = None                     # managers aren't players
            if oid and name and is_irish(obj):
                pos = ""
                for k, v in role_map.items():
                    if k in norm(str(r)) or k in norm(
                            str(obj.get("positionId") or "") +
                            str(obj.get("role") or "")):
                        pos = v
                        break
                players[str(oid)] = (str(name), pos)
            for v in obj.values():
                walk(v, role=r if isinstance(r, str) else role)
        elif isinstance(obj, list):
            for i in obj:
                walk(i, role=role)

    for sq in find_all(data, "squad"):
        walk(sq)
    if not players:
        walk(data)
    return players


def discover_loi(args):
    if args.league_id:
        league_ids = args.league_id.split(",")
    else:
        league_ids = []
        for div in ("premier division", "first division"):
            lid = find_league_id(div)
            if lid:
                league_ids.append(lid)
                print(f"{div}: league id {lid}")
            else:
                print(f"WARNING: couldn't auto-detect the {div} - pass "
                      f"--league-id (comma-separated, from fotmob URLs)")
            time.sleep(SLEEP)
    if not league_ids:
        sys.exit("No league ids.")

    teams = {}
    for lid in league_ids:
        t = league_teams(lid, debug=args.debug)
        if not t:
            print(f"No teams found for league {lid} - rerun with --debug "
                  f"and send scraper/debug/league_{lid}.json")
        teams.update(t)
        time.sleep(SLEEP)
    if not teams:
        sys.exit("No teams found at all.")
    print(f"{len(teams)} teams: {', '.join(sorted(teams.values()))}")

    players = read_player_list()
    known_slugs = {p["slug"] for p in players}
    known_ids = {v.get("fotmob_id") for v in read_id_cache().values()}
    cache = read_id_cache()
    added = []

    for tid, tname in sorted(teams.items(), key=lambda x: x[1]):
        time.sleep(SLEEP)
        try:
            irish = team_irish_players(tid, tname, debug=args.debug)
        except Exception as e:
            print(f"  {tname}: FAILED ({e})")
            continue
        new = 0
        for pid, (pname, pos) in irish.items():
            slug = slugify(pname)
            if slug in known_slugs or pid in known_ids:
                continue
            players.append({"slug": slug, "name": pname, "club": tname,
                            "league": "League of Ireland", "tier": "loi",
                            "pos": pos, "ireland_level": ""})
            cache[slug] = {"fotmob_id": pid, "fotmob_name": pname,
                           "fotmob_team": tname, "note": "discovered"}
            known_slugs.add(slug)
            added.append(f"{slug} ({tname})")
            new += 1
        print(f"  {tname}: {len(irish)} Irish in squad, {new} new")

    if added:
        cols = ["slug", "name", "club", "league", "tier", "pos",
                "ireland_level"]
        players.sort(key=lambda p: p["slug"])
        with open(PLAYER_LIST, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(players)
        write_id_cache(cache)
        print(f"\nadded {len(added)} players to players_list.csv "
              f"(ireland_level left blank - fill by hand if you care):")
        for a in added:
            print(f"  + {a}")
    else:
        print("\nno new players found")


def append_players(new_players, cache, note="added"):
    """Append players (dicts with slug/name/club/pos/ireland_level +
    fotmob_id) to players_list.csv and the ID cache. Skips known."""
    players = read_player_list()
    known_slugs = {p["slug"] for p in players}
    # a blank id is "we could not find them", not "we have them" - counting
    # blanks here made every stub look like a duplicate and silently dropped
    # the non-league and academy players the source has no page for
    known_ids = {v.get("fotmob_id") for v in cache.values()
                 if (v.get("fotmob_id") or "").strip()}
    added = []
    for np in new_players:
        nid = (np.get("fotmob_id") or "").strip()
        if np["slug"] in known_slugs or (nid and nid in known_ids):
            continue
        players.append({"slug": np["slug"], "name": np["name"],
                        "club": np.get("club", ""),
                        "league": np.get("league", ""),
                        "tier": np.get("tier", ""),
                        "pos": np.get("pos", ""),
                        "ireland_level": np.get("ireland_level", "")})
        cache[np["slug"]] = {"fotmob_id": np["fotmob_id"],
                             "fotmob_name": np["name"],
                             "fotmob_team": np.get("club", ""),
                             "note": note}
        known_slugs.add(np["slug"])
        added.append(np["slug"])
    if added:
        cols = ["slug", "name", "club", "league", "tier", "pos",
                "ireland_level"]
        players.sort(key=lambda p: p["slug"])
        with open(PLAYER_LIST, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(players)
        write_id_cache(cache)
    return added


def add_players(args):
    cache = read_id_cache()
    new = []
    for raw in args.ids:
        m = re.search(r"(\d{4,})", raw)
        if not m:
            print(f"  no id in '{raw}' - skipped")
            continue
        pid = m.group(1)
        try:
            data = get_next_data(PLAYER_URL.format(pid=pid, slug="x"))
            blob = get_player_blob(data)
        except Exception as e:
            print(f"  {pid}: fetch failed ({e})")
            continue
        name = blob.get("name") or first(find_all(blob, "name")) or pid
        pt = blob.get("primaryTeam") or {}
        pos = ""
        pd = blob.get("positionDescription") or {}
        prim = pd.get("primaryPosition") or {}
        if isinstance(prim, dict):
            lab = norm(str(prim.get("label") or ""))
            for k, v in {"keeper": "GK", "back": "DEF", "defend": "DEF",
                         "midfield": "MID", "winger": "FWD",
                         "striker": "FWD", "forward": "FWD"}.items():
                if k in lab:
                    pos = v
                    break
        new.append({"slug": slugify(str(name)), "name": str(name),
                    "club": pt.get("teamName", ""), "fotmob_id": pid,
                    "pos": pos})
        print(f"  {pid}: {name} ({pt.get('teamName', 'no club')})")
        time.sleep(SLEEP)
    added = append_players(new, cache, note="added by id")
    print(f"\nadded {len(added)}: {', '.join(added) if added else '-'}")
    if added:
        print("run `scrape` to pull their data.")


# the source labels the senior side plain "Ireland"; try both spellings
ARROW = "\u27a1"


def parse_transfer_line(line):
    """'- Name (21) - LB | Old ➡️ New*' -> ('Name', 'New'). Returns None
    for headers, blanks and comments."""
    s = line.strip()
    if not s or s.startswith("#"):
        return None
    if s[0] not in "-•*":
        return None            # headers / section titles
    s = re.sub(r"^[-•*]\s*", "", s)
    s = s.replace("\u2019", "'").replace("*", "").replace("_", "")
    if ARROW in s:
        left, _, right = s.partition(ARROW)
        club = re.sub(r"[\ufe0f\u200d]", "", right).strip(" .")
    else:
        left, club = s, ""
    left = re.split(r"\s*[\(|]", left)[0]
    left = re.split(r"\s+-\s+", left)[0]
    name = re.sub(r"[\ufe0f\u200d\U0001F000-\U0001FAFF"
                  r"\U000E0000-\U000E007F]", "", left).strip(" -")
    words = name.split()
    if not 2 <= len(words) <= 4 or len(name) < 4:
        return None
    if name.upper() == name:      # LEAGUE HEADERS
        return None
    # a name is Capitalised Words, not a sentence
    if not all(w[:1].isupper() or w[:1] in "\u2018'" for w in words):
        return None
    return name, club


def add_list(args):
    path = Path(args.file)
    if not path.exists():
        sys.exit(f"{path} not found")
    entries = []
    seen = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        parsed = parse_transfer_line(line)
        if parsed and norm(parsed[0]) not in seen:
            seen.add(norm(parsed[0]))
            entries.append(parsed)
    print(f"parsed {len(entries)} names from {path.name}")

    cache = read_id_cache()
    have = {norm(p["name"]) for p in read_player_list()}
    new, notfound, unsure = [], [], []

    for i, (name, club) in enumerate(entries):
        if norm(name) in have:
            continue
        try:
            cands = search_candidates(name)
        except Exception as e:
            print(f"  [{i+1}/{len(entries)}] {name}: search failed ({e})")
            notfound.append(name)
            time.sleep(SLEEP)
            continue
        nname, nclub = norm(name), norm(club)
        surname = nname.split()[-1]
        hits = [c for c in cands
                if norm(c[1]) == nname or nname in norm(c[1])
                or surname in norm(c[1]).split()]
        pick = None
        if len(hits) == 1:
            pick = hits[0]
        elif hits:
            for oid, cname, cteam in hits:
                if nclub and norm(cteam) and (nclub in norm(cteam)
                                              or norm(cteam) in nclub):
                    pick = (oid, cname, cteam)
                    break
            if not pick:
                exact = [h for h in hits if norm(h[1]) == nname]
                if len(exact) == 1:
                    pick = exact[0]
                else:
                    unsure.append(f"{name} ({club or 'no club'}) -> " +
                                  ", ".join(f"{c[1]}/{c[2] or '?'}#{c[0]}"
                                            for c in hits[:4]))
        if pick and args.stub_only:
            pick = None
        if pick:
            oid, cname, cteam = pick
            new.append({"slug": slugify(cname), "name": cname,
                        "club": cteam or club, "fotmob_id": oid,
                        "ireland_level": ""})
            print(f"  [{i+1}/{len(entries)}] {name} -> {cname} "
                  f"({cteam or club or 'no club'})")
        elif not hits:
            notfound.append(f"{name} ({club or 'no club'})")
        time.sleep(SLEEP)

    added = append_players(new, cache, note="from transfer list")
    print(f"\nadded {len(added)} with source data")

    if args.stub_unmatched:
        stubs = []
        for label in unsure:
            nm = label.split(" (")[0]
            cl = label.split(" (")[1].split(")")[0] if " (" in label else ""
            stubs.append({"slug": slugify(nm), "name": nm,
                          "club": "" if cl == "no club" else cl,
                          "fotmob_id": ""})
        for label in notfound:
            nm = label.split(" (")[0]
            cl = label.split(" (")[1].split(")")[0] if " (" in label else ""
            stubs.append({"slug": slugify(nm), "name": nm,
                          "club": "" if cl == "no club" else cl,
                          "fotmob_id": ""})
        stubbed = append_players(stubs, cache, note="no source data")
        print(f"added {len(stubbed)} as stubs (no match/result data "
              f"until an id is filled in)")
    if unsure:
        print(f"\n{len(unsure)} ambiguous - add by id with "
              f"`add <id>` if you want them:")
        for u in unsure:
            print(f"  ? {u}")
    if notfound:
        print(f"\n{len(notfound)} not on the source (likely academy "
              f"or non-league):")
        for n in notfound:
            print(f"  - {n}")
    if added:
        print("\nrun `scrape` to pull their data.")


def set_id(args):
    """set-id <slug> <id> [<slug> <id> ...] - fill a fotmob id on a
    player who's already in the list (name differs on the source)."""
    if len(args.pairs) % 2:
        sys.exit("give pairs: set-id <slug> <id> [<slug> <id> ...]")
    players = {p["slug"] for p in read_player_list()}
    cache = read_id_cache()
    for slug, raw in zip(args.pairs[::2], args.pairs[1::2]):
        m = re.search(r"(\d{3,})", raw)
        if not m:
            print(f"  no id in '{raw}'")
            continue
        if slug not in players:
            print(f"  {slug} is not in players_list.csv - skipped")
            continue
        pid = m.group(1)
        try:
            blob = get_player_blob(get_next_data(
                PLAYER_URL.format(pid=pid, slug=slug)))
            nm = blob.get("name") or "?"
            club = (blob.get("primaryTeam") or {}).get("teamName", "")
        except Exception as e:
            print(f"  {slug} -> {pid}: couldn't verify ({e})")
            nm, club = "?", ""
        entry = cache.get(slug, {})
        entry.update({"fotmob_id": pid, "fotmob_name": nm,
                      "fotmob_team": club, "note": "id set by hand"})
        cache[slug] = entry
        print(f"  {slug} -> {pid}  ({nm}, {club or 'no club'})")
        time.sleep(SLEEP)
    write_id_cache(cache)
    print("\nrun `scrape` to pull their data.")


def clear_id(args):
    """Blank a wrong source id so it can be resolved again."""
    cache = read_id_cache()
    for slug in args.slugs:
        if slug in cache:
            cache[slug] = {"fotmob_id": "", "note": "cleared by hand"}
            print(f"  cleared {slug}")
        else:
            print(f"  {slug} not in the cache")
    write_id_cache(cache)


def dedupe(args):
    """Drop roster entries that share a source id with another entry."""
    players = read_player_list()
    cache = read_id_cache()
    by_id = {}
    for p in players:
        pid = cache.get(p["slug"], {}).get("fotmob_id")
        if pid:
            by_id.setdefault(pid, []).append(p)

    drop = []
    for pid, group in by_id.items():
        if len(group) < 2:
            continue
        fm_name = cache[group[0]["slug"]].get("fotmob_name", "")
        want = slugify(fm_name) if fm_name else ""
        # keep the slug matching the source's own spelling, else the
        # longest name (the fuller form), else the first
        keep = next((p for p in group if p["slug"] == want), None)
        if not keep:
            keep = max(group, key=lambda p: len(p.get("name", "")))
        for p in group:
            if p["slug"] != keep["slug"]:
                drop.append((p["slug"], keep["slug"], pid))

    if not drop:
        print("no duplicates")
        return
    print(f"{len(drop)} duplicate roster entries:")
    for slug, keep, pid in drop:
        print(f"  - {slug}  (same player as {keep}, id {pid})")
    if args.dry_run:
        print("\ndry run - nothing changed. Drop --dry-run to apply.")
        return

    gone = {d[0] for d in drop}
    kept = [p for p in players if p["slug"] not in gone]
    cols = ["slug", "name", "club", "league", "tier", "pos",
            "ireland_level"]
    with open(PLAYER_LIST, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(kept)
    for slug in gone:
        cache.pop(slug, None)
    write_id_cache(cache)
    print(f"\nremoved {len(gone)}; {len(kept)} players left")
    print("the site may still have pages for the old slugs - worth a "
          "redirect or a rebuild.")


IRELAND_TEAMS = [("Ireland", "Republic of Ireland"),
                 ("Ireland U21", "Republic of Ireland U21"),
                 ("Ireland U20", "Republic of Ireland U20"),
                 ("Ireland U19", "Republic of Ireland U19"),
                 ("Ireland U17", "Republic of Ireland U17")]


def find_team_id_any(*terms):
    for t in terms:
        tid = find_team_id(t)
        if tid:
            return tid, t
        time.sleep(SLEEP)
    return None, terms[0]


def find_team_id(term):
    data = get_json(SEARCH_URL.format(term=requests.utils.quote(term)))
    hits = []

    def walk(obj):
        if isinstance(obj, dict):
            t = str(obj.get("type", "")).lower()
            name = str(obj.get("name") or obj.get("text") or "")
            oid = obj.get("id") or obj.get("teamId")
            if oid and norm(name.split("|")[0]) == norm(term) and \
                    "player" not in t:
                hits.append(str(oid).split("|")[-1])
            if "|" in name and norm(name.rsplit("|", 1)[0]) == norm(term):
                tail = name.rsplit("|", 1)[-1]
                if tail.isdigit():
                    hits.append(tail)
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for i in obj:
                walk(i)

    walk(data)
    return hits[0] if hits else None


def discover_ireland(args):
    cache = read_id_cache()
    level_of = {"ireland": "Senior", "ireland u21": "U21",
                "ireland u20": "U20", "ireland u19": "U19",
                "ireland u17": "U17"}
    total = []
    for terms in IRELAND_TEAMS:
        tid, term = find_team_id_any(*terms)
        if not tid:
            print(f"{terms[0]}: team not found on source - skipped")
            continue
        try:
            squad = team_irish_players(tid, term, debug=args.debug)
        except Exception as e:
            print(f"{term}: FAILED ({e})")
            continue
        lvl = level_of[norm(terms[0])]
        new = [{"slug": slugify(n), "name": n, "fotmob_id": pid,
                "pos": pos, "ireland_level": lvl}
               for pid, (n, pos) in squad.items()]
        added = append_players(new, cache, note=f"ireland {lvl} squad")
        total += added
        print(f"{term}: {len(squad)} in squad, {len(added)} new")
        time.sleep(SLEEP)
    print(f"\ntotal new: {len(total)}")
    for a in total:
        print(f"  + {a}")
    if total:
        print("run `scrape` to pull their data.")


TEAM_FIXTURES_URL = ("https://www.fotmob.com/teams/{tid}/fixtures/"
                     "{tslug}")
TEAM_OVERVIEW_URL = ("https://www.fotmob.com/teams/{tid}/overview/"
                     "{tslug}")


def parse_team_matches(data):
    """Pull every match-like record out of a team page."""
    out = {}

    def add(m):
        if not isinstance(m, dict):
            return
        home, away = m.get("home"), m.get("away")
        st = m.get("status") or {}
        if not isinstance(st, dict):
            st = {}
        utc = st.get("utcTime") or m.get("utcTime")
        if not (isinstance(home, dict) and isinstance(away, dict) and utc):
            return
        d = parse_iso(utc)
        if not d:
            return
        mid = str(m.get("id") or m.get("matchId") or utc)
        scores = (home.get("score"), away.get("score"))
        if scores == (None, None):
            ss = str(st.get("scoreStr") or "")
            mm = re.match(r"\s*(\d+)\s*-\s*(\d+)", ss)
            scores = (int(mm.group(1)), int(mm.group(2))) if mm else ("", "")
        out[mid] = {
            "id": mid, "utc": d,
            "comp": (m.get("tournament") or {}).get("name", "")
            if isinstance(m.get("tournament"), dict)
            else (m.get("leagueName") or ""),
            "home": home.get("name", ""), "away": away.get("name", ""),
            "hs": scores[0] if scores[0] is not None else "",
            "as": scores[1] if scores[1] is not None else "",
            "home_id": str(home.get("id") or ""),
            "away_id": str(away.get("id") or ""),
            "finished": bool(st.get("finished")),
            "started": bool(st.get("started")),
            "url": m.get("pageUrl", "") or "",
        }

    def walk(o):
        if isinstance(o, dict):
            add(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for i in o:
                walk(i)

    walk(data)
    return sorted(out.values(), key=lambda m: m["utc"])


def team_fixtures(tid, tname, debug=False, _cache={}):
    if tid in _cache:
        return _cache[tid]
    ms = []
    try:
        data = get_next_data(
            TEAM_FIXTURES_URL.format(tid=tid, tslug=slugify(tname or "team")),
            debug_name=f"teamfix_{tid}" if debug else None)
        ms = parse_team_matches(data)
    except Exception as e:
        print(f"    fixtures fetch failed for {tname}: {e}")
    _cache[tid] = ms
    time.sleep(SLEEP)
    return ms


def team_venue(tid, tname, debug=False, _cache={}):
    """(lat, lon, town) for a club's stadium, blanks when unknown."""
    if tid in _cache:
        return _cache[tid]
    lat = lon = town = ""
    try:
        data = get_next_data(
            TEAM_OVERVIEW_URL.format(tid=tid, tslug=slugify(tname or "team")),
            debug_name=f"teamvenue_{tid}" if debug else None)
        for v in find_all(data, "venue"):
            if not isinstance(v, dict):
                continue
            w = v.get("widget") or v
            coords = w.get("location") or w.get("coordinates") or {}
            if isinstance(coords, list) and len(coords) == 2:
                lat, lon = coords[0], coords[1]
            elif isinstance(coords, dict):
                lat = coords.get("lat", coords.get("latitude", "")) or lat
                lon = coords.get("lng", coords.get("longitude", "")) or lon
            town = w.get("city") or w.get("town") or town
            if lat and lon:
                break
        if not (lat and lon):
            for c in find_all(data, "coordinates"):
                if isinstance(c, list) and len(c) == 2:
                    lat, lon = c
                    break
                if isinstance(c, dict) and c.get("lat"):
                    lat, lon = c.get("lat"), c.get("lng") or c.get("lon")
                    break
        if not town:
            town = first(find_all(data, "city")) or ""
    except Exception as e:
        print(f"    venue fetch failed for {tname}: {e}")
    _cache[tid] = (str(lat or ""), str(lon or ""), str(town or ""))
    time.sleep(SLEEP)
    return _cache[tid]


def clubs_file(args):
    """Keep data/manual/clubs.csv - club, fotmob id, and the ground's
    coordinates, which is how the site knows what country a club is in.
    Rows already carrying coordinates are kept as they are: a club resolved
    once should never go missing again, and --missing-only skips straight to
    the clubs that still have none."""
    ppath = Path(args.out) / "data/api/players.csv"
    if not ppath.exists():
        sys.exit("data/api/players.csv not found - run `scrape` first.")
    with open(ppath, newline="", encoding="utf-8") as f:
        club_names = sorted({r["club"] for r in csv.DictReader(f)
                             if r.get("club")})
    out_path = Path(args.out) / "data/manual/clubs.csv"
    kept = {}
    if out_path.exists():
        with open(out_path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if (r.get("club") or "").strip():
                    kept[r["club"].strip()] = [r.get("club", ""), r.get("club_id", ""),
                                               r.get("lat", ""), r.get("lon", ""),
                                               r.get("town", "")]
    club_names = sorted(set(club_names) | set(kept))
    settled = {n for n, r in kept.items() if r[2] and r[3]}
    if getattr(args, "missing_only", False):
        club_names = [n for n in club_names if n not in settled]
        print(f"clubs: {len(settled)} already located, "
              f"{len(club_names)} to look up")
    cache = read_id_cache()
    team_ids = {}
    for slug, e in cache.items():
        if e.get("fotmob_team") and e.get("team_id"):
            team_ids[e["fotmob_team"]] = e["team_id"]

    def senior_name(name):
        """'Benfica U19' / 'Southampton U18' / 'Fleetwood Town Academy'
        -> the senior club name."""
        s = re.sub(r"\s+(u|under[- ]?)\d{2}\b", "", name,
                   flags=re.I)
        s = re.sub(r"\s+(academy|reserves|youth|ii|b team)$", "", s,
                   flags=re.I)
        return s.strip()

    rows, missing, resolved = [], [], {}
    for name, r in kept.items():
        if r[2] and r[3]:
            resolved.setdefault(name, (r[2], r[3], r[4]))
    for name in club_names:
        if name in settled and getattr(args, "missing_only", False):
            continue
        try:
            tid = team_ids.get(name) or find_team_id(name)
            time.sleep(SLEEP)
            lat = lon = town = ""
            if tid:
                lat, lon, town = team_venue(tid, name, debug=args.debug)
        except Exception as e:                 # one bad club never sinks the run
            print(f"  {name}: lookup failed ({e})")
            tid = lat = lon = town = ""
        note = ""
        if not (lat and lon):
            parent = senior_name(name)
            if parent != name:
                if parent in resolved:
                    lat, lon, town = resolved[parent]
                else:
                    try:
                        ptid = team_ids.get(parent) or find_team_id(parent)
                        time.sleep(SLEEP)
                        if ptid:
                            lat, lon, town = team_venue(ptid, parent,
                                                        debug=args.debug)
                            resolved[parent] = (lat, lon, town)
                    except Exception as e:
                        print(f"  {parent}: lookup failed ({e})")
                if lat and lon:
                    note = f"  (from {parent})"
        rows.append([name, tid or "", lat, lon, town])
        if lat and lon:
            resolved.setdefault(name, (lat, lon, town))
        else:
            missing.append(name)
        print(f"  {name}: {lat or '?'},{lon or '?'} {town}{note}")

    # merge over what was already there; a row never loses coordinates it had
    for row in rows:
        prev = kept.get(row[0])
        if prev and prev[2] and prev[3] and not (row[2] and row[3]):
            row[2], row[3], row[4] = prev[2], prev[3], prev[4] or row[4]
        kept[row[0]] = row
    write_csv(out_path, ["club", "club_id", "lat", "lon", "town"],
              [kept[k] for k in sorted(kept)])
    if missing:
        print(f"\n{len(missing)} clubs with no coordinates from the source "
              f"- fill by hand:")
        for m in missing:
            print(f"  - {m}")


def ireland_file(args):
    """Write data/manual/ireland.csv - senior + U21 fixtures & results."""
    rows = []
    for terms, label in ((("Ireland", "Republic of Ireland"), "senior"),
                         (("Ireland U21", "Republic of Ireland U21"),
                          "u21")):
        tid, term = find_team_id_any(*terms)
        if not tid:
            print(f"{terms[0]}: not found - skipped")
            continue
        ms = team_fixtures(tid, term, debug=args.debug)
        for m in ms:
            status = ("ft" if m["finished"]
                      else "live" if m["started"] else "scheduled")
            rows.append([label, iso_z(m["utc"]), m["comp"],
                         m["home"], m["away"],
                         m["hs"] if status != "scheduled" else "",
                         m["as"] if status != "scheduled" else "",
                         status])
        print(f"{term}: {len(ms)} matches")
    rows.sort(key=lambda r: (r[0], r[1]))
    write_csv(Path(args.out) / "data/manual/ireland.csv",
              ["team", "kickoff", "competition", "home", "away",
               "home_score", "away_score", "status"], rows)


# ---------------------------------------------------------------- scrape

def parse_iso(ts):
    """FotMob utcTime like '2026-08-23T19:45:00Z' or with .000Z / offset."""
    if not ts:
        return None
    ts = str(ts).replace(".000Z", "Z")
    try:
        d = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return d.astimezone(dt.timezone.utc)
    except ValueError:
        return None


def iso_z(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def get_player_blob(data):
    """Player payload lives at props.pageProps.data."""
    props = data.get("props", {}).get("pageProps", {})
    blob = props.get("data")
    if isinstance(blob, dict) and "recentMatches" in blob:
        return blob
    # fallback: pageProps.fallback["player:<id>"]
    fb = props.get("fallback", {})
    if isinstance(fb, dict):
        for k, v in fb.items():
            if str(k).startswith("player:") and isinstance(v, dict):
                return v
    raise RuntimeError("player data not found in page")


def extract_matches(blob):
    """recentMatches entries are player-centric; nextMatch is one fixture."""
    out = []
    for m in blob.get("recentMatches") or []:
        if not isinstance(m, dict):
            continue
        md = m.get("matchDate") or {}
        utc = parse_iso(md.get("utcTime") if isinstance(md, dict) else md)
        if not utc:
            continue
        is_home = bool(m.get("isHomeTeam"))
        team = m.get("teamName", "")
        opp = m.get("opponentTeamName", "")
        rating = (m.get("ratingProps") or {}).get("rating") or ""
        if rating in (0, "0"):
            rating = ""
        tid = str(m.get("teamId") or "")
        oid = str(m.get("opponentTeamId") or "")
        out.append({
            "id": str(m.get("id") or utc),
            "team": team,
            "url": m.get("matchPageUrl", "") or "",
            "utc": utc,
            "comp": m.get("leagueName", "") or "",
            "home": team if is_home else opp,
            "away": opp if is_home else team,
            "home_id": tid if is_home else oid,
            "away_id": oid if is_home else tid,
            "hscore": m.get("homeScore", ""),
            "ascore": m.get("awayScore", ""),
            "finished": True,
            "ongoing": False,
            "minute": "",
            "minutes": m.get("minutesPlayed", 0) or 0,
            "goals": m.get("goals", 0) or 0,
            "assists": m.get("assists", 0) or 0,
            "rating": rating,
            "played": bool(m.get("playedInMatch")),
            "side": "H" if is_home else "A",
            "opponent": opp,
        })
    nm = blob.get("nextMatch")
    if isinstance(nm, dict) and nm.get("matchId"):
        st = nm.get("status") or {}
        utc = parse_iso(st.get("utcTime") or nm.get("matchDate"))
        if utc:
            team = (blob.get("primaryTeam") or {}).get("teamName", "")
            home, away = nm.get("homeName", ""), nm.get("awayName", "")
            side = None
            if norm(team) and norm(team) == norm(home):
                side = "H"
            elif norm(team) and norm(team) == norm(away):
                side = "A"
            ongoing = bool(st.get("started")) and not st.get("finished")
            out.append({
                "id": str(nm.get("matchId")),
                "team": team,
                "url": nm.get("matchUrl", "") or "",
                "utc": utc,
                "comp": nm.get("leagueName", "") or "",
                "home": home, "away": away,
                "home_id": str(nm.get("homeId") or ""),
                "away_id": str(nm.get("awayId") or ""),
                "hscore": "", "ascore": "",
                "finished": bool(st.get("finished")),
                "ongoing": ongoing,
                "minute": "",
                "minutes": 0, "goals": 0, "assists": 0, "rating": "",
                "played": False,
                "side": side,
                "opponent": away if side == "H" else home,
            })
    return out


def team_side(match, club_name, fotmob_team):
    """Which side is the player's club? Returns 'H','A' or None."""
    for candidate in (fotmob_team, club_name):
        nc = norm(candidate)
        if not nc:
            continue
        nh, na = norm(match["home"]), norm(match["away"])
        if nc == nh or nc in nh or nh in nc:
            return "H"
        if nc == na or nc in na or na in nc:
            return "A"
    return None


def extract_career(blob):
    """Return (senior_caps, senior_goals, senior_debut, youth_str,
               c_apps, c_goals, c_assists)."""
    items = (blob.get("careerHistory") or {}).get("careerItems") or {}
    sr_caps = sr_goals = sr_debut = ""
    youth = []
    c_apps = c_goals = c_assists = 0
    have_career = False

    def num(v):
        try:
            return int(str(v))
        except (TypeError, ValueError):
            return None

    for section, content in items.items():
        entries = (content or {}).get("teamEntries") or [] \
            if isinstance(content, dict) else []
        nsec = norm(str(section))
        for e in entries:
            if not isinstance(e, dict):
                continue
            team = str(e.get("team") or "")
            tnorm = norm(team).replace(" ", "")
            apps, goals, assists = (num(e.get("appearances")),
                                    num(e.get("goals")),
                                    num(e.get("assists")))
            start = str(e.get("startDate") or "")[:4]
            if "national" in nsec:
                m = re.search(r"u(?:nder)?\s*(\d\d)", tnorm)
                if m:
                    lvl = "U" + m.group(1)
                    youth.append((lvl,
                                  apps if apps is not None else "",
                                  goals if goals is not None else "",
                                  start))
                elif "women" not in tnorm:
                    if apps is not None:
                        sr_caps = apps
                    if goals is not None:
                        sr_goals = goals
                    if start:
                        sr_debut = start
            elif "senior" in nsec:
                if apps is not None:
                    c_apps += apps
                    have_career = True
                if goals is not None:
                    c_goals += goals
                if assists is not None:
                    c_assists += assists

    order = {"U23": 0, "U21": 1, "U20": 2, "U19": 3, "U18": 4,
             "U17": 5, "U16": 6, "U15": 7}
    youth.sort(key=lambda y: order.get(y[0], 9))
    youth_str = "; ".join(f"{l}:{a}:{g}:{y}" for l, a, g, y in youth)
    if not have_career:
        c_apps = c_goals = c_assists = ""
    return sr_caps, sr_goals, sr_debut, youth_str, c_apps, c_goals, c_assists


def extract_season_stats(blob):
    """Best-effort current-season league stats + rating from mainLeague."""
    stats = {"apps": "", "starts": "", "goals": "", "assists": "",
             "mins": "", "yellow": "", "red": "", "rating": "", "league": ""}
    stats["season"] = ""
    ml = first(find_all(blob, "mainLeague"))
    if isinstance(ml, dict):
        stats["league"] = ml.get("leagueName", "") or ""
        sn = str(ml.get("season") or "")
        m = re.match(r"(\d{4})/(\d{4})$", sn)
        stats["season"] = f"{m.group(1)}/{m.group(2)[2:]}" if m else sn
        wanted = {
            "matches": "apps", "matches played": "apps",
            "appearances": "apps", "started": "starts", "starts": "starts",
            "goals": "goals", "assists": "assists",
            "minutes played": "mins", "minutes": "mins",
            "yellow cards": "yellow", "red cards": "red",
            "rating": "rating", "fotmob rating": "rating",
        }
        for st in find_all(ml, "stats"):
            items = st if isinstance(st, list) else [st]
            for it in items:
                if not isinstance(it, dict):
                    continue
                title = norm(str(it.get("title") or it.get("localizedTitleId")
                                 or it.get("name") or ""))
                val = it.get("value")
                if title in wanted and val is not None:
                    key = wanted[title]
                    if stats[key] == "":
                        stats[key] = val
    if stats["rating"] != "":
        try:
            stats["rating"] = f"{float(stats['rating']):.2f}"
        except ValueError:
            pass
    return stats


def current_season_label(now, sample=""):
    """Label for the season in progress. Calendar-year leagues (LOI) use
    a bare year, matching how the source labels them."""
    if re.fullmatch(r"\d{4}", str(sample or "").strip()):
        return str(now.year), (dt.datetime(now.year, 1, 1,
                                           tzinfo=dt.timezone.utc),
                               dt.datetime(now.year, 12, 31,
                                           tzinfo=dt.timezone.utc))
    y = now.year if now.month >= 7 else now.year - 1
    return (f"{y}/{str(y + 1)[2:]}",
            (dt.datetime(y, 7, 1, tzinfo=dt.timezone.utc),
             dt.datetime(y + 1, 6, 30, tzinfo=dt.timezone.utc)))


def season_from_matches(mlist, league, now):
    """Aggregate this season's club stats from the player's own match
    list. Used when the source's stats block is a season behind."""
    label, (start, end) = current_season_label(now)
    played = [m for m in mlist
              if m["played"] and start <= m["utc"] <= end
              and "friendl" not in norm(m["comp"])
              and "ireland" not in norm(m.get("team", ""))]
    if league:
        inleague = [m for m in played if norm(m["comp"]) == norm(league)]
        if inleague:
            played = inleague
    if not played:
        return None
    mins = sum(int(m["minutes"] or 0) for m in played)
    goals = sum(int(m["goals"] or 0) for m in played)
    assists = sum(int(m["assists"] or 0) for m in played)
    rated = [(float(m["rating"]), 1) for m in played
             if str(m["rating"]) not in ("", "None")]
    rating = (f"{sum(r for r, _ in rated) / len(rated):.2f}"
              if rated else "")
    return {"season": label, "apps": len(played), "starts": "",
            "goals": goals, "assists": assists, "mins": mins,
            "yellow": "", "red": "", "rating": rating,
            "league": league}


def season_splits(mlist, now, label=None):
    """Per-(team, competition) breakdown of one season's club football,
    built from the player's own match list. The stats block labels the
    whole season with one club, but the matches remember which shirt each
    game was played in - loans and mid-season moves come out as separate
    splits. Sorted by minutes, biggest first. label picks WHICH season to
    split ("2025/26" or a bare year for calendar leagues); the stats block
    can sit a season behind, and its matches live in that older window."""
    m = re.fullmatch(r"(\d{4})/(\d{2})", str(label or "").strip())
    if m:
        y = int(m.group(1))
        start = dt.datetime(y, 7, 1, tzinfo=dt.timezone.utc)
        end = dt.datetime(y + 1, 6, 30, tzinfo=dt.timezone.utc)
    elif re.fullmatch(r"\d{4}", str(label or "").strip()):
        y = int(str(label).strip())
        start = dt.datetime(y, 1, 1, tzinfo=dt.timezone.utc)
        end = dt.datetime(y, 12, 31, tzinfo=dt.timezone.utc)
    else:
        label, (start, end) = current_season_label(now)
    agg, order = {}, []
    for m in mlist:
        if not (m.get("played") and start <= m["utc"] <= end):
            continue
        if "friendl" in norm(m.get("comp", "")):
            continue
        if "ireland" in norm(m.get("team", "")):
            continue
        key = (norm(m.get("team", "")), norm(m.get("comp", "")))
        if key not in agg:
            tid = m.get("home_id") if m.get("side") == "H" else m.get("away_id")
            agg[key] = {"team": m.get("team", ""), "team_id": str(tid or ""),
                        "comp": m.get("comp", ""), "apps": 0, "goals": 0,
                        "assists": 0, "mins": 0, "_r": []}
            order.append(key)
        a = agg[key]
        a["apps"] += 1
        a["goals"] += int(m.get("goals") or 0)
        a["assists"] += int(m.get("assists") or 0)
        a["mins"] += int(m.get("minutes") or 0)
        if str(m.get("rating")) not in ("", "None"):
            try:
                a["_r"].append(float(m["rating"]))
            except ValueError:
                pass
    out = []
    for key in order:
        a = agg[key]
        a["rating"] = (f"{sum(a['_r']) / len(a['_r']):.2f}"
                       if a["_r"] else "")
        del a["_r"]
        out.append(a)
    out.sort(key=lambda a: -a["mins"])
    return label, out


def extract_personal(blob):
    """age, born(blank - fotmob lacks town), foot.

    The date of birth comes first, because it is checkable. The page's own
    "Age" field is only a fallback and has to be a believable age: matching
    any title *containing* "age" used to catch "Average rating" and a season
    field, which is how teenagers ended up recorded as 2025 years old."""
    age = foot = ""

    bd = first(find_all(blob, "birthDate"))
    if isinstance(bd, dict):
        bd = bd.get("utcTime")
    d = parse_iso(bd) if isinstance(bd, str) else None
    if d:
        today = dt.datetime.now(dt.timezone.utc)
        years = today.year - d.year - ((today.month, today.day)
                                       < (d.month, d.day))
        if 5 <= years <= 70:
            age = str(years)

    for pi in find_all(blob, "playerInformation"):
        items = pi if isinstance(pi, list) else [pi]
        for it in items:
            if not isinstance(it, dict):
                continue
            title = norm(str(it.get("title") or it.get("localizedTitleId")
                             or ""))
            val = it.get("value")
            if isinstance(val, dict):
                val = val.get("numberValue") or val.get("fallback") or ""
            if title == "age" and age == "" and val:
                m = re.search(r"\d+", str(val))
                if m and 14 <= int(m.group(0)) <= 50:
                    age = m.group(0)
            if "foot" in title and foot == "" and val:
                foot = str(val).strip().capitalize()
    return age, "", foot


def download_image(pid, slug, img_dir, refresh=False):
    dest = img_dir / f"{slug}.png"
    if dest.exists() and not refresh:
        return "cached"
    r = SESSION.get(IMAGE_URL.format(pid=pid), timeout=25)
    if r.status_code == 200 and r.content[:8].startswith(b"\x89PNG"):
        img_dir.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.content)
        return "ok"
    return "none"


def team_primary_league(tid, tname, _cache={}):
    """Current club's league name from its team page. Cached per run."""
    if tid in _cache:
        return _cache[tid]
    league = ""
    try:
        data = get_next_data(
            f"https://www.fotmob.com/teams/{tid}/overview/"
            f"{slugify(tname or 'team')}")
        for v in find_all(data, "primaryLeagueName"):
            if v:
                league = str(v)
                break
        time.sleep(SLEEP)
    except Exception:
        pass
    _cache[tid] = league
    return league


def parse_table_rows(data):
    """Hunt the league JSON for standings rows. A row is any dict carrying
    a team name/id plus played/pts - the nesting drifts, the fields don't."""
    rows, seen = [], set()
    def walk(o):
        if isinstance(o, dict):
            if ("id" in o and ("name" in o or "shortName" in o)
                    and "played" in o and "pts" in o):
                tid = str(o.get("id") or "")
                if tid and tid not in seen:
                    seen.add(tid)
                    scores = str(o.get("scoresStr") or "")
                    m = re.match(r"(\d+)\s*-\s*(\d+)", scores)
                    rows.append({
                        "team": o.get("name") or o.get("shortName") or "",
                        "team_id": tid,
                        "idx": o.get("idx") or len(rows) + 1,
                        "played": o.get("played", ""),
                        "wins": o.get("wins", ""),
                        "draws": o.get("draws", ""),
                        "losses": o.get("losses", ""),
                        "gf": m.group(1) if m else "",
                        "ga": m.group(2) if m else "",
                        "gd": o.get("goalConDiff", ""),
                        "pts": o.get("pts", ""),
                    })
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for i in o:
                walk(i)
    walk(data)
    rows.sort(key=lambda r: (int(r["idx"]) if str(r["idx"]).isdigit()
                             else 999))
    return rows


def cmd_tables(args):
    """League tables for every league our players sit in ->
    data/api/tables.csv. Curated ids are trusted; discovered ids must
    prove themselves by containing a club we track."""
    root = SCRAPER_DIR.parent
    by_league = {}
    with open(root / "data/api/players.csv", newline="",
              encoding="utf-8") as f:
        for r in csv.DictReader(f):
            lg = (r.get("league") or "").strip()
            cl = (r.get("club") or "").strip()
            if lg and cl:
                by_league.setdefault(lg, set()).add(cl)
    targets = {lg: clubs for lg, clubs in by_league.items()
               if len(clubs) >= 2 or "league of ireland" in norm(lg)
               or norm(lg) in CURATED_LEAGUE_IDS}

    def find_league_ids(term):
        """Search the source for a league by name -> candidate ids."""
        try:
            data = get_json(SEARCH_URL.format(
                term=requests.utils.quote(term)))
        except Exception:
            return []
        found = []
        def walk(obj):
            if isinstance(obj, dict):
                t = str(obj.get("type", "")).lower()
                name = str(obj.get("name") or obj.get("text") or "")
                oid = obj.get("id") or obj.get("leagueId")
                if oid and "league" in t and norm(term) in norm(name):
                    found.append(str(oid).split("|")[-1])
                if "|" in name and norm(term) in norm(name.rsplit("|", 1)[0]):
                    tail = name.rsplit("|", 1)[-1].strip()
                    if tail.isdigit():
                        found.append(tail)
                for v in obj.values():
                    walk(v)
            elif isinstance(obj, list):
                for i in obj:
                    walk(i)
        walk(data)
        seen, out = set(), []
        for f in found:
            if f not in seen:
                seen.add(f)
                out.append(f)
        return out[:4]

    out_rows, done_ids = [], {}
    for lg, clubs in sorted(targets.items(),
                            key=lambda kv: -len(kv[1])):
        nl = norm(lg)
        if nl in CURATED_LEAGUE_IDS:
            cands = [(CURATED_LEAGUE_IDS[nl], True)]
        else:
            cands = [(i, False) for i in find_league_ids(lg)]
            time.sleep(1)
        if not cands:
            print(f"  ?? {lg}: no source league found")
            continue
        best, best_score = None, -1
        for lid, trusted in cands:
            if lid in done_ids:
                rows = done_ids[lid]
            else:
                try:
                    rows = parse_table_rows(get_next_data(
                        LEAGUE_URL.format(lid=lid)))
                except Exception as e:
                    print(f"  !! {lg} (id {lid}): {e}")
                    continue
                done_ids[lid] = rows
                time.sleep(1)
            names = [norm(r["team"]) for r in rows]
            score = sum(1 for c in clubs
                        if any(norm(c) in n or n in norm(c)
                               for n in names if n))
            if trusted:
                score += 1000        # curated ids win unless they 404'd
            if score > best_score and rows:
                best, best_score = (lid, rows), score
        if not best or (best_score <= 0):
            print(f"  ?? {lg}: no candidate table matched our clubs")
            continue
        lid, rows = best
        for r in rows:
            out_rows.append([lg, lid, r["idx"], r["team"], r["team_id"],
                             r["played"], r["wins"], r["draws"],
                             r["losses"], r["gf"], r["ga"], r["gd"],
                             r["pts"]])
        print(f"  ok {lg}: id {lid}, {len(rows)} rows")
    write_csv(root / "data/api/tables.csv",
              ["league", "league_id", "idx", "team", "team_id", "played",
               "wins", "draws", "losses", "gf", "ga", "gd", "pts"],
              out_rows)
    print(f"wrote {len(out_rows)} table rows for "
          f"{len(set(r[0] for r in out_rows))} leagues")


def merge_rows(path, new_rows, touched, header_hint=None):
    """Replace rows for the touched players, keep everyone else's.
    header_hint upgrades the file in place when a scrape starts writing
    more columns than the file has - old rows are padded with blanks."""
    slugs = {p["slug"] for p in touched}
    header, keep = None, []
    if path.exists():
        with open(path, newline="", encoding="utf-8") as f:
            r = csv.reader(f)
            header = next(r, None)
            keep = [row for row in r if row and row[0] not in slugs]
    if header is None:
        return
    if header_hint and len(header_hint) > len(header):
        keep = [row + [""] * (len(header_hint) - len(row)) for row in keep]
        header = list(header_hint)
    rows = keep + [[("" if v is None else v) for v in row]
                   for row in new_rows]
    rows.sort(key=lambda x: (x[0], x[1] if len(x) > 1 else ""))
    write_csv(path, header, rows)


def merge_matches(path, matches_by_id, status_of):
    """Upsert match rows by id, keeping matches for untouched players."""
    header = ["kickoff", "competition", "home", "away", "home_id",
              "away_id", "home_score", "away_score", "status", "minute",
              "players"]
    existing = {}
    if path.exists():
        with open(path, newline="", encoding="utf-8") as f:
            r = csv.DictReader(f)
            for row in r:
                existing[(row["kickoff"], row["home"], row["away"])] = row
    for m, slugs in matches_by_id.values():
        st = status_of(m)
        key = (iso_z(m["utc"]), m["home"], m["away"])
        prev = existing.get(key)
        players = set(slugs)
        if prev:
            players |= set(filter(None, prev["players"].split(";")))
        existing[key] = {
            "kickoff": key[0], "competition": m["comp"],
            "home": m["home"], "away": m["away"],
            "home_id": m.get("home_id", ""),
            "away_id": m.get("away_id", ""),
            "home_score": m["hscore"] if st != "scheduled" else "",
            "away_score": m["ascore"] if st != "scheduled" else "",
            "status": st, "minute": m["minute"] if st == "live" else "",
            "players": ";".join(sorted(players)),
        }
    rows = [[v[h] for h in header]
            for v in sorted(existing.values(), key=lambda x: x["kickoff"])]
    write_csv(path, header, rows)


def scrape(args):
    players = read_player_list()
    cache = read_id_cache()
    if not cache:
        sys.exit("No fotmob_ids.csv - run `resolve` first.")

    out_root = Path(args.out)
    now = dt.datetime.now(dt.timezone.utc)
    past_cut = now - dt.timedelta(days=MATCH_PAST_DAYS)
    future_cut = now + dt.timedelta(days=MATCH_FUTURE_DAYS)
    fixture_cut = now + dt.timedelta(weeks=args.fixture_weeks)
    cache_dirty = False

    matches_by_id = {}   # match id -> (match dict, set(slugs))
    results_rows = []
    fixtures_rows = []
    players_rows = []
    skipped, failed = [], []

    todo = players
    if args.only:
        todo = [p for p in players if p["slug"] in set(args.only.split(","))]
    elif getattr(args, "active", False):
        idx_path = SCRAPER_DIR / "match_index.json"
        if not idx_path.exists():
            sys.exit("--active needs match_index.json - run a full scrape "
                     "first.")
        win = dt.timedelta(hours=36)
        active, active_clubs = set(), set()
        for m in json.loads(idx_path.read_text()):
            ko = parse_iso(m["kickoff"])
            if ko and abs(ko - now) <= win:
                active.update(m["slugs"])
                for cid in (m.get("home_id"), m.get("away_id")):
                    if cid:
                        active_clubs.add(str(cid).strip())
        # A match's slug list is only as good as the last full scrape. If that
        # run missed someone, an --active run can never recover him: he is not
        # in the list that decides who gets scraped, so he is never scraped, so
        # he is never added to the list. His CLUB is in the fixture either way,
        # so take everyone whose club is playing and the loop closes.
        club_of = {}
        pcsv = Path(args.out) / "data/api/players.csv"
        if pcsv.exists():
            with pcsv.open(encoding="utf-8") as fh:
                for r in csv.DictReader(fh):
                    if r.get("club_id"):
                        club_of[r["slug"]] = str(r["club_id"]).strip()
        todo = [p for p in players
                if p["slug"] in active
                or club_of.get(p["slug"]) in active_clubs]
        print(f"--active: {len(todo)} players with a match within 36h "
              f"({len(active)} named in the index, rest by club)")
        if not todo:
            print("nothing to do")
            return

    for i, p in enumerate(todo):
        slug, club = p["slug"], p["club"]
        entry = cache.get(slug, {})
        pid = entry.get("fotmob_id")
        if not pid:
            skipped.append(slug)
            players_rows.append(
                [slug, p.get("league", ""), p.get("club", ""), "", "", "",
                 "", "", "", "", "", "", "", "", "", "", "", "", "", "",
                 "", "", "none"])
            continue
        try:
            data = get_next_data(
                PLAYER_URL.format(pid=pid, slug=slug),
                debug_name=f"player_{slug}" if args.debug else None)
            blob = get_player_blob(data)
        except Exception as e:
            print(f"  [{i+1}/{len(todo)}] {slug}: FAILED ({e})")
            failed.append(slug)
            time.sleep(SLEEP)
            continue

        mlist = extract_matches(blob)

        for m in mlist:
            side = m["side"]

            # matches.csv window
            if past_cut <= m["utc"] <= future_cut:
                mid = m["id"]
                if mid not in matches_by_id:
                    matches_by_id[mid] = (m, set())
                matches_by_id[mid][1].add(slug)

            if m["played"] and side:
                own = m["hscore"] if side == "H" else m["ascore"]
                opp = m["ascore"] if side == "H" else m["hscore"]
                results_rows.append([
                    slug,
                    m["utc"].strftime("%Y-%m-%d"),
                    m["opponent"],
                    f"{own}-{opp}" if own != "" and opp != "" else "",
                    m["comp"],
                    int(m["minutes"] or 0),
                    int(m["goals"] or 0),
                    int(m["assists"] or 0),
                    (f"{float(m['rating']):.2f}"
                     if str(m["rating"]) not in ("", "None") else ""),
                    m["utc"],
                ])
            # upcoming games come from the club fixture list below

        # players.csv row
        season = extract_season_stats(blob)
        (sr_caps, sr_goals, sr_debut, youth,
         c_apps, c_goals, c_assists) = extract_career(blob)
        age, born, foot = extract_personal(blob)
        pt = blob.get("primaryTeam") or {}
        source_club = pt.get("teamName", "")
        if pt.get("teamId"):
            entry["team_id"] = str(pt["teamId"])
            cache[slug] = entry
            cache_dirty = True
        source_league = team_primary_league(pt.get("teamId"), source_club) \
            if source_club else ""
        cur_label, _ = current_season_label(now, season["season"])
        if season["season"] != cur_label:
            live_season = season_from_matches(
                mlist, source_league or season["league"], now)
            if live_season:
                live_season["league"] = source_league or season["league"]
                season = live_season
        # Which shirt do this season's numbers actually belong to? A loanee's
        # stats block is the loan club's football, but the roster (and the
        # profile header) show the parent club - so name the team explicitly
        # and keep the per-team splits so every spell can be shown.
        s_team, s_team_id = source_club, str(pt.get("teamId") or "")
        s_league = season["league"] or source_league
        _clean = lambda x: str(x).replace("|", "/").replace(";", ",")
        _, splits = season_splits(mlist, now, season["season"])
        splits_str = ";".join(
            "|".join([_clean(sp["team"]), sp["team_id"], _clean(sp["comp"]),
                      str(sp["apps"]), str(sp["goals"]), str(sp["assists"]),
                      str(sp["mins"]), sp["rating"]])
            for sp in splits)
        if splits and norm(splits[0]["team"]) != norm(source_club):
            top = splits[0]
            keep = norm(season["league"]) == norm(top["comp"])
            s_team, s_team_id, s_league = top["team"], top["team_id"], top["comp"]
            season = {"season": season["season"] or cur_label,
                      "league": top["comp"], "apps": top["apps"],
                      "starts": season["starts"] if keep else "",
                      "goals": top["goals"], "assists": top["assists"],
                      "mins": top["mins"],
                      "yellow": season["yellow"] if keep else "",
                      "red": season["red"] if keep else "",
                      "rating": top["rating"]}
        players_rows.append([
            slug,
            source_league,
            source_club,
            str(pt.get("teamId") or ""),
            age, born, foot,
            sr_caps, sr_goals, sr_debut, youth,
            season["season"],
            season["apps"], season["starts"], season["goals"],
            season["assists"], season["mins"], season["yellow"],
            season["red"],
            c_apps, c_goals, c_assists,
            season["rating"],
            "auto",
            s_team, s_team_id, s_league, splits_str,
        ])
        # upcoming fixtures for this player's club, next N weeks
        if source_club and pt.get("teamId"):
            for fm in team_fixtures(str(pt["teamId"]), source_club,
                                    debug=args.debug):
                if fm["finished"] or not (now <= fm["utc"] <= fixture_cut):
                    continue
                nc = norm(source_club)
                side = ("H" if nc and (nc in norm(fm["home"])
                                       or norm(fm["home"]) in nc)
                        else "A" if nc and (nc in norm(fm["away"])
                                            or norm(fm["away"]) in nc)
                        else None)
                if not side:
                    continue
                fixtures_rows.append([
                    slug, iso_z(fm["utc"]),
                    fm["away"] if side == "H" else fm["home"],
                    side, fm["comp"], fm["utc"],
                ])
                if past_cut <= fm["utc"] <= future_cut:
                    key = fm["id"]
                    if key not in matches_by_id:
                        matches_by_id[key] = ({
                            "id": key, "utc": fm["utc"], "comp": fm["comp"],
                            "home": fm["home"], "away": fm["away"],
                            "home_id": fm.get("home_id", ""),
                            "away_id": fm.get("away_id", ""),
                            "hscore": "", "ascore": "", "finished": False,
                            "ongoing": False, "minute": "",
                            "url": fm["url"],
                        }, set())
                    matches_by_id[key][1].add(slug)

        img = download_image(pid, slug, out_root / args.img_dir,
                             refresh=args.refresh_images)
        print(f"  [{i+1}/{len(todo)}] {slug}: ok "
              f"({len(mlist)} matches, img {img})")
        time.sleep(SLEEP)

    def status_of(m):
        if m["ongoing"]:
            return "live"
        if m["finished"]:
            return "ft"
        return "scheduled"

    # same fixture can arrive from a player page and a club page
    dedup = {}
    for m, slugs in matches_by_id.values():
        key = (m["utc"].strftime("%Y-%m-%d"), norm(m["home"]),
               norm(m["away"]))
        if key in dedup:
            prev, pslugs = dedup[key]
            pslugs |= slugs
            if m["finished"] or m["ongoing"]:
                dedup[key] = (m, pslugs)
        else:
            dedup[key] = (m, set(slugs))

    match_rows = []
    for m, slugs in sorted(dedup.values(), key=lambda x: x[0]["utc"]):
        st = status_of(m)
        match_rows.append([
            iso_z(m["utc"]), m["comp"], m["home"], m["away"],
            m.get("home_id", ""), m.get("away_id", ""),
            m["hscore"] if st != "scheduled" else "",
            m["ascore"] if st != "scheduled" else "",
            st,
            m["minute"] if st == "live" else "",
            ";".join(sorted(slugs)),
        ])

    results_rows.sort(key=lambda r: (r[0], r[-1]))
    fixtures_rows.sort(key=lambda r: (r[0], r[-1]))

    # THE WIPE BUG: an --active run only scrapes a handful of players, but
    # these full-file writes ran unconditionally first, clobbering everyone
    # else's rows before merge_rows "merged" into the gutted file. Every
    # matchday run was deleting ~60% of results/fixtures/players until the
    # next full refresh put them back. Partial runs must ONLY merge.
    if not getattr(args, "active", False):
        # Same guard as the match index: don't let a transient scrape miss
        # delete a live/recent match. Keep existing rows within +/-36h that
        # this run didn't produce.
        import csv as _csv
        _mcols = ["kickoff", "competition", "home", "away", "home_id",
                  "away_id", "home_score", "away_score", "status", "minute",
                  "players"]
        _mlo = now - dt.timedelta(hours=36)
        _mhi = now + dt.timedelta(hours=36)
        _newk = {(r[0][:16], r[2], r[3]) for r in match_rows}
        _mpath = out_root / "data/api/matches.csv"
        if _mpath.exists():
            with _mpath.open(encoding="utf-8") as _fh:
                for _r in _csv.DictReader(_fh):
                    _k = ((_r.get("kickoff") or "")[:16], _r.get("home") or "", _r.get("away") or "")
                    if _k in _newk:
                        continue
                    _ko = parse_iso(_r.get("kickoff"))
                    if _ko and _mlo <= _ko <= _mhi:
                        match_rows.append([_r.get(c, "") for c in _mcols])
        write_csv(_mpath, _mcols, match_rows)
        write_csv(out_root / "data/manual/results.csv",
                  ["slug", "date", "opponent", "score", "competition",
                   "minutes", "goals", "assists", "rating"],
                  [r[:-1] for r in results_rows])
        write_csv(out_root / "data/manual/fixtures.csv",
                  ["slug", "date", "opponent", "home_away", "competition"],
                  [r[:-1] for r in fixtures_rows])
        write_csv(out_root / "data/api/players.csv",
                  ["slug", "league", "club", "club_id", "age", "born", "foot", "senior_caps",
                   "senior_goals", "senior_debut", "youth", "season",
                   "s_apps", "s_starts",
                   "s_goals", "s_assists", "s_mins", "s_yellow", "s_red",
                   "c_apps", "c_goals", "c_assists", "avg_rating", "source",
                   "s_team", "s_team_id", "s_league", "s_splits"],
                  players_rows)

    if cache_dirty:
        write_id_cache(cache)

    def status_of(m):
        if m["ongoing"]:
            return "live"
        if m["finished"]:
            return "ft"
        return "scheduled"

    if getattr(args, "active", False):
        merge_rows(out_root / "data/manual/results.csv",
                   [r[:-1] for r in results_rows], todo)
        merge_rows(out_root / "data/manual/fixtures.csv",
                   [r[:-1] for r in fixtures_rows], todo)
        merge_rows(out_root / "data/api/players.csv", players_rows, todo,
                   header_hint=["slug", "league", "club", "club_id", "age",
                                "born", "foot", "senior_caps", "senior_goals",
                                "senior_debut", "youth", "season", "s_apps",
                                "s_starts", "s_goals", "s_assists", "s_mins",
                                "s_yellow", "s_red", "c_apps", "c_goals",
                                "c_assists", "avg_rating", "source", "s_team",
                                "s_team_id", "s_league", "s_splits"])
        merge_matches(out_root / "data/api/matches.csv", matches_by_id,
                      status_of)
        print("  merged active-player rows into existing CSVs")
        return

    index = []
    for m, slugs in matches_by_id.values():
        index.append({
            "fotmob_id": m["id"], "url": m["url"],
            "kickoff": iso_z(m["utc"]), "comp": m["comp"],
            "home": m["home"], "away": m["away"],
            "home_id": m.get("home_id", ""),
            "away_id": m.get("away_id", ""),
            "slugs": sorted(slugs),
        })
    # A live/recent match exists only because some tracked player's scrape
    # returned it. FotMob occasionally omits an in-progress game from a
    # player's fixture list for one request, and a full write would then
    # DELETE that match from the site mid-game. So keep any existing entry
    # within +/-36h that this run didn't produce - a transient miss must not
    # wipe a live match.
    _keep_lo = now - dt.timedelta(hours=36)
    _keep_hi = now + dt.timedelta(hours=36)
    _new_ids = {e["fotmob_id"] for e in index}
    try:
        _prev = json.loads((SCRAPER_DIR / "match_index.json").read_text())
    except Exception:
        _prev = []
    _kept = 0
    for e in _prev:
        if e.get("fotmob_id") in _new_ids:
            continue
        ko = parse_iso(e.get("kickoff"))
        if ko and _keep_lo <= ko <= _keep_hi:
            index.append(e); _kept += 1
    if _kept:
        print(f"  kept {_kept} recent match(es) this scrape did not return")
    (SCRAPER_DIR / "match_index.json").write_text(
        json.dumps(index, indent=1))
    print(f"  wrote {SCRAPER_DIR / 'match_index.json'}  "
          f"({len(index)} matches)")

    if skipped:
        print(f"\nskipped (no fotmob_id in cache): {', '.join(skipped)}")
    if failed:
        print(f"failed this run: {', '.join(failed)}")


# ---------------------------------------------------------------- events

EVENT_COLUMNS = ["match_id", "minute", "type", "player", "team", "venue"]
def match_id_for(utc, home, away):
    return f"{utc.strftime('%Y-%m-%d')}-{slugify(home)}-v-{slugify(away)}"


def parse_match_events(data, home, away):
    """(events, venue) from a match page. Events are dicts of
    minute/type/player/team."""
    def as_name(v):
        if isinstance(v, str):
            return v
        if isinstance(v, dict):
            return str(v.get("name") or v.get("stadium") or
                       v.get("longName") or "")
        return ""

    venue = ""
    for key in ("Stadium", "stadium", "venue", "stadiumName", "Venue"):
        for v in find_all(data, key):
            venue = as_name(v) or (as_name(v.get("widget"))
                                   if isinstance(v, dict) else "")
            if venue:
                break
        if venue:
            break

    type_map = {
        "goal": "goal", "penalty": "penalty", "penaltygoal": "penalty",
        "owngoal": "own_goal", "own goal": "own_goal",
        "penaltymiss": "missed_penalty", "missedpenalty": "missed_penalty",
        "yellowcard": "yellow", "yellow": "yellow",
        "redcard": "red", "red": "red",
        "secondyellow": "second_yellow", "yellowred": "second_yellow",
        "substitution": "sub", "sub": "sub", "assist": "assist",
    }
    events, seen = [], set()

    def add(ev):
        if not isinstance(ev, dict):
            return
        raw = str(ev.get("type") or ev.get("eventType") or "")
        if norm(raw) in ("card", "cards") or ev.get("card"):
            raw = str(ev.get("card") or ev.get("cardType") or raw)
        key = norm(raw).replace(" ", "")
        etype = type_map.get(key)
        if not etype:
            return
        if etype == "sub":
            # A substitution names TWO players (fotmob's `swap` pair: the one
            # coming on first, the one going off second). Emit one row each so
            # the CSV shape stays one-player-per-row.
            swap = ev.get("swap")
            if not (isinstance(swap, list) and len(swap) == 2):
                return
            def _nm(x):
                if isinstance(x, dict):
                    return str(x.get("name") or x.get("nameStr") or "").strip()
                return str(x or "").strip()
            on_name, off_name = _nm(swap[0]), _nm(swap[1])
            minute = ev.get("time") or ev.get("minute") or ev.get("timeStr") or ""
            if isinstance(minute, dict):
                minute = minute.get("value") or minute.get("short") or ""
            minute = re.sub(r"[^0-9+]", "", str(minute))
            side = ev.get("isHome")
            team = (home if side is True else away if side is False
                    else str(ev.get("teamName") or ""))
            for nm, st in ((on_name, "sub_on"), (off_name, "sub_off")):
                if not nm:
                    continue
                sig = (minute, st, norm(nm), norm(team))
                if sig in seen:
                    continue
                seen.add(sig)
                events.append({"minute": minute, "type": st,
                               "player": nm, "team": team})
            return
        if etype == "goal":
            if ev.get("isPenaltyShootoutEvent"):
                return
            if ev.get("ownGoal") or ev.get("isOwnGoal"):
                etype = "own_goal"
            elif ev.get("penalty") or ev.get("isPenalty"):
                etype = "penalty"
        player = (ev.get("nameStr") or ev.get("player")
                  or ev.get("playerName") or ev.get("fullName") or "")
        if isinstance(player, dict):
            player = player.get("name") or player.get("nameStr") or ""
        minute = ev.get("time") or ev.get("minute") or ev.get("timeStr") or ""
        if isinstance(minute, dict):
            minute = minute.get("value") or minute.get("short") or ""
        minute = re.sub(r"[^0-9+]", "", str(minute))
        side = ev.get("isHome")
        team = (home if side is True else away if side is False
                else str(ev.get("teamName") or ""))
        sig = (minute, etype, norm(str(player)), norm(team))
        if not player or sig in seen:
            return
        seen.add(sig)
        events.append({"minute": minute, "type": etype,
                       "player": str(player).strip(), "team": team})

    for key in ("events", "keyEvents", "matchEvents", "eventList"):
        for hit in find_all(data, key):
            items = hit if isinstance(hit, list) else [hit]
            for it in items:
                if isinstance(it, dict):
                    add(it)
                    for v in it.values():
                        if isinstance(v, list):
                            for sub_ev in v:
                                add(sub_ev)

    events.sort(key=lambda e: (int(re.sub(r"\D", "", e["minute"]) or 0),
                               e["type"]))
    return events, str(venue or "")


# Finished matches and upcoming ones now share a shape: data/api/lineups.csv is
# a rolling window rewritten every run, data/api/match_lineups.csv is the
# durable archive of games already played. Same columns, same parser.
LINEUP_CSV_COLUMNS = ["match_id", "side", "team", "formation", "status",
                      "updated", "role", "number", "name", "slug", "pos",
                      "x", "y"]


def migrate_lineup_row(row):
    """Read a match_lineups.csv row in either shape. The old one was
    match_id,team,player,player_id,role,shirt,position - no side, no formation,
    no coordinates, and an empty position column on every row it ever wrote."""
    if "name" in row or "side" in row:
        return [row.get(c, "") for c in LINEUP_CSV_COLUMNS]
    return [row.get("match_id", ""), "", row.get("team", ""), "", "", "",
            row.get("role", ""), row.get("shirt", ""), row.get("player", ""),
            "", row.get("position", ""), "", ""]


CLUB_IDS_CSV = "data/api/club_ids.csv"
_PARENT_RE = re.compile(r"\s+(u\d{2}|academy|reserves?|ii|b)$", re.I)


def team_candidates(term, debug_name=None):
    """[(id, name)] team suggestions from fotmob search."""
    data = get_json(SEARCH_URL.format(term=requests.utils.quote(term)),
                    debug_name=debug_name)
    out, seen = [], set()

    def walk(obj):
        if isinstance(obj, dict):
            t = str(obj.get("type", "")).lower()
            oid = obj.get("id") or obj.get("teamId")
            name = obj.get("name") or ""
            if oid and name and "team" in t:
                if str(oid) not in seen:
                    seen.add(str(oid))
                    out.append((str(oid), str(name)))
            text = str(obj.get("text", ""))
            payload = obj.get("payload") or {}
            if "|" in text and isinstance(payload, dict) \
                    and str(payload.get("type", "")).lower() == "team":
                nm, _, oid2 = text.rpartition("|")
                if oid2.isdigit() and oid2 not in seen:
                    seen.add(oid2)
                    out.append((oid2, nm))
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(data)
    return out


NON_CLUBS = {"without club", "retired", "career break", "unknown",
             "no club", "free agent", "---", "-", "?", ""}

# Transfermarkt truncates club names to fit its column - "Cambridge Utd.",
# "Sheff Wed", "Huddersf. U21", "Weston-super-M.". Fotmob search does far
# better on the long form, so every miss gets a second go with this.
_TM_ABBR = {"utd": "United", "utd.": "United", "ath.": "Athletic",
            "ath": "Athletic", "wed": "Wednesday", "wed.": "Wednesday",
            "nott'm": "Nottingham", "sheff": "Sheffield", "sheff.": "Sheffield",
            "huddersf.": "Huddersfield", "southampt.": "Southampton",
            "rgrs": "Rangers", "rvrs": "Rovers", "acad": "Academy",
            "twn": "Town", "cty": "City", "rov.": "Rovers", "utd..": "United"}


def expand_club(name):
    """The long form of an abbreviated club name, or "" if nothing changed.
       A bare initial ("C. Budejovice", "Abu Dhabi Wolf.") is dropped rather
       than guessed at - the rest of the name is usually enough to search."""
    out = []
    for w in str(name or "").split():
        k = w.lower()
        if k in _TM_ABBR:
            out.append(_TM_ABBR[k])
        elif len(w) <= 2 and w.endswith("."):
            continue                       # "C.", "A." - initials, not words
        elif w.endswith(".") and len(w) > 2:
            out.append(w[:-1])             # "Wolf." -> "Wolf", "Rich." -> "Rich"
        else:
            out.append(w)
    s = " ".join(out).strip()
    return s if s and s.lower() != str(name or "").strip().lower() else ""


def badges_cmd(args):
    """Keep data/api/club_ids.csv - a durable club name -> fotmob id map, so
    every club the site ever shows has a badge. Names are harvested for free
    wherever a name and an id already travel together; whatever is left is
    looked up once on fotmob search, clubs playing in the next week first.
    A name resolved once is never looked up again."""
    out_root = Path(args.out)
    out_path = out_root / CLUB_IDS_CSV
    COLS = ["club", "club_id", "source", "checked"]
    known = {}
    if out_path.exists():
        with open(out_path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if (r.get("club") or "").strip():
                    known[r["club"].strip()] = [r.get(c, "") or "" for c in COLS]
    stamp = iso_z(dt.datetime.now(dt.timezone.utc))

    def put(name, cid, source):
        name = (name or "").strip()
        if not name:
            return
        cur = known.get(name)
        if cur and cur[1]:                       # resolved once = kept forever
            return
        known[name] = [name, str(cid or "").strip(), source, stamp]

    # 1. free harvest
    for rel, pairs in (("data/api/matches.csv",
                        (("home", "home_id"), ("away", "away_id"))),
                       ("data/api/players.csv", (("club", "club_id"),)),
                       ("data/manual/clubs.csv", (("club", "club_id"),))):
        f = out_root / rel
        if not f.exists():
            continue
        with open(f, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                for nk, ik in pairs:
                    if (r.get(nk) or "").strip() and (r.get(ik) or "").strip():
                        put(r[nk], r[ik], "harvest")
    idx_path = SCRAPER_DIR / "match_index.json"
    if idx_path.exists():
        for m in json.loads(idx_path.read_text()):
            for nk, ik in (("home", "home_id"), ("away", "away_id")):
                if m.get(nk) and m.get(ik):
                    put(m[nk], m[ik], "harvest")

    def resolved(name):
        row = known.get(name)
        if row and row[1]:
            return True
        parent = _PARENT_RE.sub("", name)
        row = known.get(parent)
        return bool(row and row[1])

    # 2. who still needs one - and who is playing this coming week
    now = dt.datetime.now(dt.timezone.utc)
    week = now + dt.timedelta(days=7)
    soon, everyone = set(), set()
    f = out_root / "data/api/matches.csv"
    if f.exists():
        with open(f, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                ko = parse_iso(r.get("kickoff") or "")
                for k in ("home", "away"):
                    n = (r.get(k) or "").strip()
                    if not n:
                        continue
                    everyone.add(n)
                    if ko and now <= ko <= week:
                        soon.add(n)
    for rel, datecol in (("data/manual/results.csv", "date"),
                         ("data/manual/fixtures.csv", "date")):
        f = out_root / rel
        if not f.exists():
            continue
        with open(f, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                n = (r.get("opponent") or "").strip()
                if not n:
                    continue
                everyone.add(n)
                d = (r.get(datecol) or "")[:10]
                if str(now.date()) <= d <= str(week.date()):
                    soon.add(n)

    # every club a tracked player has ever moved to or from - the transfers
    # page shows all of them, so all of them want a crest
    f = out_root / "data/api/transfers.csv"
    if f.exists():
        with open(f, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                for k in ("from_club", "to_club"):
                    n = (r.get(k) or "").strip()
                    if n and n.lower() not in NON_CLUBS:
                        everyone.add(n)

    everyone = {n for n in everyone if n.lower() not in NON_CLUBS}
    soon = {n for n in soon if n.lower() not in NON_CLUBS}

    def wants_lookup(name):
        if resolved(name):
            return False
        row = known.get(name)
        if row is not None and not row[1]:       # tried before and missed
            return name in soon                  # only nag for imminent games
        return True

    todo = ([n for n in sorted(soon) if wants_lookup(n)]
            + [n for n in sorted(everyone - soon) if wants_lookup(n)])
    limit = getattr(args, "limit", 150)
    if limit and len(todo) > limit:
        print(f"badges: {len(todo)} clubs to look up, doing {limit} this run")
        todo = todo[:limit]
    else:
        print(f"badges: {len(todo)} clubs to look up")

    def simplify(n):
        drop = {"fc", "cf", "afc", "cd", "sc", "ac", "if", "fk", "sk",
                "bk", "sv", "club", "de", "cfc"}
        return " ".join(w for w in norm(n).split() if w not in drop)

    def pick(cands, want):
        """One id, or "" - never a guess. An exact name match wins; failing
        that, one candidate that reduces to the same words as what we asked
        for. Two plausible teams means we do not know which, so no badge."""
        if not want:
            return ""
        exact = [c for c in cands if norm(c[1]) == norm(want)]
        if exact:
            return exact[0][0]
        key = simplify(want)
        loose = [c for c in cands if key and simplify(c[1]) == key]
        return loose[0][0] if len(loose) == 1 else ""

    found = 0
    for i, name in enumerate(todo):
        hit, tried = "", []
        for term in [name] + ([expand_club(name)] if expand_club(name) else []):
            tried.append(term)
            try:
                cands = team_candidates(
                    term, debug_name=f"badge_{norm(term)[:30].replace(' ', '_')}"
                    if args.debug else None)
            except Exception as e:
                print(f"  [{i+1}/{len(todo)}] {name}: search failed ({e})")
                time.sleep(SLEEP)
                continue
            hit = pick(cands, name) or pick(cands, term)
            time.sleep(SLEEP)
            if hit:
                break
        put(name, hit, "search")
        if hit:
            found += 1
            print(f"  [{i+1}/{len(todo)}] {name} -> {hit}")
        else:
            extra = f" (also tried '{tried[-1]}')" if len(tried) > 1 else ""
            print(f"  [{i+1}/{len(todo)}] {name}: no confident match{extra}")

    write_csv(out_path, COLS, sorted(known.values(), key=lambda r: r[0]))
    have_ids = sum(1 for r in known.values() if r[1])
    print(f"badges: {found} new this run, {have_ids}/{len(known)} names mapped")


def events_cmd(args):
    idx_path = SCRAPER_DIR / "match_index.json"
    if not idx_path.exists():
        sys.exit("no match_index.json - run `scrape` first.")
    index = json.loads(idx_path.read_text())
    now = dt.datetime.now(dt.timezone.utc)
    since = now - dt.timedelta(days=args.days)
    stamp = iso_z(now)
    by_id, by_name = slug_lookup()

    out_path = Path(args.out) / "data/api/match_events.csv"
    lineup_path = Path(args.out) / "data/api/match_lineups.csv"
    have = set()
    rows, lineup_rows = [], []
    if lineup_path.exists() and not args.rebuild:
        with open(lineup_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                lineup_rows.append(migrate_lineup_row(row))
    have_subs = set()
    if out_path.exists() and not args.rebuild:
        with open(out_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                rows.append([row.get(c, "") for c in EVENT_COLUMNS])
                have.add(row["match_id"])
                if str(row.get("type", "")).startswith("sub_"):
                    have_subs.add(row["match_id"])

    todo = []
    for m in index:
        ko = parse_iso(m["kickoff"])
        if not ko or not (since <= ko <= now):
            continue
        mid = match_id_for(ko, m["home"], m["away"])
        if not m.get("url"):
            continue
        # a match scraped before substitutions existed here has events but no
        # sub rows - rescrape it (it is inside the days window anyway) so the
        # subs get backfilled; matches that already have them are settled
        if mid in have and mid in have_subs:
            continue
        todo.append((mid, m))
    redo = {mid for mid, _ in todo}
    rows = [r for r in rows if r[0] not in redo]
    print(f"{len(todo)} finished matches to read")

    done = 0
    for i, (mid, m) in enumerate(todo):
        try:
            data = match_page(
                m["url"], debug_name=f"match_{mid}" if args.debug else None)
            if data is None:
                print(f"  [{i+1}/{len(todo)}] {mid}: skipped, wrong leg")
                time.sleep(SLEEP)
                continue
            evs, venue = parse_match_events(data, m["home"], m["away"])
            sides = parse_match_lineup(data, m["home"], m["away"])
        except Exception as e:
            print(f"  [{i+1}/{len(todo)}] {mid}: failed ({e})")
            time.sleep(SLEEP)
            continue
        for e in evs:
            rows.append([mid, e["minute"], e["type"], e["player"],
                         e["team"], venue])
        if not evs:
            rows.append([mid, "", "", "", "", venue])
        lus = lineup_rows_for(mid, sides, by_id, by_name,
                              m["home"], m["away"], stamp)
        lineup_rows.extend(lus)
        done += 1
        print(f"  [{i+1}/{len(todo)}] {mid}: {len(evs)} events, "
              f"{len(lus)} in lineups"
              + (f", {venue}" if venue else ""))
        time.sleep(SLEEP)

    rows.sort(key=lambda r: (r[0], int(re.sub(r"\D", "", r[1]) or 0)))
    write_csv(out_path, EVENT_COLUMNS, rows)
    lineup_rows.sort(key=lambda r: (r[0], r[1], r[6], r[8]))
    write_csv(lineup_path, LINEUP_CSV_COLUMNS, lineup_rows)
    print(f"{done} matches read this run")
    if done and not any(r[8] for r in lineup_rows):
        print("no lineups found - rerun with --debug and send a file "
              "from scraper/debug/ if you want these")


# ---------------------------------------------------------------- lineups
LAST_XI_FILE = SCRAPER_DIR / "last_xi.json"


def _player_entry(p, role):
    if not isinstance(p, dict):
        return None
    name = p.get("name") or p.get("nameStr") or p.get("fullName")
    if isinstance(name, dict):
        name = (name.get("fullName") or
                " ".join(x for x in (name.get("firstName"),
                                     name.get("lastName")) if x))
    if not name:
        return None
    if looks_like_staff(p.get("role"), p.get("title"),
                        p.get("positionStringShort")):
        return None
    pos = (p.get("positionStringShort") or p.get("position")
           or p.get("role") or "")
    if isinstance(pos, dict):
        pos = pos.get("key") or pos.get("label") or ""
    x = y = ""
    for key in ("horizontalLayout", "verticalLayout", "pitchPosition",
                "position"):
        v = p.get(key)
        if isinstance(v, dict) and ("x" in v or "y" in v):
            x, y = v.get("x", ""), v.get("y", "")
            break
    if x == "" and isinstance(p.get("x"), (int, float)):
        x, y = p.get("x", ""), p.get("y", "")
    return {
        "name": str(name).strip(),
        "id": str(p.get("id") or p.get("playerId") or ""),
        "number": str(p.get("shirt") or p.get("shirtNumber") or ""),
        "pos": str(pos), "x": str(x), "y": str(y), "role": role,
    }


def _side_block(block):
    """(formation, [players]) for one side, starters in formation order."""
    if not isinstance(block, dict):
        return "", []
    formation = str(block.get("formation") or block.get("lineupFormation")
                    or "")
    players = []
    for key in ("starters", "startingLineup", "players", "lineup"):
        val = block.get(key)
        if not isinstance(val, list):
            continue
        for item in val:
            if isinstance(item, list):        # rows: keeper first
                for p in item:
                    e = _player_entry(p, "start")
                    if e:
                        players.append(e)
            else:
                e = _player_entry(item, "start")
                if e:
                    players.append(e)
        if players:
            break
    for key in ("bench", "subs", "substitutes"):
        val = block.get(key)
        if isinstance(val, list):
            for p in val:
                e = _player_entry(p, "bench")
                if e:
                    players.append(e)
    return formation, players


def parse_match_lineup(data, home, away):
    """{'home': (formation, players, confirmed), 'away': (...)} or {}."""
    out = {}
    confirmed_flag = None
    for key in ("isLineupConfirmed", "lineupConfirmed", "confirmed"):
        v = first(find_all(data, key))
        if isinstance(v, bool):
            confirmed_flag = v
            break
    predicted_flag = bool(first(find_all(data, "isPredictedLineup")) or
                          first(find_all(data, "predictedLineup")))

    for lu in find_all(data, "lineup"):
        blocks = lu if isinstance(lu, list) else [lu]
        for b in blocks:
            if not isinstance(b, dict):
                continue
            for side_key, side in (("homeTeam", "home"), ("home", "home"),
                                   ("awayTeam", "away"), ("away", "away")):
                blk = b.get(side_key)
                if isinstance(blk, dict):
                    f, ps = _side_block(blk)
                    if ps and side not in out:
                        out[side] = (f, ps)
            name = b.get("teamName") or b.get("name")
            if name:
                f, ps = _side_block(b)
                if ps:
                    side = ("home" if norm(str(name)) == norm(home)
                            else "away" if norm(str(name)) == norm(away)
                            else None)
                    if side and side not in out:
                        out[side] = (f, ps)
    if not out:
        return {}
    # An unknown flag must NOT be labelled confirmed: pre-match scrapes were
    # getting stamped CONFIRMED and then shown as gospel. Only say confirmed
    # when the page says so; otherwise admit it's just a lineup.
    status = ("confirmed" if confirmed_flag
              else "predicted" if (predicted_flag or confirmed_flag is False)
              else "")
    return {side: (f, ps, status) for side, (f, ps) in out.items()}


def load_last_xi():
    if LAST_XI_FILE.exists():
        return json.loads(LAST_XI_FILE.read_text())
    return {}


def slug_lookup():
    """(by_source_id, by_name) so a lineup row can be tied to a player page.
    Id first - names on the source don't always match the roster."""
    by_id, by_name = {}, {}
    for p in read_player_list():
        by_name[norm(p["name"])] = p["slug"]
    for slug, e in read_id_cache().items():
        if e.get("fotmob_id"):
            by_id[str(e["fotmob_id"])] = slug
    return by_id, by_name


def lineup_rows_for(mid, sides, by_id, by_name, home, away, stamp):
    """Flatten parse_match_lineup output into LINEUP_CSV_COLUMNS rows."""
    out = []
    for side, team_name in (("home", home), ("away", away)):
        got = sides.get(side)
        if not got:
            continue
        formation, players, status = got
        for p in players:
            slug = by_id.get(p["id"]) or by_name.get(norm(p["name"]), "")
            out.append([mid, side, team_name, formation, status, stamp,
                        p["role"], p["number"], p["name"], slug,
                        p["pos"], p["x"], p["y"]])
    return out


def lineups_cmd(args):
    idx_path = SCRAPER_DIR / "match_index.json"
    if not idx_path.exists():
        sys.exit("no match_index.json - run `scrape` first.")
    index = json.loads(idx_path.read_text())
    now = dt.datetime.now(dt.timezone.utc)
    start = now - dt.timedelta(hours=args.past_hours)
    end = now + dt.timedelta(hours=args.ahead_hours)

    by_id, by_name = slug_lookup()
    last_xi = load_last_xi()
    rows, seen_matches = [], 0
    stamp = iso_z(now)

    for m in sorted(index, key=lambda x: x["kickoff"]):
        ko = parse_iso(m["kickoff"])
        if not ko or not (start <= ko <= end) or not m.get("url"):
            continue
        seen_matches += 1
        mid = match_id_for(ko, m["home"], m["away"])
        try:
            data = match_page(
                m["url"], debug_name=f"lineup_{mid}" if args.debug else None)
            if data is None:
                print(f"  {mid}: skipped, wrong leg")
                time.sleep(SLEEP)
                continue
            sides = parse_match_lineup(data, m["home"], m["away"])
        except Exception as e:
            print(f"  {mid}: failed ({e})")
            time.sleep(SLEEP)
            continue

        for side, team_name, team_id in (("home", m["home"],
                                          m.get("home_id", "")),
                                         ("away", m["away"],
                                          m.get("away_id", ""))):
            key = team_id or norm(team_name)
            got = sides.get(side)
            if got:
                formation, players, status = got
                if status == "confirmed":
                    last_xi[key] = {"formation": formation,
                                    "players": players, "when": stamp}
            elif key in last_xi:            # fall back to their last XI
                prev = last_xi[key]
                formation = prev.get("formation", "")
                players, status = prev["players"], "last"
            else:
                continue
            for p in players:
                slug = by_id.get(p["id"]) or by_name.get(norm(p["name"]), "")
                rows.append([mid, side, team_name, formation, status, stamp,
                             p["role"], p["number"], p["name"], slug,
                             p["pos"], p["x"], p["y"]])
            print(f"  {mid} {side}: {status} {formation or '(no formation)'}"
                  f" - {sum(1 for p in players if p['role'] == 'start')}"
                  f" starters, {sum(1 for p in players if p['role'] == 'bench')}"
                  f" bench")
        time.sleep(SLEEP)

    LAST_XI_FILE.write_text(json.dumps(last_xi))
    write_csv(Path(args.out) / "data/api/lineups.csv",
              LINEUP_CSV_COLUMNS, rows)
    print(f"{seen_matches} matches in the window "
          f"(-{args.past_hours}h to +{args.ahead_hours}h)")
    if seen_matches and not rows:
        print("no lineups found at all - rerun with --debug and send a "
              "file from scraper/debug/ so the parser can be fixed")


# ---------------------------------------------------------------- live

LIVE_WINDOW_H = 3


def parse_match_page(data):
    """Hunt the match page JSON for teams+status. Returns
    (hs, as_, status, minute) with None scores when unknown."""
    def hunt(obj):
        if isinstance(obj, dict):
            teams = obj.get("teams")
            status = obj.get("status")
            if (isinstance(teams, list) and len(teams) == 2
                    and all(isinstance(t, dict) and "name" in t
                            for t in teams)
                    and isinstance(status, dict)):
                return teams, status
            for v in obj.values():
                r = hunt(v)
                if r:
                    return r
        elif isinstance(obj, list):
            for i in obj:
                r = hunt(i)
                if r:
                    return r
        return None

    hit = hunt(data)
    if not hit:
        return None
    teams, status = hit
    hs, as_ = teams[0].get("score"), teams[1].get("score")
    if hs is None or as_ is None:
        ss = str(status.get("scoreStr") or "")
        m = re.match(r"\s*(\d+)\s*-\s*(\d+)", ss)
        if m:
            hs, as_ = int(m.group(1)), int(m.group(2))
    finished = bool(status.get("finished"))
    started = bool(status.get("started"))
    st = "ft" if finished else ("live" if started else "scheduled")
    minute = ""
    lt = status.get("liveTime") or {}
    if isinstance(lt, dict):
        raw = str(lt.get("short") or lt.get("long") or "")
        # FotMob shows "HT" (and sometimes "Half Time") at the break. Keep it as
        # a real value rather than stripping it to an empty string, so the site
        # can say "HT" instead of a blank live badge.
        if "ht" in raw.lower() or "half" in raw.lower():
            minute = "HT"
        else:
            minute = re.sub(r"[^0-9+]", "", raw)
    # FotMob keeps started=true through the interval, so an "HT" minute is still
    # a live match — don't blank it out.
    if st != "live":
        minute = ""
    if st == "scheduled":
        hs = as_ = None
    return hs, as_, st, minute


def live(args):
    idx_path = SCRAPER_DIR / "match_index.json"
    if not idx_path.exists():
        sys.exit("No match_index.json - run `scrape` first.")
    index = json.loads(idx_path.read_text())

    plist = {p["slug"]: p for p in read_player_list()}
    now = dt.datetime.now(dt.timezone.utc)
    win = dt.timedelta(hours=LIVE_WINDOW_H)

    out = []
    for m in index:
        ko = parse_iso(m["kickoff"])
        if not ko or abs(ko - now) > win:
            continue
        hs = as_ = None
        st, minute = "scheduled", ""
        if m.get("url"):
            try:
                page = match_page(m["url"])
                parsed = parse_match_page(page) if page else None
                if parsed:
                    hs, as_, st, minute = parsed
            except Exception as e:
                print(f"  fetch failed for {m['home']} v {m['away']}: {e}")
                parsed = None
        else:
            parsed = None
        if parsed is None:
            # time-based guess when the page can't be read
            if now >= ko + dt.timedelta(hours=2, minutes=10):
                st = "ft"
            elif now >= ko:
                st = "live"

        players = []
        for slug in m["slugs"]:
            p = plist.get(slug, {})
            name = p.get("name", slug.replace("-", " ").title())
            ini = "".join(w[0] for w in name.split()[:2]).upper()
            players.append({"slug": slug, "n": name,
                            "club": p.get("club", ""),
                            "ini": ini, "pos": p.get("pos", "")})

        out.append({
            "id": (f"{ko.strftime('%Y-%m-%d')}-"
                   f"{slugify(m['home'])}-v-{slugify(m['away'])}"),
            "kickoff": iso_z(ko),
            "comp": m["comp"],
            "home": m["home"], "away": m["away"],
            "hs": hs, "as_": as_,
            "status": st, "minute": minute,
            "players": players,
        })
        time.sleep(SLEEP)

    out.sort(key=lambda x: x["kickoff"])
    payload = {"updated": iso_z(now), "matches": out}
    dest = Path(args.out) / "live.json"
    dest.write_text(json.dumps(payload, separators=(",", ":"),
                               ensure_ascii=False))
    print(f"wrote {dest}  ({len(out)} matches in window)")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("resolve", help="find FotMob IDs for players")
    r.add_argument("--force", action="store_true",
                   help="re-resolve even cached players")
    r.add_argument("--debug", action="store_true",
                   help="dump raw search JSON to scraper/debug/")

    s = sub.add_parser("scrape", help="write the 4 CSVs")
    s.add_argument("--out", default=".",
                   help="project root (default: current dir)")
    s.add_argument("--only", default="",
                   help="comma-separated slugs to scrape (for testing)")
    s.add_argument("--debug", action="store_true",
                   help="dump raw page JSON to scraper/debug/")
    s.add_argument("--img-dir", default="img/players",
                   help="where player images go, relative to --out "
                        "(default: img/players)")
    s.add_argument("--refresh-images", action="store_true",
                   help="re-download images even if present")
    s.add_argument("--active", action="store_true",
                   help="only players with a match within 36h; merges "
                        "into the existing CSVs (fast, for matchdays)")
    s.add_argument("--fixture-weeks", type=int, default=6,
                   help="how many weeks of upcoming fixtures (default 6)")

    cl = sub.add_parser("clubs", help="write data/manual/clubs.csv")
    cl.add_argument("--out", default=".")
    cl.add_argument("--missing-only", action="store_true",
                    help="only look up clubs that still have no coordinates")
    cl.add_argument("--debug", action="store_true")

    ie = sub.add_parser("ireland",
                        help="write data/manual/ireland.csv (senior + U21)")
    ie.add_argument("--out", default=".")
    ie.add_argument("--debug", action="store_true")

    d = sub.add_parser("discover-loi",
                       help="add all Irish players from LOI Premier + "
                            "First Division squads")
    d.add_argument("--league-id", default="",
                   help="fotmob league id(s), comma-separated "
                        "(auto-detected if omitted)")
    d.add_argument("--debug", action="store_true")

    ad = sub.add_parser("add", help="add players by source id or URL")
    ad.add_argument("ids", nargs="+",
                    help="player ids or page URLs")

    al = sub.add_parser("add-list",
                        help="bulk-add players from a transfer list file")
    al.add_argument("file", nargs="?", default="scraper/transfers.txt",
                    help="path to the list (default scraper/transfers.txt)")
    al.add_argument("--no-stubs", dest="stub_unmatched",
                    action="store_false",
                    help="don't add players the source doesn't cover")
    al.add_argument("--stub-only", action="store_true",
                    help="add every name as a stub, match nothing")

    dd = sub.add_parser("dedupe",
                        help="remove roster entries sharing a source id")
    dd.add_argument("--dry-run", action="store_true",
                    help="just list them")

    ci = sub.add_parser("clear-id",
                        help="blank a wrong source id so it re-resolves")
    ci.add_argument("slugs", nargs="+")

    si = sub.add_parser("set-id",
                        help="fill a source id on an existing player "
                             "(when the source spells the name differently)")
    si.add_argument("pairs", nargs="+", help="<slug> <id> [<slug> <id> ...]")

    di = sub.add_parser("discover-ireland",
                        help="add everyone in Ireland senior/U21/U20/"
                             "U19/U17 squads")
    di.add_argument("--debug", action="store_true")

    bd = sub.add_parser("badges",
                        help="maintain data/api/club_ids.csv so every club "
                             "shown on the site has a badge")
    bd.add_argument("--out", default=".")
    bd.add_argument("--limit", type=int, default=150,
                    help="max search lookups per run (default 150)")
    bd.add_argument("--debug", action="store_true")

    ev = sub.add_parser("events",
                        help="write data/api/match_events.csv "
                             "(scorers, cards, venue)")
    ev.add_argument("--out", default=".")
    ev.add_argument("--days", type=int, default=7,
                    help="how far back to read matches (default 7)")
    ev.add_argument("--rebuild", action="store_true")
    ev.add_argument("--debug", action="store_true")

    ln = sub.add_parser("lineups",
                        help="write data/api/lineups.csv for upcoming "
                             "and in-progress matches")
    ln.add_argument("--out", default=".")
    ln.add_argument("--ahead-hours", type=float, default=36,
                    help="how far ahead to look (default 36)")
    ln.add_argument("--past-hours", type=float, default=6,
                    help="how far back to look (default 6)")
    ln.add_argument("--debug", action="store_true")

    tb = sub.add_parser("tables", help="league tables for every league "
                        "our players sit in -> data/api/tables.csv")

    lv = sub.add_parser("live", help="write live.json for matches "
                                     "within +-3h")
    lv.add_argument("--out", default=".")

    a = sub.add_parser("all", help="resolve missing then scrape")
    a.add_argument("--out", default=".")
    a.add_argument("--debug", action="store_true")
    a.add_argument("--img-dir", default="img/players")
    a.add_argument("--refresh-images", action="store_true")
    a.add_argument("--active", action="store_false")
    a.add_argument("--fixture-weeks", type=int, default=6)

    args = ap.parse_args()
    if args.cmd == "resolve":
        resolve(args)
    elif args.cmd == "discover-loi":
        discover_loi(args)
    elif args.cmd == "live":
        live(args)
    elif args.cmd == "add":
        add_players(args)
    elif args.cmd == "add-list":
        add_list(args)
    elif args.cmd == "set-id":
        set_id(args)
    elif args.cmd == "dedupe":
        dedupe(args)
    elif args.cmd == "clear-id":
        clear_id(args)
    elif args.cmd == "discover-ireland":
        discover_ireland(args)
    elif args.cmd == "badges":
        badges_cmd(args)
    elif args.cmd == "events":
        events_cmd(args)
    elif args.cmd == "lineups":
        lineups_cmd(args)
    elif args.cmd == "tables":
        cmd_tables(args)
    elif args.cmd == "clubs":
        clubs_file(args)
    elif args.cmd == "ireland":
        ireland_file(args)
    elif args.cmd == "scrape":
        scrape(args)
    else:
        args.force = False
        args.only = ""
        resolve(args)
        scrape(args)


if __name__ == "__main__":
    main()
