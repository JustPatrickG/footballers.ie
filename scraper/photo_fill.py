#!/usr/bin/env python3
"""Fill headshots for roster players who have neither a scraped photo
(img/players/<slug>.png) nor a manual one, using Wikidata's P18 (image)
property - the same free, no-auth, CC-licensed source build/fetch_roster.py
already queries, so the network path is one GitHub Actions is known to reach.

WHAT IT TOUCHES
    data/manual/players.csv, and only the `photo` / `photo_credit` columns,
    and only on rows currently blank there. Never img/ (the scraper owns
    that), never any other column. A row it adds carries nothing but the
    slug and the photo, and gen.py's merge skips blank manual fields, so
    adding one cannot override anything the scraper knows.

Safe to re-run: the "missing" set is recomputed each run rather than
cached, so a player who later gets a scraped photo, or a hand-picked one,
is left alone from then on.

Run:  python3 scraper/photo_fill.py
      python3 scraper/photo_fill.py --dry     # report only, write nothing
"""
import argparse
import csv
import json
import os
import re
import sys
import unicodedata
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
ENDPOINT = "https://query.wikidata.org/sparql"
UA = "footballers.ie photo sync"

# Same population as fetch_roster.py's roster query (Irish men's association
# footballers), plus their image where Wikidata has one.
QUERY = """
SELECT ?playerLabel ?image WHERE {
  ?player wdt:P106 wd:Q937857 ;
          wdt:P27  wd:Q27 ;
          wdt:P21  wd:Q6581097 .
  OPTIONAL { ?player wdt:P18 ?image }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en" }
}
LIMIT 1200
"""


def slugify(name):
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = s.replace("'", "").replace("’", "")
    return re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()


def fetch_images():
    url = ENDPOINT + "?" + urllib.parse.urlencode({"query": QUERY, "format": "json"})
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept": "application/sparql-results+json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        rows = json.load(r)["results"]["bindings"]
    out = {}
    for row in rows:
        name = row.get("playerLabel", {}).get("value", "").strip()
        img = row.get("image", {}).get("value", "").strip()
        if name and img:
            out[slugify(name)] = img
    return out


def missing_headshots():
    """Roster slugs with no image anywhere. Returns (missing, manual, fields)."""
    roster = []
    roster_path = os.path.join(HERE, "players_list.csv")
    if os.path.exists(roster_path):
        with open(roster_path, newline="", encoding="utf-8-sig") as f:
            roster = [(r.get("slug") or "").strip()
                      for r in csv.DictReader(f) if (r.get("slug") or "").strip()]

    have_img = set()
    img_dir = os.path.join(ROOT, "img", "players")
    if os.path.isdir(img_dir):
        have_img = {os.path.splitext(fn)[0] for fn in os.listdir(img_dir)
                    if fn.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))}

    manual_path = os.path.join(ROOT, "data", "manual", "players.csv")
    with open(manual_path, newline="", encoding="utf-8-sig") as f:
        rd = csv.DictReader(f)
        fields = rd.fieldnames or []
        manual = {r["slug"]: r for r in rd if r.get("slug")}

    missing = [s for s in roster
               if s not in have_img
               and not (manual.get(s, {}).get("photo") or "").strip()]
    return missing, manual, fields


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true",
                    help="report what is missing, query nothing, write nothing")
    args = ap.parse_args()

    missing, manual, fields = missing_headshots()
    if "photo" not in fields:
        print("data/manual/players.csv has no photo column - nothing to do")
        return 0
    if not missing:
        print("no missing headshots")
        return 0
    print(f"{len(missing)} roster players with no headshot")
    if args.dry:
        for s in missing[:20]:
            print(f"  {s}")
        if len(missing) > 20:
            print(f"  ... and {len(missing) - 20} more")
        return 0

    try:
        images = fetch_images()
    except Exception as e:
        # soft-fail: leave the gap for next week rather than break the workflow
        print(f"Wikidata query failed: {e}", file=sys.stderr)
        return 0

    filled = 0
    for slug in missing:
        img = images.get(slug)
        if not img:
            continue
        row = manual.setdefault(slug, {k: "" for k in fields})
        row["slug"] = slug
        row["photo"] = img
        row["photo_credit"] = "Wikimedia Commons"
        filled += 1

    if not filled:
        print("Wikidata had no image for any of them")
        return 0

    manual_path = os.path.join(ROOT, "data", "manual", "players.csv")
    with open(manual_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for slug in sorted(manual):
            w.writerow({k: manual[slug].get(k, "") for k in fields})
    print(f"filled {filled} headshot(s) from Wikidata; "
          f"{len(missing) - filled} still have none free to source")
    return 0


if __name__ == "__main__":
    sys.exit(main())
