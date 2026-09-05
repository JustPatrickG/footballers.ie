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
import re
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

def _num(name, default, cast=int):
    """An unset GitHub Actions variable arrives as an empty string, not as an
       absent key, so os.environ.get(name, default) hands back "" and int("")
       raises. Anything unparseable falls back rather than failing the run."""
    try:
        return cast(str(os.environ.get(name, "")).strip())
    except (TypeError, ValueError):
        return default


LOOKBACK_DAYS = _num("SOCIAL_LOOKBACK_DAYS", 2)
MAX_PER_RUN = _num("SOCIAL_MAX_PER_RUN", 2)
X_MONTHLY_MAX = _num("X_MONTHLY_MAX", 200)

# WHAT GETS POSTED. An Irish player scoring, anywhere in the world except the
# League of Ireland - that is the rule, and it goes to both Reddit and X every
# time. Domestic LOI goals are left alone: the audience for those is already
# following those clubs, and they would be most of the volume.
#
# A match report's standfirst is built by auto_reports.py as
#   "LaLiga: Troy Parrott (1 goal, rated 7.4)."
# so a goal is genuinely in the text, and a rating-only report ("shines") has
# no "goal" in it and is correctly left out.
#
# Everything else - transfers, injuries, retirements, rating-only reports - is
# off by default. SOCIAL_POST_NEWS=yes adds the news-pipeline articles
# (transfers and the like); SOCIAL_POST_ALL=yes posts everything that clears
# the weight bar below, which is how this worked before.
POST_NEWS = os.environ.get("SOCIAL_POST_NEWS", "").strip().lower() in ("1", "yes", "true")
POST_ALL = os.environ.get("SOCIAL_POST_ALL", "").strip().lower() in ("1", "yes", "true")

# COST CONTROL. X bills per post, and the price depends entirely on whether
# the text contains a link: about $0.015 without, about $0.20 with - roughly
# thirteen times more. At six posts a day that is the difference between $3
# and $38 a month, so X posts carry no link by default and Reddit, which is
# free, carries them instead.
#
# Careful with X_SUFFIX: X auto-links anything that looks like a domain, so
# putting "footballers.ie" in the text would very likely be billed as a post
# with a link and undo the whole saving. An @handle is safe; a domain is not.
X_INCLUDE_LINK = os.environ.get("X_INCLUDE_LINK", "").strip().lower() in ("1", "yes", "true")
X_SUFFIX = os.environ.get("X_SUFFIX", "").strip()

# A hard ceiling on X posts per calendar month, counted from the state file.
# This is a spend cap, not a style choice: at the link-free price, 200 posts
# is about $3. Reddit is free and is not capped.
NO_WEIGHT = 150.0
MIN_WEIGHT = _num("SOCIAL_MIN_WEIGHT", 140.0, float)   # only used by SOCIAL_POST_ALL


def is_report(a):
    return (a.get("slug") or "").startswith("report-")


def is_loi(a):
    return (a.get("tag") or "").strip().upper() == "LEAGUE OF IRELAND"


# Deliberately narrow. "goal" on its own would match goalkeeper and goalless,
# and "winner" matches award write-ups, so neither is in here.
GOAL_RE = re.compile(r"\b(scores?|scored|nets?|netted|brace|hat-?tricks?)\b", re.I)


def scored(a):
    """True when the article is about someone putting the ball in the net.

       A generated report says so in its standfirst - auto_reports.py builds it
       as "LaLiga: Troy Parrott (1 goal, rated 7.4)" - and a rating-only report
       ("shines") has no goal in it. A hand-written article has no such shape,
       so its headline is what gets read. Either way the question is the same:
       did somebody score."""
    if is_report(a):
        return "goal" in (a.get("standfirst") or "").lower()
    return bool(GOAL_RE.search((a.get("headline") or "") + " " + (a.get("standfirst") or "")))


def wanted(a):
    """The posting rule, in one place."""
    if POST_ALL:
        return weight_of(a) >= MIN_WEIGHT
    if is_loi(a):
        return False
    if scored(a):
        return True          # a goal abroad, whoever wrote it up
    return POST_NEWS and not is_report(a)


def weight_of(a):
    try:
        return float(a.get("weight") or NO_WEIGHT)
    except (TypeError, ValueError):
        return NO_WEIGHT


def posted_today(state, platform):
    """How many the given platform has already taken today, so the daily cap
       survives a restart - the state file is the only memory this has."""
    today = dt.date.today().isoformat()
    n = 0
    for v in state.values():
        if not isinstance(v, dict):
            continue
        if v.get(platform) == "ok" and str(v.get("at", ""))[:10] == today:
            n += 1
    return n


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
    # Best first, not oldest first. Reddit only gets a few slots a day and a
    # backlog only clears a couple at a time, so whatever goes out first has
    # to be the strongest thing waiting - never whatever happens to be oldest.
    out.sort(key=lambda a: (weight_of(a), a.get("date", "")), reverse=True)
    return out


