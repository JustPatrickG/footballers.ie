#!/usr/bin/env bash
# Full League of Ireland refresh - FotMob + Transfermarkt, both divisions.
# Run on the Mac (home connection): bash scraper/loi_refresh.sh
cd "$(dirname "$0")/.." || exit 1
PY=venv/bin/python3

echo "== git pull =="
git pull --rebase --autostash || true

echo "== FotMob: discover both LOI divisions =="
$PY scraper/irish_scraper.py discover-loi
echo "== FotMob: fill missing ids =="
$PY scraper/irish_scraper.py resolve
echo "== FotMob: full scrape (players, fixtures, results, matches) =="
$PY scraper/irish_scraper.py scrape
echo "== FotMob: events + lineups =="
$PY scraper/irish_scraper.py events --days 7
$PY scraper/irish_scraper.py lineups
echo "== FotMob: club badges for new teams =="
$PY scraper/irish_scraper.py clubs --missing-only

echo "== Transfermarkt: LOI only (may pause on blocks) =="
LOI=$(awk -F, 'NR>1 && $5=="loi"{printf "%s%s",s,$1; s=","}' scraper/players_list.csv)
echo "   $(printf '%s' "$LOI" | tr ',' '\n' | grep -c .) LOI players"
$PY scraper/tm_scraper.py resolve  --only "$LOI"
$PY scraper/tm_scraper.py profiles --only "$LOI"
$PY scraper/tm_scraper.py transfers --only "$LOI"

echo "== build + deploy =="
$PY build/gen.py
git add -A
git commit -m "full LOI refresh: fotmob + transfermarkt" || echo "nothing to commit"
git pull --rebase --autostash || true
git push
echo "== done =="
