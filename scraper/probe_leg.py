"""Find a url shape that serves ONE specific leg of a two-legged tie.

The page path identifies the tie, not the leg, so fotmob serves whichever leg
is current. We want match 5988052 (Shamrock Rovers v KuPS, 20 Aug) but the
path gives 5988076 (the 27 Aug return). Try some candidates and report which
match each one actually returns.

    python3 scraper/probe_leg.py
"""
import json
import re
import sys

import requests

WANT = "5988052"
PATH = "/matches/kups-vs-shamrock-rovers/a3ngj"
B = "https://www.fotmob.com"

CANDIDATES = [
    B + PATH,
    B + PATH + "?matchId=" + WANT,
    B + "/match/" + WANT,
    B + "/matches/" + WANT,
    B + "/api/data/matchDetails?matchId=" + WANT,
    B + "/api/matchDetails?matchId=" + WANT + "&ccode3=IRL",
    "https://data.fotmob.com/matchDetails.0.json?matchId=" + WANT,
]

HEAD = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
}


def match_id_of(body):
    """The matchId a response is actually about, JSON or HTML."""
    try:
        d = json.loads(body)
    except ValueError:
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
                      body, re.S)
        if not m:
            return None, None
        d = json.loads(m.group(1)).get("props", {}).get("pageProps", {})
    if not isinstance(d, dict):
        return None, None
    g = d.get("general") or {}
    ev = ((d.get("content") or {}).get("matchFacts") or {}).get("events") or {}
    return (str(g.get("matchId") or "") or None,
            (f"{g.get('homeTeam', {}).get('name')} v "
             f"{g.get('awayTeam', {}).get('name')} · "
             f"{g.get('matchTimeUTC')} · {len(ev.get('events') or [])} events"))


print(f"looking for matchId {WANT} (Shamrock Rovers v KuPS, 20 Aug)\n")
for url in CANDIDATES:
    try:
        r = requests.get(url, headers=HEAD, timeout=20)
    except Exception as e:
        print(f"  {url}\n      FAILED {e}\n")
        continue
    mid, what = match_id_of(r.text)
    flag = "  <<< THIS ONE" if mid == WANT else ""
    print(f"  {url}")
    print(f"      http={r.status_code} bytes={len(r.content)} "
          f"matchId={mid}{flag}")
    if what:
        print(f"      {what}")
    print()
