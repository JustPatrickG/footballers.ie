#!/usr/bin/env bash
cd "$(dirname "$0")/.." || exit 1
echo "== build (confirms the site renders) =="
venv/bin/python3 build/gen.py || { echo "BUILD FAILED - tell Claude"; exit 1; }
echo "== commit =="
git add scraper/players_list.csv scraper/irish_scraper.py build/gen.py \
        scraper/goal_alert.py scraper/match_watch.py .github/workflows/matchwatch.yml
git commit -m "site audit fixes + personal goal alerts for Irish players abroad" || echo "nothing to commit"
echo "== reconcile + push =="
git pull --no-rebase -X ours --no-edit
git push && echo "PUSH OK" || echo "PUSH FAILED - tell Claude"
