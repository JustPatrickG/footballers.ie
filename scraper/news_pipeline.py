#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Turn posts from the wire into articles.

   The fetch happens elsewhere — the source blocks datacentre IPs, so it runs
   behind a residential proxy and hands the posts to this script. Everything
   from there down lives here: classify, extract, gate, illustrate, publish.

   SOURCE BLINDNESS. This repo is readable by people who should not learn where
   the wire comes from, so nothing that identifies it is ever written to disk.
   No handle, no URL, no post id. De-duplication uses a salted hash, which is
   one-way and — because the salt is a secret — cannot be reversed by anyone
   who guesses a post id. The account name lives only in the fetcher's secrets.

   FAIL CLOSED. A broken run writes nothing. Half an article is worse than no
   article, and a wrong one is worse than either.

   Usage
     news_pipeline.py --posts posts.json         # publish
     news_pipeline.py --posts posts.json --dry   # write drafts, publish nothing
"""
import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
API = ROOT / "data" / "api"
SEEN = API / "news_seen.json"
OUT_CSV = API / "articles.csv"
DRAFTS = API / "news_drafts.json"
SKIPPED = API / "news_skipped.log"
IMG_DIR = ROOT / "img" / "articles"

ARTICLE_COLUMNS = ["slug", "date", "tag", "headline", "standfirst", "body",
                   "author", "image", "player_slug", "expires",
                   "use_player_photo", "partner", "partner_url"]

# Articles go out under the site's journalist byline. That name is on the
# site in this one capacity and no other - no credits, no about text, no
# contact. writers.csv already carries it, so the byline links to a real
# author page rather than a generated stub.
BYLINE = os.environ.get("NEWS_BYLINE", "Jack Deane")
SALT = os.environ.get("NEWS_SALT", "")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_URL = ("https://generativelanguage.googleapis.com/v1beta/models/"
              "{model}:generateContent")

# A plain browser string. Never anything that names the site's people.
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

MIN_CHARS = 40            # shorter than this is a reaction, not a report
MIN_CONFIDENCE = 0.7
MAX_PER_RUN = 6           # a burst of ten posts is a sign something is wrong


# ------------------------------------------------------------------ helpers
def key_for(post_id):
    """The only thing we ever store about a post's identity.

       Post ids are sequential and guessable, so an unsalted hash would be
       trivially reversible by anyone who suspected the source: hash a few
       thousand candidate ids and look for a match. The salt is what makes
       this one-way in practice as well as in principle."""
    if not SALT:
        sys.exit("NEWS_SALT is not set — refusing to write reversible ids.")
    return hashlib.sha256(f"{SALT}:{post_id}".encode()).hexdigest()[:24]


def load_seen():
    try:
        return set(json.loads(SEEN.read_text()))
    except Exception:
        return set()


def save_seen(seen):
    SEEN.parent.mkdir(parents=True, exist_ok=True)
    SEEN.write_text(json.dumps(sorted(seen), indent=0))


def log_skip(key, reason):
    """Why a post didn't become an article. The tuning signal — and note it
       records the hash, not the post, so the log gives nothing away."""
    SKIPPED.parent.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    with open(SKIPPED, "a", encoding="utf-8") as f:
        f.write(f"{stamp}\t{key}\t{reason}\n")
    print(f"  skip {key}: {reason}")


def slugify(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s[:70].strip("-") or "article"


def normalise(post):
    """One post, whatever shape the fetcher handed us, reduced to what this
       script needs. Different actors name these fields differently and the
       naming changes between versions, so take the first key that exists."""
    def pick(*names):
        for n in names:
            v = post.get(n)
            if isinstance(v, str) and v.strip():
                return v.strip()
            if isinstance(v, (int, float)) and v:
                return str(v)
        return ""

    pid = pick("id", "id_str", "tweetId", "restId", "conversationId")
    text = pick("fullText", "full_text", "text", "content")
    created = pick("createdAt", "created_at", "date", "timestamp")
    media = []
    for k in ("media", "images", "photos", "mediaUrls", "extendedEntities"):
        v = post.get(k)
        if isinstance(v, list):
            for item in v:
                if isinstance(item, str):
                    media.append(item)
                elif isinstance(item, dict):
                    u = (item.get("media_url_https") or item.get("url")
                         or item.get("src") or "")
                    if u:
                        media.append(u)
        elif isinstance(v, dict):
            for item in (v.get("media") or []):
                u = (item.get("media_url_https") or item.get("url") or "")
                if u:
                    media.append(u)
    media = [u for u in media if re.match(r"^https://\S+$", u)]
    return dict(id=pid, text=text, created=created, media=media,
                is_reply=bool(post.get("isReply") or post.get("in_reply_to_status_id")),
                is_repost=bool(post.get("isRetweet") or post.get("retweeted_status")),
                quoted=bool(post.get("isQuote") or post.get("quoted_status")))


def pre_gate(p):
    """The cheap checks, before spending an API call. Everything here is a
       category judgement the model shouldn't be asked to make."""
    t = p["text"]
    if not p["id"]:
        return "no id on the post"
    if p["is_repost"] and len(t) < 80:
        return "repost with nothing added"
    if p["is_reply"] or t.startswith("@"):
        return "reply"
    if len(t) < MIN_CHARS:
        return f"too short ({len(t)} chars)"
    if t.count("http") and len(re.sub(r"https?://\S+", "", t).strip()) < MIN_CHARS:
        return "link with no text of its own"
    return ""


