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
                   "use_player_photo", "partner", "partner_url", "weight"]
# "weight" belongs to auto_reports.py, not to news posts - but write_rows()
# rewrites the whole file through this list with extrasaction="ignore", so
# leaving it out silently strips the weight off every match report and
# collapses same-day ordering to date only. News rows just leave it blank.

# Articles go out under the site's journalist byline. That name is on the
# site in this one capacity and no other - no credits, no about text, no
# contact. writers.csv already carries it, so the byline links to a real
# author page rather than a generated stub.
# `or`, not a get() default: an unset repo variable arrives as an empty
# string rather than absent, so the default never fires and everything
# downstream ends up blank - which is how the base URL became "".
BYLINE = os.environ.get("NEWS_BYLINE") or "Jack Deane"
SALT = os.environ.get("NEWS_SALT", "")
# The model provider is configuration, not code. Free tiers appear and vanish,
# and one of them wanting an ID scan shouldn't mean editing this file. Anything
# that speaks the OpenAI chat-completions shape works — Groq, Mistral,
# OpenRouter, Cerebras, OpenAI itself — and Anthropic is handled as the one
# exception because its API is shaped differently.
#
# Defaults are Groq: free, no card, no identity check, and far more requests a
# day than this will ever use.
LLM_KEY = os.environ.get("NEWS_LLM_KEY", "")
LLM_BASE = os.environ.get("NEWS_LLM_BASE") or "https://api.groq.com/openai/v1"
LLM_MODEL = os.environ.get("NEWS_LLM_MODEL") or "openai/gpt-oss-120b"
LLM_KIND = (os.environ.get("NEWS_LLM_KIND") or "openai").strip().lower()

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

Return a JSON object with EXACTLY these keys and no others:
  is_news      boolean
  skip_reason  string - why it is not news; empty string when it is
  headline     string
  standfirst   string
  facts        array of strings
  body         string - the article itself, in prose
  tag          string - one of: transfer, injury, callup, contract, result, other
  confidence   number between 0 and 1

You are licensed to use the FACTS in the post. You are NOT licensed to use its
words. Reusing its phrasing is plagiarism and breaks the agreement the facts
come under, so it is the single most serious mistake you can make here.
- Report the facts as if you had learned them independently and never saw this
  wording. Change the sentence structures, the order and the phrasing.
- No run of more than six consecutive words may match the post. Names of people,
  clubs and competitions are the only unavoidable exception.
- Do not simply reorder the post's sentences or swap in synonyms. Rebuild it.

Sourcing:
- Use ONLY information present in the post. Never add context, background or
  statistics from your own knowledge.
- Never add a descriptor the post does not contain. If the post does not state
  a player's position, age, nationality, club or role, do not supply one.
  "Ireland U21 international" is not licence to write "striker".
- Remove all opinion, praise, criticism, emoji, hashtags and hype.
- No predictions and no consequences. What a move "could" or "should" lead to,
  what it means for someone's chances, how it might affect selection - all of
  that is opinion even when the post says it. Report what happened, not what
  might follow.
- Preserve hedging exactly. If the post says a move is close, expected or being
  pursued, it must stay that way. Never turn a rumour into a completed deal.
- Never name, quote, link or otherwise identify where the post came from.

headline: factual, under 70 characters, no clickbait, no colon-stacking.

standfirst: ONE sentence, 8 to 16 words. It must NOT restate the headline or
repeat any fact already in it. Pick the single most interesting OTHER detail in
the post - a number, a piece of background, a bit of context - and state it
plainly. Shape to aim for:
  headline:   Como Sign 17 Year Old Josh Harpur
  standfirst: The Bohemians striker has played just 30 minutes of senior football.
  headline:   Gavin Bazunu Nearing Permanent Exit to Bolton Wanderers
  standfirst: Bazunu played only 6 times last season while on loan at Stoke City.
Those two show the SHAPE only. Never invent a statistic that is not in the post -
if the post holds no second fact, use the nearest supporting detail it does hold.

facts: the bare factual claims from the post, most newsworthy first.

