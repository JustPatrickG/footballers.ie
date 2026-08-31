"""Probe v2: real Chrome + persistent profile. You solve the Cloudflare
checkbox ONCE; the clearance is saved to scraper/.pw_profile and reused."""
import pathlib
from playwright.sync_api import sync_playwright

PROFILE = str(pathlib.Path("scraper/.pw_profile").resolve())
URLS = {
    "profile":   "https://www.extratime.com/player/11152846/charles_akimrintoyo/",
    "squad_ucd": "https://www.extratime.com/players/4/ucd/",
    "comp_prem": "https://www.extratime.com/competition/2132/100/2026-league-of-ireland-premier-division/tables/",
}
out = pathlib.Path("scraper/debug"); out.mkdir(parents=True, exist_ok=True)

def cleared(pg):
    t = (pg.title() or "").lower()
    return "just a moment" not in t and "moment..." not in t and "attention required" not in t

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        PROFILE, channel="chrome", headless=False, locale="en-IE",
        viewport={"width": 1400, "height": 1000})
    pg = ctx.pages[0] if ctx.pages else ctx.new_page()

    first = list(URLS.values())[0]
    pg.goto(first, wait_until="domcontentloaded", timeout=60000)
    print(">>> If you see a Cloudflare 'Verify you are human' box in the Chrome")
    print(">>> window, TICK IT NOW. Waiting up to 3 minutes...")
    ok = False
    for _ in range(180):
        if cleared(pg):
            ok = True; break
        pg.wait_for_timeout(1000)
    print("cleared:", ok, "| title:", (pg.title() or "")[:60])

    for name, url in URLS.items():
        pg.goto(url, wait_until="domcontentloaded", timeout=60000)
        for _ in range(30):
            if cleared(pg): break
            pg.wait_for_timeout(1000)
        pg.wait_for_timeout(1500)
        html = pg.content()
        (out / f"et_{name}.html").write_text(html, encoding="utf-8")
        print("%-12s -> %-55s | %d bytes | %s"
              % (name, (pg.title() or "")[:55], len(html),
                 "OK" if cleared(pg) else "STILL CLOUDFLARE"))
    ctx.close()
print("done. dumps in scraper/debug/et_*.html")