# ------------------------------------------------------------------- the model
SYSTEM = """You convert a social media post into a factual news item for a football website.

Rules:
- Use ONLY information present in the post. Never add context, background, or
  detail from your own knowledge.
- Remove all opinion, praise, criticism, emoji, hashtags and hype.
- Preserve hedging exactly. If the post says "understood to be close to a move",
  the fact must stay hedged. Never convert a rumour into a completed event.
- Order facts most newsworthy first, then supporting detail.
- The headline is factual, under 70 characters, no clickbait, no colon-stacking.
- The standfirst is one sentence, 15-30 words, and must not repeat the headline.
- If the post is not football news, set is_news to false and leave other
  fields empty.
- Never name, quote, link or otherwise identify where the post came from.

Return JSON only."""

SCHEMA = {
    "type": "object",
    "properties": {
        "is_news": {"type": "boolean"},
        "skip_reason": {"type": "string"},
        "headline": {"type": "string"},
        "standfirst": {"type": "string"},
        "facts": {"type": "array", "items": {"type": "string"}},
        "tag": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["is_news", "headline", "standfirst", "facts", "confidence"],
}


def extract(text):
    """One call. JSON mode, so there is never prose to parse. Returns None if
       the model gives us something we can't trust — the caller skips."""
    if not GEMINI_KEY:
        sys.exit("GEMINI_API_KEY is not set.")
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": text}]}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseSchema": SCHEMA,
        },
    }
    url = GEMINI_URL.format(model=GEMINI_MODEL)
    for attempt in (1, 2):
        try:
            r = requests.post(url, params={"key": GEMINI_KEY}, json=body,
                              timeout=45, headers={"User-Agent": UA})
            r.raise_for_status()
            raw = (r.json()["candidates"][0]["content"]["parts"][0]["text"])
            return json.loads(raw)
        except Exception as e:
            print(f"    model call failed ({e})")
            if attempt == 1:
                time.sleep(3)
    return None


def gate(d):
    """What the prompt can't be trusted to decide for itself. The model is
       steady at rewriting; where it slips is category — banter read as fact,
       a rumour flattened into a done deal, a birthday post made into news."""
    if not isinstance(d, dict):
        return "model returned no object"
    if not d.get("is_news"):
        return f"not news ({(d.get('skip_reason') or 'no reason given')[:60]})"
    facts = [f.strip() for f in (d.get("facts") or []) if str(f).strip()]
    if not facts:
        return "no facts extracted"
    if len(facts) == 1 and len(facts[0]) < 60:
        return "one trivial fact"
    try:
        conf = float(d.get("confidence") or 0)
    except (TypeError, ValueError):
        conf = 0.0
    if conf < MIN_CONFIDENCE:
        return f"confidence {conf:.2f} below {MIN_CONFIDENCE}"
    if not (d.get("headline") or "").strip():
        return "no headline"
    if not (d.get("standfirst") or "").strip():
        return "no standfirst"
    return ""


# --------------------------------------------------------------------- output
def player_slug_for(text):
    """Attach the article to a tracked player when one is clearly named, so the
       page and the share card can use their photo. Two-word full-name matches
       only — a surname on its own belongs to too many people."""
    path = ROOT / "scraper" / "players_list.csv"
    if not path.exists():
        return ""
    low = " " + re.sub(r"\s+", " ", text.lower()) + " "
    best_name, best_slug = "", ""
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            name = (r.get("name") or "").strip()
            if len(name.split()) < 2:
                continue
            # longest match wins, so "Josh O'Dwyer" beats a shorter name that
            # happens to be a substring of it
            if f" {name.lower()} " in low and len(name) > len(best_name):
                best_name, best_slug = name, r["slug"]
    return best_slug