body: ONE flowing paragraph, two to four sentences, 40 to 90 words, built from
those facts and nothing else. Never use line breaks, newlines, bullet points or
the characters backslash-n. Do not repeat the standfirst or open by restating
the headline.
Combine related facts into single sentences. One sentence per fact reads like a
list and is wrong. Leave out background the reader already has: a player's
nationality or position is never a sentence of its own.

Wrong - a list with the facts spread one per sentence, including filler:
  "Gavin Bazunu has completed a permanent move to Bolton Wanderers. He signed a
  three-year deal with Bolton Wanderers. He is an Ireland international
  goalkeeper. His recent years were disrupted by injury."
Right - the same facts, folded, with the filler carried inside a sentence that
does other work:
  "Gavin Bazunu has completed a permanent move to Bolton Wanderers, signing a
  three-year deal. The Ireland goalkeeper arrives after a difficult couple of
  years disrupted by injury and a lack of consistent football."

If the post is not football news - a podcast or video promo, a plug, banter, a
poll, a birthday - set is_news false and leave the other fields empty.

Return JSON only."""

# The output contract lives in SYSTEM above. It is prose because it has to
# survive a provider swap - not every backend accepts a json_schema block.


def extract(text, retry_note="", temperature=0):
    """One call. JSON mode, so there is never prose to parse. Returns None if
       the model gives us something we can't trust — the caller skips, and the
       post is deliberately left unseen so the next run tries again."""
    if not LLM_KEY:
        sys.exit("NEWS_LLM_KEY is not set.")

    if LLM_KIND == "anthropic":
        url = f"{LLM_BASE.rstrip('/')}/v1/messages"
        headers = {"x-api-key": LLM_KEY, "anthropic-version": "2023-06-01",
                   "content-type": "application/json", "User-Agent": UA}
        payload = {"model": LLM_MODEL, "max_tokens": 1200,
                   "temperature": temperature,
                   "system": SYSTEM + retry_note,
                   "messages": [{"role": "user", "content": text}]}
        def unwrap(j):
            return j["content"][0]["text"]
    else:
        url = f"{LLM_BASE.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {LLM_KEY}",
                   "content-type": "application/json", "User-Agent": UA}
        payload = {"model": LLM_MODEL, "temperature": temperature,
                   "response_format": {"type": "json_object"},
                   "messages": [{"role": "system", "content": SYSTEM + retry_note},
                                {"role": "user", "content": text}]}
        def unwrap(j):
            return j["choices"][0]["message"]["content"]

    for attempt in (1, 2):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=60)
            if r.status_code == 429:
                # rate limited: wait once, then give up and leave it unseen
                print("    rate limited, waiting")
                time.sleep(20)
                continue
            r.raise_for_status()
            raw = unwrap(r.json())
            # some models still fence the block despite JSON mode
            raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(),
                         flags=re.M).strip()
            return json.loads(raw)
        except Exception as e:
            print(f"    model call failed ({e})")
            if attempt == 1:
                time.sleep(3)
    return None


def _words(t):
    return re.findall(r"[a-z0-9]+", (t or "").lower())


def longest_shared_run(out, src):
    """Longest run of consecutive words appearing in both. This is the check
       that matters: the licence covers the facts, not the copy, so lifting a
       sentence is a contract breach, not a style problem."""
    a, b = _words(out), _words(src)
    if not a or not b:
        return 0, ""
    index = {}
    for i, w in enumerate(b):
        index.setdefault(w, []).append(i)
    best, best_txt = 0, ""
    for i in range(len(a)):
        for j in index.get(a[i], ()):
            n = 0
            while i + n < len(a) and j + n < len(b) and a[i + n] == b[j + n]:
                n += 1
            if n > best:
                best, best_txt = n, " ".join(a[i:i + n])
    return best, best_txt


MAX_SHARED_RUN = 7

# Defects that mean "written badly", not "not a story". These get one more
# attempt: binning a real transfer because the model added a position it was
# never given is a worse outcome than spending a second call on it.
RETRYABLE = ("copies ", "invented a position", "standfirst restates",
             "standfirst too long", "body too long", "body too thin",
             "no body written", "model ignored the key contract")

RETRY_NOTE = ("\n\nYOUR PREVIOUS ATTEMPT WAS REJECTED: {why}. "
              "Write it again from scratch. Keep every fact and keep the hedging. "
              "Change the phrasing and sentence structure and do not follow the "
              "post's order. No run of more than six consecutive words may match "
              "the post. Add nothing the post does not state - no position, no "
              "age, no nationality, no prediction.")


def _flatten(text):
    """One paragraph, whatever the model did. Some models emit the two
       characters backslash-n instead of a real newline, which then renders
       as visible junk in the article."""
    t = (text or "").replace("\\n", " ").replace("\\r", " ")
    return " ".join(t.split())


POSITIONS = ("striker", "winger", "midfielder", "defender", "goalkeeper",
             "keeper", "forward", "fullback", "full-back", "centre-back",
             "centre back", "attacker", "playmaker", "wing-back")


def gate(d, source_text=""):
    """What the prompt can't be trusted to decide for itself. The model is
       steady at rewriting; where it slips is category - banter read as fact, a
       rumour flattened into a done deal - and, worse, reusing the source's
       actual words. Every field is read up front so the checks below can be
       reordered without tripping over a name that isn't bound yet."""
    if not isinstance(d, dict):
        return "model returned no object"
    if not d.get("is_news"):
        return f"not news ({(d.get('skip_reason') or 'no reason given')[:60]})"

    facts = [f.strip() for f in (d.get("facts") or []) if str(f).strip()]
    head = (d.get("headline") or "").strip()
    sf = (d.get("standfirst") or "").strip()
    prose = _flatten(d.get("body"))

    # --- substance
    if not facts:
        return "no facts extracted"
    if len(facts) == 1 and len(facts[0]) < 60:
        return "one trivial fact"
    if "confidence" not in d:
        return "model ignored the key contract (no confidence field)"
    try:
        conf = float(d.get("confidence") or 0)
    except (TypeError, ValueError):
        conf = 0.0
    if conf < MIN_CONFIDENCE:
        return f"confidence {conf:.2f} below {MIN_CONFIDENCE}"

    # --- shape
    if not head:
        return "no headline"
    if not sf:
        return "no standfirst"
    if not prose:
        return "no body written"
    sfw, bw = len(sf.split()), len(prose.split())
    if sfw > 24:
        return f"standfirst too long ({sfw} words, house style is 8-16)"
    if bw < 25:
        return f"body too thin ({bw} words)"
    if bw > 120:
        return f"body too long ({bw} words)"

    # --- a standfirst that just replays the headline
    big = lambda t: set(re.findall(r"[a-z]{5,}", t.lower()))
    overlap = big(sf) & big(head)
    if len(overlap) >= 4:
        return f"standfirst restates the headline (shares {sorted(overlap)})"

    if source_text:
        # Inventing a position is the likeliest embarrassing hallucination: the
        # post says "Ireland U21 international", the model writes "striker".
        src = source_text.lower()
        written = " ".join([head.lower(), sf.lower(), prose.lower()])
        for pos in POSITIONS:
            if pos in written and pos.split("-")[0].split()[0] not in src:
                return f"invented a position not in the post ({pos!r})"
        # The licence covers the facts, not the copy. Lifting a sentence is a
        # contract breach, so this is the last and hardest check.
        run, phrase = longest_shared_run(" ".join([head, sf, prose]), source_text)
        if run > MAX_SHARED_RUN:
            return f"copies {run} consecutive words from the source ({phrase!r})"
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
        body += f"![]({image})\n\n"
    body += _flatten(d.get("body")) or "\n\n".join(facts)
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


