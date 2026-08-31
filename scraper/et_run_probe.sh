#!/usr/bin/env bash
cd "$(dirname "$0")/.." || exit 1
PY=venv/bin/python3
echo "== ensure playwright =="
$PY -c "import playwright" 2>/dev/null || venv/bin/pip install playwright
echo "== ensure chromium =="
$PY -m playwright install chromium
echo "== probe (a Chromium window will open) =="
$PY scraper/et_probe.py