def fetch_image(urls, slug):
    """First usable image, saved under the article's own folder. The filename
       is the article slug, never the post id."""
    for u in urls[:3]:
        try:
            r = requests.get(u, timeout=30, headers={"User-Agent": UA},
                             stream=True)
            r.raise_for_status()
            ctype = r.headers.get("content-type", "")
            if not ctype.startswith("image/"):
                continue
            ext = ".png" if "png" in ctype else ".jpg"
            blob = r.content
            if len(blob) > 8_000_000 or len(blob) < 2_000:
                continue
            folder = IMG_DIR / slug
            folder.mkdir(parents=True, exist_ok=True)
            rel = f"img/articles/{slug}/lead{ext}"
            (ROOT / rel).write_bytes(blob)
            return rel
        except Exception as e:
            print(f"    image failed ({e})")
    return ""


def build_row(d, post, slug):
    """The article, in the shape the site already reads."""
    facts = [f.strip().rstrip(".") + "." for f in d["facts"] if str(f).strip()]
    image = fetch_image(post["media"], slug)
    body = ""
    if image:
        body += f"![]({image})\\n\\n"
    body += "\\n\\n".join(facts)
    date = (post["created"] or "")[:10]
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        date = dt.date.today().isoformat()
    tag = re.sub(r"[^A-Za-z ]", "", str(d.get("tag") or "NEWS")).strip().upper()[:18]
    return {
        "slug": slug,
        "date": date,
        "tag": tag or "NEWS",
        "headline": d["headline"].strip(),
        "standfirst": d["standfirst"].strip(),
        "body": body,
        "author": BYLINE,
        "image": image,
        "player_slug": player_slug_for(d["headline"] + " " + " ".join(facts)),
        "expires": "",
        "use_player_photo": "" if image else "yes",
        "partner": "",
        "partner_url": "",
    }


def read_existing():
    if not OUT_CSV.exists():
        return []
    with open(OUT_CSV, newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r.get("slug")]


def write_rows(rows):
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_CSV.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=ARTICLE_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, OUT_CSV)


# ----------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--posts", required=True,
                    help="JSON file: a list of posts, or {items:[...]}")
    ap.add_argument("--dry", action="store_true",
                    help="write drafts and publish nothing")
    args = ap.parse_args()

    try:
        raw = json.loads(Path(args.posts).read_text())
    except Exception as e:
        print(f"could not read posts ({e}) — nothing to do")
        return 0
    if isinstance(raw, dict):
        raw = raw.get("items") or raw.get("posts") or raw.get("data") or []
    if not isinstance(raw, list) or not raw:
        # An empty fetch is a failed fetch, not a quiet news day. Either way
        # there is nothing to publish and nothing to record.
        print("no posts in the payload — exiting without changes")
        return 0

    seen = load_seen()
    existing = read_existing()
    have_slugs = {r["slug"] for r in existing}
    fresh, drafts, published = [], [], 0

    posts = [normalise(p) for p in raw]
    posts = [p for p in posts if p["id"] and key_for(p["id"]) not in seen]
    print(f"{len(raw)} posts in, {len(posts)} not seen before")
    if len(posts) > MAX_PER_RUN:
        # Ten new posts in half an hour means a backfill or a duplicated feed,
        # not a busy afternoon. Take the newest few and let the next run catch
        # the rest rather than dumping a wall of articles on the site.
        print(f"  capping at {MAX_PER_RUN} this run")
        posts = posts[:MAX_PER_RUN]

    for p in posts:
        k = key_for(p["id"])
        why = pre_gate(p)
        if why:
            log_skip(k, why)
            fresh.append(k)
            continue
        d = extract(p["text"])
        if d is None:
            log_skip(k, "model gave no usable answer — will retry next run")
            continue                       # deliberately NOT marked seen
        why = gate(d)
        if why:
            log_skip(k, why)
            fresh.append(k)
            continue

        slug = slugify(d["headline"])
        n = 2
        while slug in have_slugs:
            slug = f"{slugify(d['headline'])}-{n}"
            n += 1
        row = build_row(d, p, slug)
        if args.dry:
            drafts.append(dict(row, _confidence=d.get("confidence"),
                               _facts=d.get("facts")))
            print(f"  draft: {row['headline']}")
            continue
        existing.insert(0, row)
        have_slugs.add(slug)
        fresh.append(k)
        published += 1
        print(f"  published: {row['headline']}")

    if args.dry:
        DRAFTS.parent.mkdir(parents=True, exist_ok=True)
        old = []
        try:
            old = json.loads(DRAFTS.read_text())
        except Exception:
            pass
        DRAFTS.write_text(json.dumps(drafts + old, indent=1, ensure_ascii=False))
        print(f"dry run — {len(drafts)} drafts written, nothing published, "
              f"nothing marked seen")
        return 0

    if published:
        write_rows(existing)
    # Only now, with the rows on disk, is a post finished with. A crash before
    # this point leaves it unseen and the next run picks it up again.
    if fresh:
        save_seen(seen | set(fresh))
    print(f"{published} published, {len(fresh)} posts settled")
    return 0


if __name__ == "__main__":
    sys.exit(main())