# --------------------------------------------------------------- one story
# Three posts about one goal are three posts. They are not three stories, and
# publishing them as three buries everything else on the homepage.

# Tags that all describe the same kind of event: something happened in a match.
# A match report written by auto_reports.py carries no tag at all, so it lands
# in this bucket too and competes with the news write-up of the same goal.
RESULT_TAGS = {"RESULT", "GOAL", "MATCH", "REPORT", ""}


def story_key(row):
    """Same player, same day, same kind of event = same story. Returns None
       when there is no player to match on, which lets the article through
       rather than guessing."""
    who = (row.get("player_slug") or "").strip().lower()
    if not who:
        return None
    tag = (row.get("tag") or "").strip().upper()
    if (row.get("slug") or "").startswith("report-") or tag in RESULT_TAGS:
        tag = "RESULT"
    return (who, (row.get("date") or "")[:10], tag)


def richness(row):
    """Which telling of a story to keep. The longer body carries more of the
       facts; a picture breaks the tie. A generated match report loses to a
       written-up post of the same length, because the post had a human
       deciding it was worth posting."""
    body = (row.get("body") or "")
    return (len(body),
            1 if (row.get("image") or "").strip() else 0,
            0 if (row.get("slug") or "").startswith("report-") else 1)


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


def dedupe_existing():
    """One-off tidy of articles already published: where several articles tell
       the same story, keep the richest and drop the rest. Same rule the live
       pipeline now applies as it publishes, applied backwards."""
    rows = read_existing()
    best = {}
    for r in rows:
        k = story_key(r)
        if k is None:
            continue
        if k not in best or richness(r) > richness(best[k]):
            best[k] = r
    keep, dropped = [], []
    for r in rows:
        k = story_key(r)
        if k is None or best[k] is r:
            keep.append(r)
        else:
            dropped.append((r, best[k]))
    if not dropped:
        print("no duplicate stories - nothing to do")
        return 0
    for r, winner in dropped:
        print(f"  dropping: {r['headline']}")
        print(f"       for: {winner['headline']}")
    write_rows(keep)
    print(f"{len(rows)} articles -> {len(keep)} ({len(dropped)} folded away)")
    return 0


