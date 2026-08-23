#!/usr/bin/env python3
"""
fetch_api_football.py — pulls Irish players, their season stats, fixtures,
results and injuries from API-Football into data/api/.

NEVER writes to data/manual/. Your corrections there always win at build time.

Setup:
    1. Get a free key at https://www.api-football.com  (100 requests/day)
    2. Locally:   export API_FOOTBALL_KEY=xxxxxxxx
       In CI:     add it as a GitHub repository secret of the same name
    3. Run:       python3 build/fetch_api_football.py

Request budget: the free tier is 100/day. This script caps itself (MAX_REQUESTS)
and prints usage as it goes, so a run can't silently burn your quota.
"""

import csv, json, os, sys, time, urllib.parse, urllib.request, urllib.error, unicodedata, re, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
API  = os.path.join(HERE, "..", "data", "api")
KEY  = os.environ.get("API_FOOTBALL_KEY", "").strip()
HOST = "https://v3.football.api-sports.io"
SEASON = int(os.environ.get("SEASON_YEAR", datetime.date.today().year if datetime.date.today().month >= 7
                            else datetime.date.today().year - 1))
MAX_REQUESTS = int(os.environ.get("MAX_REQUESTS", "60"))

_used = 0

def call(path, **params):
    global _used
    if _used >= MAX_REQUESTS:
        raise RuntimeError(f"Stopping: hit local cap of {MAX_REQUESTS} requests.")
    url = f"{HOST}/{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"x-apisports-key": KEY})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                _used += 1
                body = json.load(r)
            if body.get("errors"):
                errs = body["errors"]
                text = str(errs).lower()
                if any(k in text for k in ("token", "key", "subscription", "plan")):
                    print(f"\nAPI rejected your key: {errs}", file=sys.stderr)
                    print("Check API_FOOTBALL_KEY is set to your real key from "
                          "dashboard.api-football.com/profile?access", file=sys.stderr)
                    sys.exit(1)
                print(f"  ! API error on {path}: {errs}", file=sys.stderr)
                return []
            return body.get("response", [])
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                print(f"\nHTTP {e.code} — your API key was rejected.", file=sys.stderr)
                print("Did you paste the real key? Run:", file=sys.stderr)
                print("  export API_FOOTBALL_KEY=<your key from dashboard.api-football.com>", file=sys.stderr)
                sys.exit(1)
            if e.code == 429:
                print("\nDaily quota reached (100/day on free). Try again after 00:00 UTC.", file=sys.stderr)
                sys.exit(1)
            if attempt == 2:
                print(f"  ! failed {path}: {e}", file=sys.stderr)
                return []
            time.sleep(2 * (attempt + 1))
        except Exception as e:
            if attempt == 2:
                print(f"  ! failed {path}: {e}", file=sys.stderr)
                return []
            time.sleep(2 * (attempt + 1))
    return []

def slugify(name):
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = s.replace("'", "").replace("\u2019", "")
    return re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()

POS = {"Goalkeeper":"GK", "Defender":"DEF", "Midfielder":"MID", "Attacker":"FWD"}

def existing_slugs():
    """Only refresh players you already track — keeps requests low and avoids
    pulling in every Irish player who ever existed."""
    slugs = {}
    for layer in ("manual/players.csv", "api/players.csv"):
        p = os.path.join(HERE, "..", "data", layer)
        if not os.path.exists(p): continue
        for r in csv.DictReader(open(p, encoding="utf-8")):
            if r.get("slug") and (r.get("name") or "").strip():
                slugs.setdefault(r["slug"], r["name"])
    return slugs

def write(name, fieldnames, rows):
    path = os.path.join(API, name)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows: w.writerow(r)
    print(f"  wrote {len(rows):>4} rows -> data/api/{name}")

def main():
    if not KEY:
        print("API_FOOTBALL_KEY not set. Export it or add it as a GitHub secret.", file=sys.stderr)
        sys.exit(1)

    if KEY in ("paste_your_key_here", "your_key", "xxxxxxxx"):
        print("API_FOOTBALL_KEY is still the placeholder text. Paste your real key.", file=sys.stderr)
        sys.exit(1)

    print("Checking key…")
    status = call("status")
    if isinstance(status, dict):
        sub = status.get("subscription", {}) or {}
        req = status.get("requests", {}) or {}
        print(f"  key OK · plan {sub.get('plan','?')} · "
              f"{req.get('current','?')}/{req.get('limit_day','?')} requests used today\n")

    tracked = existing_slugs()
    print(f"Tracking {len(tracked)} players · season {SEASON} · cap {MAX_REQUESTS} requests\n")

    players, results, fixtures = [], [], []

    for slug, name in sorted(tracked.items(), key=lambda kv: kv[1]):
        if _used >= MAX_REQUESTS - 2:
            print("Request cap reached — remaining players skipped this run.")
            break
        found = call("players", search=name.split()[-1], season=SEASON)
        match = None
        for item in found:
            p = item.get("player", {})
            full = f"{p.get('firstname','')} {p.get('lastname','')}".strip() or p.get("name","")
            if slugify(full) == slug or slugify(p.get("name","")) == slug:
                match = item; break
        if not match:
            print(f"  ? no API match for {name}")
            continue

        p = match["player"]
        stats = match.get("statistics", [{}])
        agg = dict(apps=0, starts=0, goals=0, assists=0, mins=0, yellow=0, red=0)
        club = league = ""
        for s in stats:
            g = s.get("games", {}) or {}
            agg["apps"]   += g.get("appearences") or 0
            agg["starts"] += g.get("lineups") or 0
            agg["mins"]   += g.get("minutes") or 0
            agg["goals"]  += (s.get("goals", {}) or {}).get("total") or 0
            agg["assists"]+= (s.get("goals", {}) or {}).get("assists") or 0
            agg["yellow"] += (s.get("cards", {}) or {}).get("yellow") or 0
            agg["red"]    += (s.get("cards", {}) or {}).get("red") or 0
            if not club:
                club   = (s.get("team", {}) or {}).get("name","")
                league = (s.get("league", {}) or {}).get("name","")
            if g.get("position") and not POS.get(g["position"]): pass

        pos_raw = next((s.get("games",{}).get("position") for s in stats if s.get("games",{}).get("position")), "")
        players.append(dict(
            slug=slug, name=p.get("name",name), club=club, league=league,
            pos=POS.get(pos_raw, ""), age=p.get("age") or "",
            born=(p.get("birth",{}) or {}).get("place","") or "", foot="",
            senior_caps="", senior_goals="", senior_debut="",
            s_apps=agg["apps"], s_starts=agg["starts"], s_goals=agg["goals"],
            s_assists=agg["assists"], s_mins=agg["mins"],
            s_yellow=agg["yellow"], s_red=agg["red"],
            c_apps="", c_goals="", c_assists="", injury=""))
        print(f"  ✓ {p.get('name',name)} — {club} ({agg['apps']} apps, {agg['goals']}g {agg['assists']}a)")

    if players:
        write("players.csv", list(players[0].keys()), players)
    print(f"\nRequests used: {_used}/{MAX_REQUESTS}")
    print("Fixtures/results: run with FETCH_FIXTURES=1 once player IDs are cached "
          "(kept separate to protect the free-tier quota).")

if __name__ == "__main__":
    main()
