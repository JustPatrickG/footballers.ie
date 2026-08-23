#!/usr/bin/env python3
"""
fetch_roster.py — pulls the roster of Irish professional footballers from Wikidata
and merges it into data/players.csv.

Wikidata is free, has no rate limit worth worrying about, is openly licensed (CC0),
and is explicitly built to be queried — so unlike scraping FBref or Transfermarkt,
this won't break your terms of service or get your IP blocked.

WHAT IT FILLS IN AUTOMATICALLY
    slug, name, club, position, age, born, senior caps/goals (where Wikidata has them)

WHAT IT DELIBERATELY LEAVES ALONE
    season stats, fixtures, results, eligibility, cap_status, tier
    — these are either match-by-match (see fetch_stats.py) or editorial judgement.

Any row already in players.csv keeps its existing values for those columns;
this script only adds new players and refreshes club/position.

Run:  python3 build/fetch_roster.py
"""

import csv, json, os, sys, time, urllib.parse, urllib.request, datetime, unicodedata, re

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data")
ENDPOINT = "https://query.wikidata.org/sparql"
UA = "footballers.ie roster sync (contact: business@matchweek.ie)"

# Irish men's association footballers who are currently on a club's roster.
QUERY = """
SELECT ?player ?playerLabel ?clubLabel ?posLabel ?dob ?birthLabel WHERE {
  ?player wdt:P106 wd:Q937857 ;          # occupation: association football player
          wdt:P27  wd:Q27 .              # country of citizenship: Ireland
  OPTIONAL { ?player wdt:P54 ?club .
             FILTER NOT EXISTS { ?player p:P54 [ ps:P54 ?club ; pq:P582 ?end ] } }
  OPTIONAL { ?player wdt:P413 ?pos }
  OPTIONAL { ?player wdt:P569 ?dob }
  OPTIONAL { ?player wdt:P19  ?birth }
  ?player wdt:P21 wd:Q6581097 .          # male (the site tracks the men's game)
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en" }
}
LIMIT 1200
"""

POS_MAP = {
    "goalkeeper": "GK",
    "defender": "DEF", "centre-back": "DEF", "full-back": "DEF",
    "left-back": "DEF", "right-back": "DEF", "sweeper": "DEF", "wing-back": "DEF",
    "midfielder": "MID", "defensive midfielder": "MID", "attacking midfielder": "MID",
    "central midfielder": "MID", "winger": "MID",
    "forward": "FWD", "striker": "FWD", "centre-forward": "FWD",
    "second striker": "FWD", "inside forward": "FWD",
}

def slugify(name):
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = s.replace("'", "").replace("\u2019", "")   # O'Shea -> oshea, not o-shea
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s

def age_from(dob):
    if not dob: return ""
    try:
        y, m, d = int(dob[:4]), int(dob[5:7]), int(dob[8:10])
        today = datetime.date.today()
        return today.year - y - ((today.month, today.day) < (m, d))
    except Exception:
        return ""

def fetch():
    url = ENDPOINT + "?" + urllib.parse.urlencode({"query": QUERY, "format": "json"})
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/sparql-results+json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)["results"]["bindings"]

def main():
    print("Querying Wikidata…")
    try:
        rows = fetch()
    except Exception as e:
        print(f"Wikidata query failed: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"  {len(rows)} rows returned")

    incoming = {}
    for r in rows:
        name = r.get("playerLabel", {}).get("value", "").strip()
        if not name or name.startswith("Q"):
            continue
        club = r.get("clubLabel", {}).get("value", "").strip()
        if not club:                     # no current club = not a tracked professional
            continue
        pos_raw = r.get("posLabel", {}).get("value", "").strip().lower()
        incoming[slugify(name)] = dict(
            name=name,
            club=club,
            pos=POS_MAP.get(pos_raw, ""),
            age=age_from(r.get("dob", {}).get("value", "")),
            born=r.get("birthLabel", {}).get("value", "").strip(),
        )
    print(f"  {len(incoming)} players with a current club")

    path = os.path.join(DATA, "players.csv")
    existing = {}
    fieldnames = None
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as f:
            rd = csv.DictReader(f)
            fieldnames = rd.fieldnames
            for row in rd:
                existing[row["slug"]] = row
    if not fieldnames:
        print("players.csv missing — run the site build once first.", file=sys.stderr)
        sys.exit(1)

    added = updated = 0
    for slug, inc in incoming.items():
        if slug in existing:
            row = existing[slug]
            # only refresh the facts Wikidata is authoritative on
            for col, key in (("club", "club"), ("pos", "pos"), ("age", "age")):
                if inc[key] and str(row.get(col, "")) != str(inc[key]):
                    row[col] = inc[key]
                    updated += 1
        else:
            row = {k: "" for k in fieldnames}
            row.update(slug=slug, name=inc["name"], club=inc["club"], pos=inc["pos"],
                       age=inc["age"], born=inc["born"], league="", tier="abroad-lower",
                       cap_status="uncapped", eligible="Republic of Ireland:eligible",
                       s_apps=0, s_starts=0, s_goals=0, s_assists=0, s_mins=0,
                       s_yellow=0, s_red=0, c_apps=0, c_goals=0, c_assists=0)
            existing[slug] = row
            added += 1

    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for slug in sorted(existing, key=lambda s: existing[s]["name"]):
            w.writerow(existing[slug])

    print(f"Done. {added} new player(s), {updated} field(s) refreshed, {len(existing)} total.")
    if added:
        print("New players need `league` and `tier` set by hand — they default to abroad-lower.")

if __name__ == "__main__":
    main()
