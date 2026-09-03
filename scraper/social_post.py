#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Post new articles (news + match reports) to Reddit and X.

Reddit is free and posts as soon as its secrets are set. X is written and
ready but does nothing until X's secrets are set too - X's API now needs a
paid tier to post, so this idles quietly (like news.yml does without
X_BEARER_TOKEN) rather than failing every run.

STATE. scraper/social_posted.json is a small committed list of article
slugs already posted, so a slug is never posted twice even across separate
Reddit/X runs. Not secret (just slugs+timestamps), unlike the news
pipeline's cursor - fine to keep in git history.

LOOKBACK. Only articles dated within SOCIAL_LOOKBACK_DAYS (default 2) are
considered, so turning this on for the first time doesn't blast out
everything ever published. A hard per-run cap keeps one bad run from
flooding either platform.

Secrets (all optional - each platform posts only once its own are set):
  Reddit   REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET,
           REDDIT_USERNAME, REDDIT_PASSWORD
           + repo variable REDDIT_SUBREDDIT (which subreddit to post to)
  X        X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET
           (OAuth 1.0a user-context - the shape X's v2 POST /tweets needs;
           the same X_BEARER_TOKEN the news fetch uses is read-only and
           can't post)

Usage
  social_post.py            # post anything new, respecting the cap
  social_post.py --dry      # print what would post, touch nothing
"""
import argparse
import csv
import json
import os
import sys
import time
import datetime as dt
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
API = ROOT / "data" / "api"
MANUAL = ROOT / "data" / "manual"
STATE = HERE / "social_posted.json"
SITE_URL = "https://footballers.ie"

LOOKBACK_DAYS = int(os.environ.get("SOCIAL_LOOKBACK_DAYS", "2"))
MAX_PER_RUN = int(os.environ.get("SOCIAL_MAX_PER_RUN", "5"))


def load_articles():
    """Same priority as build/gen.py's load(): a manual row wins over an
    api-sourced row of the same slug."""
    def rows(path):
        if not path.exists():
            return []
        with open(path, newline="", encoding="utf-8") as f:
            return [r for r in csv.DictReader(f) if r.get("slug")]

    manual = rows(MANUAL / "articles.csv")
    mine = {(r.get("slug") or "").strip().lower() for r in manual}
    api = [r for r in rows(API / "articles.csv")
          if (r.get("slug") or "").strip().lower() not in mine]
    return manual + api


def load_state():
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except Exception:
            pass
    return {}


def save_state(state):
    STATE.write_text(json.dumps(state, indent=1, ensure_ascii=False, sort_keys=True))


def pick_candidates(articles, posted, cutoff):
    out = []
    for a in articles:
        slug = (a.get("slug") or "").strip()
        date = (a.get("date") or "").strip()
        if not slug or slug in posted or not date or date < cutoff:
            continue
        headline = (a.get("headline") or "").strip()
        if not headline:
            continue
        out.append(a)
    out.sort(key=lambda a: a.get("date", ""))   # oldest of the new batch first
    return out


def post_text_for(a):
    headline = (a.get("headline") or "").strip()
    slug = (a.get("slug") or "").strip()
    return headline, f"{SITE_URL}/news/{slug}.html"   # news + match reports both live here


# --------------------------------------------------------------- Reddit
def reddit_client():
    cid = os.environ.get("REDDIT_CLIENT_ID", "").strip()
    csec = os.environ.get("REDDIT_CLIENT_SECRET", "").strip()
    user = os.environ.get("REDDIT_USERNAME", "").strip()
    pw = os.environ.get("REDDIT_PASSWORD", "").strip()
    sub = os.environ.get("REDDIT_SUBREDDIT", "").strip()
    if not (cid and csec and user and pw and sub):
        return None, None
    try:
        import praw
    except ImportError:
        print("  reddit: praw not installed - skipping")
        return None, None
    reddit = praw.Reddit(client_id=cid, client_secret=csec,
                         username=user, password=pw,
                         user_agent="footballers.ie bot (by /u/%s)" % user)
    return reddit, sub


def post_to_reddit(reddit, sub, headline, link):
    reddit.subreddit(sub).submit(title=headline[:300], url=link)


# -------------------------------------------------------------------- X
def x_session():
    key = os.environ.get("X_API_KEY", "").strip()
    secret = os.environ.get("X_API_SECRET", "").strip()
    token = os.environ.get("X_ACCESS_TOKEN", "").strip()
    token_secret = os.environ.get("X_ACCESS_SECRET", "").strip()
    if not (key and secret and token and token_secret):
        return None
    try:
        from requests_oauthlib import OAuth1Session
    except ImportError:
        print("  x: requests-oauthlib not installed - skipping")
        return None
    return OAuth1Session(key, client_secret=secret,
                         resource_owner_key=token,
                         resource_owner_secret=token_secret)


def post_to_x(session, headline, link):
    text = f"{headline}\n\n{link}"
    if len(text) > 280:
        # X shortens the link itself; trim the headline, not the url
        room = 280 - len(link) - 4
        text = f"{headline[:max(0, room)].rstrip()}…\n\n{link}"
    r = session.post("https://api.twitter.com/2/tweets", json={"text": text}, timeout=20)
    if r.status_code >= 300:
        raise RuntimeError(f"X {r.status_code}: {r.text[:300]}")


def run(dry=False):
    articles = load_articles()
    state = load_state()
    posted = set(state.keys())
    cutoff = (dt.date.today() - dt.timedelta(days=LOOKBACK_DAYS)).isoformat()
    candidates = pick_candidates(articles, posted, cutoff)[:MAX_PER_RUN]

    if not candidates:
        print("nothing new to post")
        return 0

    reddit, sub = (None, None) if dry else reddit_client()
    x = None if dry else x_session()
    print("reddit: " + ("ON -> r/" + sub if reddit else "off (secrets/subreddit not set)"))
    print("x: " + ("ON" if x else "off (secrets not set - needs a paid X tier to post)"))

    changed = False
    for a in candidates:
        headline, link = post_text_for(a)
        slug = a["slug"]
        result = {"headline": headline, "link": link, "at": dt.datetime.now(dt.timezone.utc).isoformat()}
        if dry:
            print(f"[dry] would post: {headline}  ({link})")
            continue

        ok_any = False
        if reddit:
            try:
                post_to_reddit(reddit, sub, headline, link)
                result["reddit"] = "ok"
                ok_any = True
                print(f"  reddit: posted {slug}")
            except Exception as e:
                result["reddit"] = f"failed: {e}"
                print(f"  reddit: FAILED {slug}: {e}")
        if x:
            try:
                post_to_x(x, headline, link)
                result["x"] = "ok"
                ok_any = True
                print(f"  x: posted {slug}")
            except Exception as e:
                result["x"] = f"failed: {e}"
                print(f"  x: FAILED {slug}: {e}")

        if ok_any:
            state[slug] = result
            changed = True
        time.sleep(2)   # be a considerate caller on both APIs

    if changed:
        save_state(state)
        print(f"state saved ({len(state)} slugs ever posted)")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    return run(dry=args.dry)


if __name__ == "__main__":
    sys.exit(main())