# ----------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--posts",
                    help="JSON file: a list of posts, or {items:[...]}")
    ap.add_argument("--dedupe", action="store_true",
                    help="fold existing duplicate tellings of one story into "
                         "the best one and exit; fetches nothing")
    ap.add_argument("--dry", action="store_true",
                    help="write drafts and publish nothing")
    args = ap.parse_args()

    if args.dedupe:
        return dedupe_existing()
    if not args.posts:
        print("nothing to do - pass --posts or --dedupe")
        return 0

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
    fresh, drafts, published, replaced, merged = [], [], 0, 0, 0

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
        why = gate(d, p["text"])
        if why.startswith(RETRYABLE):
            # Not a bad story - badly written. Worth one more attempt, with the
            # temperature lifted because a retry at 0 returns the same words.
            print(f"    {why} - rewriting")
            d2 = extract(p["text"], RETRY_NOTE.format(why=why), temperature=0.6)
            if d2:
                why2 = gate(d2, p["text"])
                if not why2:
                    d, why = d2, ""
                else:
                    why = f"{why2} (after a rewrite for copying)"
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
        # Already covered? Keep the better telling, not both. This is what
        # stops one goal becoming three articles and filling the carousel.
        key = story_key(row)
        twin = next((r for r in existing if story_key(r) == key), None) if key else None
        if twin:
            if richness(row) > richness(twin):
                existing[existing.index(twin)] = row
                have_slugs.add(slug)
                replaced += 1
                print(f"  replaced a thinner telling of the same story: {row['headline']}")
                print(f"    (dropped: {twin['headline']})")
            else:
                merged += 1
                log_skip(k, f"same story as {twin['slug']}")
                print(f"  already covered, skipped: {row['headline']}")
            fresh.append(k)
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

    if published or replaced:
        write_rows(existing)
    # Only now, with the rows on disk, is a post finished with. A crash before
    # this point leaves it unseen and the next run picks it up again.
    if fresh:
        save_seen(seen | set(fresh))
    print(f"{published} published, {replaced} replaced a thinner version, "
          f"{merged} folded into a story already told, {len(fresh)} posts settled")
    return 0


if __name__ == "__main__":
    sys.exit(main())
