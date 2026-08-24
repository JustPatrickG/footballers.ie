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
    r = SESSION.get(url, timeout=25)
    r.raise_for_status()
    data = r.json()
    if debug_name:
        DEBUG_DIR.mkdir(exist_ok=True)
        (DEBUG_DIR / f"{debug_name}.json").write_text(
            json.dumps(data, indent=1)[:2_000_000])
    return data


def get_next_data(url, debug_name=None):
    """Fetch a fotmob page and return the embedded __NEXT_DATA__ JSON."""
    r = SESSION.get(url, timeout=25)
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


def resolve(args):
    players = read_player_list()
    cache = read_id_cache()
    todo = [p for p in players
            if args.force or not cache.get(p["slug"], {}).get("fotmob_id")]
    print(f"resolving {len(todo)} of {len(players)} players "
          f"({len(players) - len(todo)} already cached)")

    unresolved = []
    for i, p in enumerate(todo):
        slug, name, club = p["slug"], p["name"], p["club"]
        try:
            cands = search_candidates(
                name, debug_name=f"search_{slug}" if args.debug else None)
        except Exception as e:
            print(f"  [{i+1}/{len(todo)}] {name}: search failed ({e})")
            unresolved.append(slug)
            cache[slug] = {"fotmob_id": "", "note": f"search error: {e}"}
            time.sleep(SLEEP)
            continue

        nclub = norm(club)
        nname = norm(name)
        nsurname = nname.split()[-1] if nname else ""
        name_hits = []
        for oid, cname, cteam in cands:
            nc = norm(cname)
            if nc == nname or nname in nc or nc in nname or \
                    (nsurname and nsurname in nc.split()):
                name_hits.append((oid, cname, cteam))

        best = None
        pool = name_hits or cands
        if len(pool) == 1:
            # only one option -> obviously them
            oid, cname, cteam = pool[0]
            best = (2, oid, cname, cteam)
        else:
            for oid, cname, cteam in name_hits:
                club_match = (nclub and norm(cteam) and
                              (nclub in norm(cteam) or norm(cteam) in nclub))
                score = 2 if club_match else 1
                if best is None or score > best[0]:
                    best = (score, oid, cname, cteam)

        if best:
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


