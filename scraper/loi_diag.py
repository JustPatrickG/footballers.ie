import json, sys, os
sys.path.insert(0, "scraper")
import irish_scraper as S

def dump(name, obj):
    p = f"scraper/debug/{name}.json"
    with open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps(obj, indent=1)[:300000])
    print("  saved", p)

for div in ("premier division", "first division"):
    print("\n==== %s ====" % div)
    try:
        raw = S.get_json(S.SEARCH_URL.format(
            term=S.requests.utils.quote(div + " Ireland")))
        dump("search_" + div.split()[0], raw)
    except Exception as e:
        print("  search failed:", repr(e))
    lid = None
    try:
        lid = S.find_league_id(div)
        print("  find_league_id ->", lid)
    except Exception as e:
        print("  find_league_id ERROR:", repr(e))
    if not lid:
        print("  (no league id -> discovery can't proceed for this division)")
        continue
    try:
        teams = S.league_teams(lid, debug=True)
        print("  teams found: %d" % len(teams))
        for tid, tn in list(teams.items())[:40]:
            print("     %s  %s" % (tid, tn))
        if teams:
            tid, tn = next(iter(teams.items()))
            pl = S.team_irish_players(tid, tn, debug=True)
            print("  sample squad %s: %d irish players" % (tn, len(pl)))
            for pid, (pname, pos) in list(pl.items())[:8]:
                print("     %s  %s  %s" % (pid, pname, pos))
    except Exception as e:
        print("  league_teams/squad ERROR:", repr(e))
