#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tuning harness. Runs real posts through the real pipeline, prints what it made.

   This imports news_pipeline rather than reimplementing it, so what you read
   here is exactly what the live run would do - a harness that drifts from the
   thing it is tuning is worse than no harness.

   Nothing is published and nothing is marked seen. Safe to run repeatedly.

   First run fetches the last posts from your Apify account and saves them, so
   every run after that judges the SAME posts and any change in the output is
   down to the prompt rather than the news.

     export NEWS_LLM_KEY=gsk_...        # your Groq key
     export APIFY_TOKEN=apify_api_...   # Apify - Settings - API tokens
     venv/bin/python3 scraper/news_tune.py            # fetch + judge
     venv/bin/python3 scraper/news_tune.py --again    # judge the same ones
"""
import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
CACHE = HERE / "debug" / "tune_posts.json"
REPORT = HERE / "debug" / "tune_report.txt"
SCRAPER = "parseforge~x-com-scraper"

os.environ.setdefault("NEWS_SALT", "tuning-only-not-the-real-salt")

spec = importlib.util.spec_from_file_location("np", HERE / "news_pipeline.py")
np = importlib.util.module_from_spec(spec)
spec.loader.exec_module(np)


def fetch(limit):
    token = os.environ.get("APIFY_TOKEN", "")
    if not token:
        sys.exit("APIFY_TOKEN not set, and no saved posts to reuse.\n"
                 "Get one at console.apify.com - Settings - API tokens.")
    url = f"https://api.apify.com/v2/acts/{SCRAPER}/runs/last/dataset/items"
    r = requests.get(url, params={"token": token, "limit": limit}, timeout=60)
    r.raise_for_status()
    items = r.json()
    if not items:
        sys.exit("that Apify run's dataset is empty - run the actor once first")
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(items, indent=1, ensure_ascii=False))
    print(f"fetched {len(items)} posts, saved to {CACHE.name}")
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--again", action="store_true",
                    help="reuse the saved posts instead of fetching")
    ap.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()

    if args.again or CACHE.exists():
        items = json.loads(CACHE.read_text())
        print(f"reusing {len(items)} saved posts ({CACHE.name})")
    else:
        items = fetch(args.limit)

    out = []
    kept = 0
    for i, raw in enumerate(items, 1):
        p = np.normalise(raw)
        block = [f"{'=' * 78}", f"POST {i}", f"{'-' * 78}",
                 p["text"] or "(no text)", ""]

        why = np.pre_gate(p)
        if why:
            block.append(f"  SKIPPED before the model: {why}")
            out.append("\n".join(block))
            continue

        d = np.extract(p["text"])
        if d is None:
            block.append("  MODEL FAILED - no usable answer")
            out.append("\n".join(block))
            continue

        why = np.gate(d)
        if why:
            block.append(f"  GATED: {why}")
            block.append(f"  (model said is_news={d.get('is_news')}, "
                         f"confidence={d.get('confidence')}, "
                         f"facts={len(d.get('facts') or [])})")
            out.append("\n".join(block))
            continue

        kept += 1
        slug = np.slugify(d["headline"])
        block += [
            "  PUBLISHED AS",
            f"    tag         {d.get('tag')}",
            f"    headline    {d['headline']}",
            f"    standfirst  {d['standfirst']}",
            f"    confidence  {d.get('confidence')}",
            f"    player      {np.player_slug_for(d['headline'] + ' ' + ' '.join(d['facts'])) or '-'}",
            f"    slug        {slug}",
            f"    image       {'yes' if p['media'] else 'no'}",
            "    facts",
        ] + [f"      - {f}" for f in d["facts"]]
        out.append("\n".join(block))

    head = (f"{kept} of {len(items)} posts would have been published\n"
            f"model: {np.LLM_MODEL} at {np.LLM_BASE}\n")
    text = head + "\n" + "\n\n".join(out) + "\n"
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(text)
    print(head)
    print(f"full report: {REPORT}")


if __name__ == "__main__":
    main()
