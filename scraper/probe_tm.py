"""Grab one Transfermarkt player page so the transfer-history parser can be
written against something real instead of guessed at.

Dumps into scraper/debug/ and prints what it found.

    python3 scraper/probe_tm.py            # Enda Stevens
    python3 scraper/probe_tm.py 711544     # or any tm_id
"""
import json
import re
import sys
from pathlib import Path

import requests

TID = sys.argv[1] if len(sys.argv) > 1 else "85706"
OUT = Path(__file__).resolve().parent / "debug"
OUT.mkdir(exist_ok=True)

HEAD = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0 Safari/537.36"),
    "Accept-Language": "en-GB,en;q=0.9",
}

TARGETS = [
    ("profile", f"https://www.transfermarkt.co.uk/-/profil/spieler/{TID}"),
    ("transfers_page", f"https://www.transfermarkt.co.uk/-/transfers/spieler/{TID}"),
    ("transfers_api", f"https://www.transfermarkt.co.uk/ceapi/transferHistory/list/{TID}"),
]

for name, url in TARGETS:
    try:
        r = requests.get(url, headers=HEAD, timeout=25)
    except Exception as e:
        print(f"{name:16} FAILED  {e}")
        continue
    ext = "json" if "json" in r.headers.get("content-type", "") else "html"
    path = OUT / f"tm_{name}_{TID}.{ext}"
    path.write_text(r.text[:2_000_000])
    note = ""
    if ext == "json":
        try:
            d = json.loads(r.text)
            rows = d.get("transfers") or []
            note = f"{len(rows)} transfers"
            if rows:
                t = rows[0]
                note += (f" | latest: {t.get('date')} "
                         f"{(t.get('from') or {}).get('clubName')} -> "
                         f"{(t.get('to') or {}).get('clubName')}")
        except Exception as e:
            note = f"not json ({e})"
    else:
        hits = len(re.findall(r"transferhistorie|transfer-history|Transfer history",
                              r.text, re.I))
        note = f"{hits} mentions of transfer history in the html"
    print(f"{name:16} http={r.status_code} bytes={len(r.content):>8}  {note}")
    print(f"                 -> {path}")

print("\nSend me whichever of those came back with real data.")