def team_irish_players(tid, tname="", debug=False):
    url = TEAM_URL.format(tid=tid, tslug=slugify(tname or "team"))
    try:
        data = get_next_data(url, debug_name=f"team_{tid}" if debug else None)
    except Exception:
        time.sleep(2)  # one retry - fotmob 500s are often transient
        data = get_next_data(url, debug_name=f"team_{tid}" if debug else None)
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
        out.append({
            "id": str(m.get("id") or utc),
            "utc": utc,
            "comp": m.get("leagueName", "") or "",
            "home": team if is_home else opp,
            "away": opp if is_home else team,
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
                "utc": utc,
                "comp": nm.get("leagueName", "") or "",
                "home": home, "away": away,
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
    ml = first(find_all(blob, "mainLeague"))
    if isinstance(ml, dict):
        stats["league"] = ml.get("leagueName", "") or ""
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


def extract_personal(blob):
    """age, born(blank - fotmob lacks town), foot."""
    age = foot = ""
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
            if "age" in title and age == "" and val:
                m = re.search(r"\d+", str(val))
                age = m.group(0) if m else ""
            if "foot" in title and foot == "" and val:
                foot = str(val).strip().capitalize()
    if not age:
        bd = first(find_all(blob, "birthDate"))
        if isinstance(bd, dict):
            bd = bd.get("utcTime")
        d = parse_iso(bd) if isinstance(bd, str) else None
        if d:
            today = dt.datetime.now(dt.timezone.utc)
            age = str(today.year - d.year -
                      ((today.month, today.day) < (d.month, d.day)))
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


def scrape(args):
    players = read_player_list()
    cache = read_id_cache()
    if not cache:
        sys.exit("No fotmob_ids.csv - run `resolve` first.")

    out_root = Path(args.out)
    now = dt.datetime.now(dt.timezone.utc)
    past_cut = now - dt.timedelta(days=MATCH_PAST_DAYS)
    future_cut = now + dt.timedelta(days=MATCH_FUTURE_DAYS)

    matches_by_id = {}   # match id -> (match dict, set(slugs))
    results_rows = []
    fixtures_rows = []
    players_rows = []
    skipped, failed = [], []

    todo = players
    if args.only:
        todo = [p for p in players if p["slug"] in set(args.only.split(","))]

    for i, p in enumerate(todo):
        slug, club = p["slug"], p["club"]
        entry = cache.get(slug, {})
        pid = entry.get("fotmob_id")
        if not pid:
            skipped.append(slug)
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
                    m["utc"].strftime("%d %b"),
                    m["opponent"],
                    f"{own}-{opp}" if own != "" and opp != "" else "",
                    m["comp"],
                    int(m["minutes"] or 0),
                    int(m["goals"] or 0),
                    int(m["assists"] or 0),
                    m["utc"],
                ])
            elif not m["finished"] and not m["ongoing"] and side \
                    and m["utc"] >= now:
                fixtures_rows.append([
                    slug, m["utc"].strftime("%d %b"), m["opponent"], side,
                    m["comp"], m["utc"],
                ])

        # players.csv row
        season = extract_season_stats(blob)
        (sr_caps, sr_goals, sr_debut, youth,
         c_apps, c_goals, c_assists) = extract_career(blob)
        age, born, foot = extract_personal(blob)
        players_rows.append([
            slug,
            season["league"] or p.get("league", ""),
            age, born, foot,
            sr_caps, sr_goals, sr_debut, youth,
            season["apps"], season["starts"], season["goals"],
            season["assists"], season["mins"], season["yellow"],
            season["red"],
            c_apps, c_goals, c_assists,
            season["rating"],
        ])
        img = download_image(pid, slug, out_root / args.img_dir,
                             refresh=args.refresh_images)
        print(f"  [{i+1}/{len(todo)}] {slug}: ok "
              f"({len(mlist)} matches, img {img})")
        time.sleep(SLEEP)

    # ---- write files
    def status_of(m):
        if m["ongoing"]:
            return "live"
        if m["finished"]:
            return "ft"
        return "scheduled"

    match_rows = []
    for m, slugs in sorted(matches_by_id.values(), key=lambda x: x[0]["utc"]):
        st = status_of(m)
        match_rows.append([
            iso_z(m["utc"]), m["comp"], m["home"], m["away"],
            m["hscore"] if st != "scheduled" else "",
            m["ascore"] if st != "scheduled" else "",
            st,
            m["minute"] if st == "live" else "",
            ";".join(sorted(slugs)),
        ])

    results_rows.sort(key=lambda r: (r[0], r[-1]))
    fixtures_rows.sort(key=lambda r: (r[0], r[-1]))

    write_csv(out_root / "data/api/matches.csv",
              ["kickoff", "competition", "home", "away", "home_score",
               "away_score", "status", "minute", "players"],
              match_rows)
    write_csv(out_root / "data/manual/results.csv",
              ["slug", "date", "opponent", "score", "competition",
               "minutes", "goals", "assists"],
              [r[:-1] for r in results_rows])
    write_csv(out_root / "data/manual/fixtures.csv",
              ["slug", "date", "opponent", "home_away", "competition"],
              [r[:-1] for r in fixtures_rows])
    write_csv(out_root / "data/api/players.csv",
              ["slug", "league", "age", "born", "foot", "senior_caps",
               "senior_goals", "senior_debut", "youth", "s_apps", "s_starts",
               "s_goals", "s_assists", "s_mins", "s_yellow", "s_red",
               "c_apps", "c_goals", "c_assists", "avg_rating"],
              players_rows)

    if skipped:
        print(f"\nskipped (no fotmob_id in cache): {', '.join(skipped)}")
    if failed:
        print(f"failed this run: {', '.join(failed)}")


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

    d = sub.add_parser("discover-loi",
                       help="add all Irish players from LOI Premier + "
                            "First Division squads")
    d.add_argument("--league-id", default="",
                   help="fotmob league id(s), comma-separated "
                        "(auto-detected if omitted)")
    d.add_argument("--debug", action="store_true")

    a = sub.add_parser("all", help="resolve missing then scrape")
    a.add_argument("--out", default=".")
    a.add_argument("--debug", action="store_true")
    a.add_argument("--img-dir", default="img/players")
    a.add_argument("--refresh-images", action="store_true")

    args = ap.parse_args()
    if args.cmd == "resolve":
        resolve(args)
    elif args.cmd == "discover-loi":
        discover_loi(args)
    elif args.cmd == "scrape":
        scrape(args)
    else:
        args.force = False
        args.only = ""
        resolve(args)
        scrape(args)


if __name__ == "__main__":
    main()