def post_text_for(a):
    headline = (a.get("headline") or "").strip()
    slug = (a.get("slug") or "").strip()
    return headline, f"{SITE_URL}/news/{slug}.html"   # news + match reports both live here


def x_posts_this_month(state):
    """X posts already made this calendar month, so the cap survives restarts."""
    month = dt.date.today().strftime("%Y-%m")
    return sum(1 for v in state.values()
               if isinstance(v, dict) and v.get("x") == "ok"
               and str(v.get("at", ""))[:7] == month)


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
    text = headline if not X_INCLUDE_LINK else f"{headline}\n\n{link}"
    if X_SUFFIX:
        text = f"{text}\n\n{X_SUFFIX}"
    if not X_INCLUDE_LINK:
        if len(text) > 280:
            text = text[:279].rstrip() + "\u2026"
        r = session.post("https://api.twitter.com/2/tweets", json={"text": text}, timeout=20)
        if r.status_code >= 300:
            raise RuntimeError(f"X {r.status_code}: {r.text[:300]}")
        return
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
    if dry:
        # A dry run that reported "nobody wants this" purely because no keys
        # are set would tell you nothing. Preview as though both were on, so
        # what you see is the split you would get once they are.
        print("dry run - previewing as though both platforms were configured")
    else:
        print("reddit: " + ("ON -> r/" + sub if reddit else "off (secrets/subreddit not set)"))
        print("x: " + ("ON" if x else "off (secrets not set - needs a paid X tier to post)"))

    if not dry and not reddit and not x:
        # Nothing configured means nothing can be decided. Falling through here
        # would mark every article "skipped" and settle it forever - so the day
        # the X keys are finally added, the whole backlog would already be
        # marked done and would never post.
        print("no platform configured - nothing posted, nothing marked")
        return 0

    rule = ("everything over weight %.0f" % MIN_WEIGHT) if POST_ALL else (
           "Irish goals outside the League of Ireland" + (" + news" if POST_NEWS else ""))
    print(f"posting: {rule}")
    x_used = x_posts_this_month(state)
    x_left = X_MONTHLY_MAX - x_used
    price = 0.20 if X_INCLUDE_LINK else 0.015
    print(f"x: {x_used}/{X_MONTHLY_MAX} posts this month "
          f"(~${x_used * price:.2f} so far, {'with' if X_INCLUDE_LINK else 'no'} links)")
    if x_left <= 0:
        print("  x: monthly cap reached - Reddit only until the 1st")

    changed = False
    for a in candidates:
        headline, link = post_text_for(a)
        slug = a["slug"]
        w = weight_of(a)
        result = {"headline": headline, "link": link, "weight": w,
                  "at": dt.datetime.now(dt.timezone.utc).isoformat()}

        # who actually wants this one
        take = wanted(a)
        take_reddit = (dry or bool(reddit)) and take
        take_x = (dry or bool(x)) and take and x_left > 0

        if dry:
            if take and take_x:
                x_left -= 1
            who = ("reddit + x" if (take and take_x) else "reddit only" if take
                   else "League of Ireland" if is_loi(a)
                   else "no goal" if is_report(a) else "not a goal")
            print(f"[dry] {who:18} {headline[:56]}")
            continue

        if not (take_reddit or take_x):
            if take:
                # It qualifies - the platform just has no room right now
                # (monthly cap). Leave it unmarked so it goes out when the
                # cap resets, rather than settling it as though it failed.
                print(f"  qualifies, but X is capped - holding: {headline[:44]}")
                continue
            # Genuinely not wanted. Settle it rather than reconsider the same
            # article every 20 minutes for the next two days.
            why = ("League of Ireland" if is_loi(a)
                   else "no goal in it" if is_report(a) else "not a goal report")
            state[slug] = dict(result, skipped=why)
            changed = True
            print(f"  not posting ({why}): {headline[:52]}")
            continue

        ok_any = False
        if take_reddit:
            try:
                post_to_reddit(reddit, sub, headline, link)
                result["reddit"] = "ok"
                ok_any = True
                print(f"  reddit: posted {slug}")
            except Exception as e:
                result["reddit"] = f"failed: {e}"
                print(f"  reddit: FAILED {slug}: {e}")
        if take_x:
            try:
                post_to_x(x, headline, link)
                result["x"] = "ok"
                ok_any = True
                x_left -= 1
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
