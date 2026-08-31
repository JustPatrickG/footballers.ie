#!/usr/bin/env bash
# Remove the extratime import (League of Ireland First Division rows) and
# rebuild the Irish roster from Transfermarkt (Irish-nationality only), then
# let FotMob fill fixtures/results and Transfermarkt fill profiles/stats.
cd "$(dirname "$0")/.." || exit 1
PY=venv/bin/python3

echo "== 1. remove the extratime First Division import =="
$PY - <<'PYEOF'
import csv
COLS=["slug","name","club","league","tier","pos","ireland_level"]
rows=[r for r in csv.DictReader(open("scraper/players_list.csv", encoding="utf-8-sig"))]
keep=[r for r in rows if (r.get("league") or "").strip()!="League of Ireland First Division"]
with open("scraper/players_list.csv","w",newline="",encoding="utf-8") as f:
    w=csv.DictWriter(f,fieldnames=COLS,extrasaction="ignore"); w.writeheader()
    for r in keep: w.writerow({c:r.get(c,"") for c in COLS})
print("removed",len(rows)-len(keep),"extratime rows; roster now",len(keep))
PYEOF

echo "== 2. Transfermarkt: discover every Irish-nationality player (+ club) =="
$PY scraper/tm_scraper.py sweep --restart || $PY scraper/tm_scraper.py sweep

echo "== 3. add Irish-first-nationality players who have a club =="
$PY scraper/tm_scraper.py pool-add --declared-only --clubs-only

echo "== 4. FotMob ids for the new players =="
$PY scraper/irish_scraper.py resolve

echo "== 5. FotMob: fixtures, results, stats (full scrape) =="
$PY scraper/irish_scraper.py scrape
$PY scraper/irish_scraper.py events --days 7 || true
$PY scraper/irish_scraper.py lineups || true

echo "== 6. Transfermarkt profiles: market value, dob, career stats =="
$PY scraper/tm_scraper.py resolve || true
$PY scraper/tm_scraper.py profiles || true

echo "== 7. build + push =="
$PY build/gen.py || { echo "BUILD FAILED - tell Claude"; exit 1; }
git add -A
git commit -m "rebuild League of Ireland from Transfermarkt (Irish-only), drop extratime import" || echo "nothing to commit"
git pull --no-rebase -X ours --no-edit
git push && echo "PUSH OK" || echo "PUSH FAILED - tell Claude"
echo "== done =="
