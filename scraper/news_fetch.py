#!/usr/bin/env python3
"""Pull new posts from the source account via the official X API.

Billing is per *resource returned*, not per request, so the whole design here
is about never paying for the same post twice:

  * the numeric user id is looked up once and cached forever (a User:Read is
    2x the price of a Post:Read, and the id never changes)
  * every call passes since_id, so a poll with nothing new returns zero
    resources and costs nothing - which is why a 30 minute cadence is free
  * replies and reposts are excluded at the API, not after: the pipeline
    throws them away anyway, so paying to receive them is pure waste

Output is posts.json in the shape news_pipeline.normalise() already reads.

Env:
  X_BEARER_TOKEN      required, app-only OAuth 2.0 bearer
  NEWS_SOURCE_HANDLE  required, the account to read, no @
  NEWS_FETCH_MAX      optional, cap per poll (default 20)
  NEWS_FIRST_RUN_MAX  optional, cap on the very first poll (default 5)
"""
import json
import os
import sys
from pathlib import Path

import requests

API = "https://api.x.com/2"
STATE = Path(__file__).with_name("news_since.json")
OUT = Path("posts.json")

TOKEN = os.environ.get("X_BEARER_TOKEN", "").strip()
HANDLE = os.environ.get("NEWS_SOURCE_HANDLE", "").strip().lstrip("@")
MAX = int(os.environ.get("NEWS_FETCH_MAX") or 20)
FIRST_MAX = int(os.environ.get("NEWS_FIRST_RUN_MAX") or 5)

TIMEOUT = 30


def die(msg, code=1):
    print(msg, file=sys.stderr)
    sys.exit(code)


def load_state():
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except (json.JSONDecodeError, OSError):
            print("state file unreadable, starting fresh", file=sys.stderr)
    return {}


def save_state(st):
    STATE.write_text(json.dumps(st, indent=2) + "\n")


def call(session, path, params=None):
    r = session.get(f"{API}{path}", params=params or {}, timeout=TIMEOUT)
    if r.status_code == 401:
        die("401 from X: the bearer token is wrong, revoked, or not app-only. "
            "Regenerate it in the developer portal and update X_BEARER_TOKEN.")
    if r.status_code == 429:
        # Rate limited. Not an error worth failing a scheduled run over - the
        # next poll in 30 minutes picks up exactly where this one stopped,
        # because the cursor has not moved.
        print("429 rate limited, backing off until the next run", file=sys.stderr)
        sys.exit(78)
    if r.status_code >= 400:
        die(f"{r.status_code} from X on {path}: {r.text[:300]}")
    return r.json()


def resolve_user_id(session, st):
    """One User:Read, ever. Cached against the handle so that changing the
       source account re-resolves instead of silently reading the old one."""
    if st.get("user_id") and st.get("handle", "").lower() == HANDLE.lower():
        return st["user_id"]
    j = call(session, f"/users/by/username/{HANDLE}")
    data = j.get("data") or {}
    uid = data.get("id")
    if not uid:
        die(f"no such account: {HANDLE!r} ({j.get('errors')})")
    print(f"resolved @{HANDLE} -> {uid} (billed once, cached from here on)")
    st["user_id"] = uid
    st["handle"] = HANDLE
    return uid


def media_map(includes):
    """media_key -> a usable https image url. Videos and gifs expose a still
       via preview_image_url; photos use url."""
    out = {}
    for m in (includes or {}).get("media") or []:
        key = m.get("media_key")
        url = m.get("url") or m.get("preview_image_url") or ""
        if key and url.startswith("https://"):
            out[key] = url
    return out


def shape(t, mm):
    """Emit the canonical key names normalise() looks for first."""
    refs = {r.get("type") for r in (t.get("referenced_tweets") or [])}
    keys = (t.get("attachments") or {}).get("media_keys") or []
    return {
        "id": t.get("id", ""),
        "text": t.get("text", ""),
        "createdAt": t.get("created_at", ""),
        "isReply": "replied_to" in refs,
        "isRetweet": "retweeted" in refs,
        "isQuote": "quoted" in refs,
        "media": [mm[k] for k in keys if k in mm],
    }


def main():
    if not TOKEN:
        die("X_BEARER_TOKEN is not set")
    if not HANDLE:
        die("NEWS_SOURCE_HANDLE is not set")

    st = load_state()
    session = requests.Session()
    session.headers.update({
        "authorization": f"Bearer {TOKEN}",
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
    })

    uid = resolve_user_id(session, st)
    since = st.get("since_id")

    params = {
        "max_results": max(5, min(MAX if since else FIRST_MAX, 100)),
        "exclude": "replies,retweets",
        "tweet.fields": "created_at,referenced_tweets,attachments",
        "expansions": "attachments.media_keys",
        "media.fields": "url,preview_image_url,type",
    }
    if since:
        params["since_id"] = since
    else:
        print(f"no cursor yet - first run, taking at most {params['max_results']} posts")

    j = call(session, f"/users/{uid}/tweets", params)
    rows = j.get("data") or []
    mm = media_map(j.get("includes"))
    posts = [shape(t, mm) for t in rows]

    OUT.write_text(json.dumps(posts, indent=2) + "\n")

    newest = (j.get("meta") or {}).get("newest_id")
    if newest:
        st["since_id"] = newest
    save_state(st)

    cost = len(posts) * 0.005 + (0.0 if since else 0.01)
    print(f"{len(posts)} new post(s); cursor now {st.get('since_id')}; "
          f"this poll cost about ${cost:.3f}")

    gh = os.environ.get("GITHUB_OUTPUT")
    if gh:
        with open(gh, "a") as fh:
            fh.write(f"count={len(posts)}\n")


if __name__ == "__main__":
    main()
