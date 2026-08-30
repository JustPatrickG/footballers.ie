#!/usr/bin/env python3
"""Sit through the matches that are on, and scrape each one the moment it ends.

Why this exists: GitHub throttles scheduled workflows hard. A '*/10' cron
really fires every few hours, so a match finishing at 14:20 might not be
scraped until 17:00 - which is how a player ends up on the site with no
minutes against a game everyone watched him play.

So instead of asking "has anything finished?" every few minutes and being
ignored, this starts once, stays up while there is football on, polls the
match pages itself, and reacts within a couple of minutes of full time.
Actions minutes are free on a public repo, so the waiting costs nothing.

Exits immediately when nothing is on, so an idle run is seconds long.
"""
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import irish_scraper as ir            # noqa: E402  (path set above)
import goal_alert                    # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

POLL_S      = int(os.environ.get("WATCH_POLL", 120))     # between checks
BUDGET_MIN  = int(os.environ.get("WATCH_BUDGET", 300))   # job stays up this long
LOOKAHEAD_M = int(os.environ.get("WATCH_LOOKAHEAD", 210))# start this early
SETTLE_S    = int(os.environ.get("WATCH_SETTLE", 120))   # let ratings settle
HARD_END_M  = 165                                        # assume done by now
EARLIEST_FT = 85    # no 90-minute game is over before this, so do not ask


def log(msg):
    print(f"[{dt.datetime.now(dt.timezone.utc):%H:%M:%S}] {msg}", flush=True)


def watchable(now):
    """Matches already under way, or kicking off soon enough to wait for."""
    idx = json.loads((HERE / "match_index.json").read_text())
    out = []
    for m in idx:
        ko = ir.parse_iso(m.get("kickoff"))
        if not ko:
            continue
        mins_to_ko = (ko - now).total_seconds() / 60
        if -HARD_END_M <= mins_to_ko <= LOOKAHEAD_M:
            out.append(dict(m, _ko=ko))
    return out


def status_of(m, now):
    """'ft' | 'live' | 'scheduled'. Falls back to the clock if the page fails,
       so an unreadable page can never keep the job alive forever."""
    if m.get("url"):
        try:
            page = ir.match_page(m["url"])
            parsed = ir.parse_match_page(page) if page else None
            if parsed:
                return parsed[2] or "scheduled"
        except Exception as e:
            log(f"  page read failed for {m['home']} v {m['away']}: {e}")
    if now >= m["_ko"] + dt.timedelta(minutes=HARD_END_M):
        return "ft"
    return "live" if now >= m["_ko"] else "scheduled"


def run(cmd):
    log("  $ " + " ".join(cmd))
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if r.stdout.strip():
        print("   " + r.stdout.strip().replace("\n", "\n   "), flush=True)
    if r.returncode != 0:
        log(f"  (exit {r.returncode}) {r.stderr.strip()[:300]}")
    return r.returncode == 0


def harvest(finished):
    """Scrape and publish. Committing per finish rather than at the end is the
       whole point - the stats should be up while people are still looking."""
    names = ", ".join(f"{m['home']} v {m['away']}" for m in finished)
    log(f"full time: {names} - scraping")
    py = sys.executable
    run([py, "scraper/irish_scraper.py", "scrape", "--active"])
    run([py, "scraper/irish_scraper.py", "events", "--days", "1"])
    run([py, "scraper/irish_scraper.py", "lineups"])
    subprocess.run(["git", "add", "-A", "data/api", "scraper/match_index.json"],
                   cwd=ROOT)
    staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT)
    if staged.returncode == 0:
        log("  nothing changed")
        return
    subprocess.run(["git", "commit", "-q", "-m", f"full-time scrape: {names}"[:72]],
                   cwd=ROOT)
    if not run(["git", "pull", "--rebase", "--autostash"]):
        subprocess.run(["git", "rebase", "--abort"], cwd=ROOT)
        log("  lost the race with another job; next finish catches up")
        return
    run(["git", "push"])
    log("  pushed")


def main():
    now = dt.datetime.now(dt.timezone.utc)
    watching = watchable(now)
    if not watching:
        log("nothing on - exiting")
        return 0

    for m in watching:
        log(f"watching {m['home']} v {m['away']} (ko {m['_ko']:%H:%M}Z)")
    deadline = now + dt.timedelta(minutes=BUDGET_MIN)
    done = set()
    IRISH = goal_alert.load_irish()
    CFG = goal_alert.config()
    SEEN = set()
    log('goal alerts: ' + (('ON -> ' + CFG['to']) if CFG else 'logging only (set GMAIL_* secrets to email)'))

    while dt.datetime.now(dt.timezone.utc) < deadline:
        now = dt.datetime.now(dt.timezone.utc)
        just_finished = []
        for m in watching:
            if m["fotmob_id"] in done:
                continue
            if now < m["_ko"]:
                continue                       # not kicked off yet
            # live goal alerts, from kickoff onwards
            try:
                _d = ir.get_json(ir.MATCH_API.format(mid=m["fotmob_id"]))
                goal_alert.alert_new_goals(m, _d, SEEN, CFG, IRISH, log)
            except Exception as _e:
                log(f"  goal-check failed ({m['home']} v {m['away']}): {_e}")
            time.sleep(1)
            # Full time: only ask once a game could plausibly have ended, so we
            # do not hammer match pages that are nowhere near over.
            if now < m["_ko"] + dt.timedelta(minutes=EARLIEST_FT):
                continue
            st = status_of(m, now)
            time.sleep(1)                    # be a considerate caller
            if st == "ft":
                done.add(m["fotmob_id"])
                just_finished.append(m)

        if just_finished:
            if SETTLE_S:
                log(f"  waiting {SETTLE_S}s for ratings to settle")
                time.sleep(SETTLE_S)
            harvest(just_finished)

        if len(done) == len(watching):
            log("all watched matches finished - done")
            return 0
        time.sleep(POLL_S)

    log(f"budget of {BUDGET_MIN} min used up; {len(watching) - len(done)} "
        f"still unfinished - the next run picks them up")
    return 0


if __name__ == "__main__":
    sys.exit(main())
