#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One email a day listing every Irish goalscorer, once everything has finished.

This replaces the per-goal alerts that goal_alert.py used to send from inside
match_watch. Those fired the moment a goal went in, which meant a busy Saturday
produced a dozen separate emails. This sends one.

TIMING. It runs the morning after and reports on the previous day, because
"every match is over" is later than it sounds: League of Ireland games end
around 21:45 Irish time, European games nearer 22:00, and USL matches played in
the United States can finish at 03:00. Reporting yesterday from this morning is
the only way to be sure nothing is missed - and the scrapers have had all night
to fill the ratings in.

  goal_digest.py                 # yesterday, send it
  goal_digest.py --date 2026-09-02
  goal_digest.py --dry           # print it, send nothing
  goal_digest.py --force         # send again even if already sent

Secrets, the same ones goal_alert.py used:
  GMAIL_USER, GMAIL_APP_PASSWORD, and optionally ALERT_TO.
Without them it prints and sends nothing, rather than failing.
"""
import argparse
import csv
import datetime as dt
import json
import os
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STATE = HERE / "goal_digest_sent.json"
SITE = "https://footballers.ie"

# Same list goal_alert.py used, so "abroad" means the same thing in both.
LOI_COMPS = ("premier division", "first division", "league of ireland",
             "fai cup", "presidents cup", "president's cup")


def rows(rel):
    p = ROOT / rel
    if not p.exists():
        return []
    with open(p, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def is_loi(comp):
    n = (comp or "").strip().lower()
    return any(k in n for k in LOI_COMPS)


def scorers_on(date):
    """Every Irish player with at least one goal that day, richest first."""
    names = {r["slug"]: (r.get("name") or r["slug"]) for r in rows("scraper/players_list.csv") if r.get("slug")}
    meta = {r["slug"]: r for r in rows("data/api/players.csv") if r.get("slug")}
    out = []
    for r in rows("data/manual/results.csv"):
        if (r.get("date") or "")[:10] != date:
            continue
        try:
            goals = int(r.get("goals") or 0)
        except ValueError:
            continue
        if goals < 1:
            continue
        slug = (r.get("slug") or "").strip()
        m = meta.get(slug, {})
        try:
            rating = float(r.get("rating") or 0) or None
        except ValueError:
            rating = None
        out.append({
            "slug": slug,
            "name": names.get(slug, slug.replace("-", " ").title()),
            "club": (m.get("club") or "").strip(),
            "goals": goals,
            "assists": int(r.get("assists") or 0) if str(r.get("assists") or "").strip().isdigit() else 0,
            "opponent": (r.get("opponent") or "").strip(),
            "score": (r.get("score") or "").strip(),
            "comp": (r.get("competition") or "").strip(),
            "rating": rating,
            "loi": is_loi(r.get("competition")),
        })
    out.sort(key=lambda p: (-p["goals"], -(p["rating"] or 0), p["name"]))
    return out


def line_for(p):
    bits = f"{p['goals']} goal" + ("s" if p["goals"] > 1 else "")
    if p["assists"]:
        bits += f", {p['assists']} assist" + ("s" if p["assists"] > 1 else "")
    if p["rating"]:
        bits += f", rated {p['rating']:.1f}"
    where = f"{p['club']} v {p['opponent']}" if p["club"] else p["opponent"]
    if p["score"]:
        where += f" {p['score']}"
    return p["name"], bits, where, p["comp"]


def render_text(date, scorers):
    day = dt.date.fromisoformat(date).strftime("%A %d %B")
    if not scorers:
        return f"No Irish goals on {day}."
    lines = [f"Irish goalscorers - {day}", ""]
    for group, label in ((False, "Abroad"), (True, "League of Ireland")):
        block = [p for p in scorers if p["loi"] == group]
        if not block:
            continue
        lines.append(label.upper())
        for p in block:
            name, bits, where, comp = line_for(p)
            lines.append(f"  {name} - {bits}")
            lines.append(f"      {where} ({comp})")
        lines.append("")
    total = sum(p["goals"] for p in scorers)
    lines.append(f"{total} goal{'s' if total != 1 else ''} from "
                 f"{len(scorers)} player{'s' if len(scorers) != 1 else ''}.")
    return "\n".join(lines)


def esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def render_html(date, scorers):
    day = dt.date.fromisoformat(date).strftime("%A %d %B")
    if not scorers:
        body = ('<p style="color:#77837F;font-size:15px">No Irish goals yesterday.</p>')
    else:
        body = ""
        for group, label in ((False, "Abroad"), (True, "League of Ireland")):
            block = [p for p in scorers if p["loi"] == group]
            if not block:
                continue
            body += (f'<h2 style="color:#F5C518;font-size:12px;letter-spacing:.1em;'
                     f'text-transform:uppercase;margin:26px 0 10px">{esc(label)}</h2>')
            for p in block:
                name, bits, where, comp = line_for(p)
                body += (
                    f'<div style="padding:11px 0;border-bottom:1px solid #242B2C">'
                    f'<a href="{SITE}/player/{esc(p["slug"])}.html" '
                    f'style="color:#EFF3F1;text-decoration:none;font-weight:700;font-size:16px">'
                    f'{esc(name)}</a>'
                    f'<span style="color:#35D4BF;font-size:14px"> &middot; {esc(bits)}</span>'
                    f'<div style="color:#77837F;font-size:13px;margin-top:3px">'
                    f'{esc(where)} &middot; {esc(comp)}</div></div>')
    total = sum(p["goals"] for p in scorers)
    return f'''<html><body style="background:#0C0F10;color:#EFF3F1;font-family:Arial,sans-serif;margin:0;padding:24px">
<div style="max-width:600px;margin:0 auto">
  <div style="font-weight:700;font-size:19px">FOOTBALLERS<span style="color:#F5C518">.ie</span></div>
  <div style="color:#77837F;font-size:13px;margin:4px 0 8px">Irish goalscorers &mdash; {esc(day)}</div>
  {body}
  <div style="color:#77837F;font-size:12px;margin-top:26px;border-top:1px solid #242B2C;padding-top:14px">
    {total} goal{'s' if total != 1 else ''} from {len(scorers)} player{'s' if len(scorers) != 1 else ''}.
  </div>
</div></body></html>'''


def config():
    u = os.environ.get("GMAIL_USER", "").strip()
    pw = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    if not (u and pw):
        return None
    return {"user": u, "pw": pw, "to": os.environ.get("ALERT_TO", "").strip() or u}


def load_state():
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except Exception:
            pass
    return {}


def run(date, dry=False, force=False):
    state = load_state()
    if not force and state.get("last") == date:
        print(f"already sent the digest for {date}")
        return 0

    scorers = scorers_on(date)
    text = render_text(date, scorers)
    print(text)

    if dry:
        print("\n(dry run - nothing sent)")
        return 0

    cfg = config()
    if not cfg:
        print("\nGMAIL_USER/GMAIL_APP_PASSWORD not set - printed only")
        return 0

    total = sum(p["goals"] for p in scorers)
    day = dt.date.fromisoformat(date).strftime("%a %d %b")
    subject = (f"{total} Irish goal{'s' if total != 1 else ''} - {day}"
               if scorers else f"No Irish goals - {day}")

    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = cfg["user"], cfg["to"], subject
    msg.set_content(text)
    msg.add_alternative(render_html(date, scorers), subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(cfg["user"], cfg["pw"])
        s.send_message(msg)
    print(f"\nsent to {cfg['to']}")

    state["last"] = date
    STATE.write_text(json.dumps(state, indent=1))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD (default: yesterday)")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    date = a.date or (dt.date.today() - dt.timedelta(days=1)).isoformat()
    return run(date, dry=a.dry, force=a.force)


if __name__ == "__main__":
    sys.exit(main())
