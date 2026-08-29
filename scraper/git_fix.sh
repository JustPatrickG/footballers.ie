#!/usr/bin/env bash
# Recover from the stuck rebase and push the LOI refresh.
cd "$(dirname "$0")/.." || exit 1
echo "== clearing stale locks =="
rm -f .git/index.lock .git/HEAD.lock 2>/dev/null

echo "== aborting the stuck rebase/merge =="
git rebase --abort 2>/dev/null
git merge  --abort 2>/dev/null

echo "== making sure we are on main =="
git checkout main 2>/dev/null
git symbolic-ref -q HEAD || { echo "STILL DETACHED - stop and tell Claude"; exit 1; }

echo "== state after abort =="
git status -sb | head -5

echo "== committing the refresh (incl. rebuilt site) =="
git add -A
git commit -m "full LOI refresh: fotmob + transfermarkt" --allow-empty

echo "== reconciling with origin (our scrape data wins conflicts) =="
git pull --no-rebase -X ours --no-edit

echo "== pushing =="
git push && echo "PUSH OK" || echo "PUSH FAILED - tell Claude"

echo "== last commits =="
git log --oneline -4
