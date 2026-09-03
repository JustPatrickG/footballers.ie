#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Assemble and send the twice-weekly newsletter, built entirely from data
already on disk - the match reports auto_reports.py generates, plus
upcoming fixtures and current injuries. No paid API of any kind.

Two editions, matching newsletter.html's own copy:
  monday  - how everyone got on at the weekend, what's on this week
  friday  - the midweek recap, who's playing this weekend, injuries

SENDING. Gmail SMTP, same free mechanism goal_alert.py already uses -
nothing new to pay for. Recipients come from the private subscribers repo
(the same one api/subscribe.js writes to) via the GitHub Contents API, so a
second read-only token is needed for THAT repo specifically - the
Actions-provided GITHUB_TOKEN only reaches this one.

Secrets/variables (all optional - sends only once every one of these is
set; otherwise prints what it would have sent and stops):
  GMAIL_USER, GMAIL_APP_PASSWORD      - same as goal_alert.py
  SUBSCRIBERS_REPO                    - e.g. JustPatrickG/footballers-private
  SUBSCRIBERS_READ_TOKEN              - a PAT with read access to that repo
                                         (GITHUB_TOKEN can't reach another repo)

STATE. scraper/newsletter_sent.json remembers the last (edition, date)
combination sent, so a workflow that fires twice in a morning - or is
re-run by hand - never double-sends. Not secret, safe to commit.

Usage
  newsletter.py --edition monday            # send (if configured)
  newsletter.py --edition friday --dry       # print the issue, send nothing
  newsletter.py --edition monday --force     # ignore the "already sent today" guard
"""
import argparse
import base64
import csv
import datetime as dt
import json
import os
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
# Reuse auto_reports' own filter rather than keeping a second copy of the
# rules in step - it is what decides which matches become reports, so the
# newsletter previewing a fixture it will never report on would be odd.
from auto_reports import BLOCK, norm, youth_side   # noqa: E402


def is_senior_comp(comp):
    """True for competitions auto_reports would write a report about:
       no friendlies, no LOI First Division, no youth or reserve football."""
    n = norm(comp)
    return not any(b in n for b in BLOCK)

API = ROOT / "data" / "api"
MANUAL = ROOT / "data" / "manual"
STATE = HERE / "newsletter_sent.json"
SITE_URL = "https://footballers.ie"
BCC_BATCH = 80   # keep each send well under Gmail's per-message recipient cap


# ------------------------------------------------------------- content

def load_articles():
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


def match_reports_since(cutoff_date):
    out = [a for a in load_articles()
          if (a.get("slug") or "").startswith("report-")
          and (a.get("date") or "") >= cutoff_date]

    def weight_of(a):
        try:
            return float(a.get("weight") or 0)
        except ValueError:
            return 0.0
    out.sort(key=weight_of, reverse=True)
    return out


def upcoming_fixtures(days_ahead):
    path = API / "matches.csv"
    if not path.exists():
        return []
    today = dt.date.today().isoformat()
    end = (dt.date.today() + dt.timedelta(days=days_ahead)).isoformat()
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        for m in csv.DictReader(f):
            date = (m.get("kickoff") or "")[:10]
            if not date or not (today <= date <= end):
                continue
            if m.get("status") == "ft":
                continue
            if not (m.get("players") or "").strip():
                continue           # no Irish player involved
            if not is_senior_comp(m.get("competition")):
                continue
            if youth_side(m.get("home")) or youth_side(m.get("away")):
                continue
            out.append(m)
    out.sort(key=lambda m: m.get("kickoff", ""))
    return out


def current_injuries():
    path = MANUAL / "players.csv"
    if not path.exists():
        return []
    out = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            note = (r.get("injury") or "").strip()
            if note:
                out.append((r.get("name") or r.get("slug") or "", note))
    out.sort(key=lambda x: x[0])
    return out


def roster_names():
    out = {}
    path = HERE / "players_list.csv"
    if path.exists():
        with open(path, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                slug = (r.get("slug") or "").strip()
                if slug:
                    out[slug] = (r.get("name") or slug).strip()
    return out


# -------------------------------------------------------------- render

def esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
           .replace(">", "&gt;"))


def render_html(edition, reports, fixtures, injuries, names):
    today_label = dt.date.today().strftime("%A %-d %B")
    title = ("Monday round-up" if edition == "monday" else "Friday preview")

    report_html = ""
    for a in reports[:20]:
        headline = esc(a.get("headline", ""))
        standfirst = esc(a.get("standfirst", ""))
        link = f"{SITE_URL}/news/{a['slug']}.html"
        report_html += (
            f'<tr><td style="padding:14px 0;border-bottom:1px solid #242B2C">'
            f'<a href="{link}" style="color:#EFF3F1;text-decoration:none;'
            f'font-weight:700;font-size:16px">{headline}</a>'
            f'<div style="color:#77837F;font-size:13px;margin-top:4px">{standfirst}</div>'
            f'</td></tr>')
    if not report_html:
        report_html = ('<tr><td style="padding:14px 0;color:#77837F">'
                       'Nothing to report - quiet few days.</td></tr>')

    fx_html = ""
    for m in fixtures[:25]:
        date = (m.get("kickoff") or "")[:10]
        try:
            day = dt.date.fromisoformat(date).strftime("%a %-d %b")
        except ValueError:
            day = date
        players = [names.get(s, s) for s in (m.get("players") or "").split(";") if s.strip()]
        who = ", ".join(players[:6]) + (f" +{len(players)-6} more" if len(players) > 6 else "")
        fx_html += (
            f'<tr><td style="padding:10px 0;border-bottom:1px solid #242B2C;font-size:14px">'
            f'<b>{esc(day)}</b> — {esc(m.get("home",""))} v {esc(m.get("away",""))} '
            f'<span style="color:#77837F">({esc(m.get("competition",""))})</span>'
            f'<div style="color:#35D4BF;font-size:13px;margin-top:2px">{esc(who)}</div>'
            f'</td></tr>')
    if not fx_html:
        fx_html = '<tr><td style="padding:10px 0;color:#77837F">Nothing scheduled in this window.</td></tr>'

    injury_html = ""
    if edition == "friday" and injuries:
        rows = "".join(f'<li>{esc(n)} — {esc(note)}</li>' for n, note in injuries[:15])
        injury_html = (f'<h2 style="color:#F5C518;font-size:13px;letter-spacing:.1em;'
                       f'text-transform:uppercase;margin:28px 0 8px">Injury watch</h2>'
                       f'<ul style="color:#EFF3F1;font-size:14px;line-height:1.7;padding-left:18px">{rows}</ul>')

    return f'''<html><body style="background:#0C0F10;color:#EFF3F1;font-family:Arial,sans-serif;margin:0;padding:24px">
<div style="max-width:600px;margin:0 auto">
  <div style="font-weight:700;font-size:19px;margin-bottom:4px">FOOTBALLERS<span style="color:#F5C518">.ie</span></div>
  <div style="color:#77837F;font-size:13px;margin-bottom:24px">{esc(title)} — {esc(today_label)}</div>
  <h2 style="color:#F5C518;font-size:13px;letter-spacing:.1em;text-transform:uppercase;margin:0 0 8px">
    {"How they got on" if edition=="monday" else "Midweek recap"}</h2>
  <table width="100%" cellpadding="0" cellspacing="0">{report_html}</table>
  <h2 style="color:#F5C518;font-size:13px;letter-spacing:.1em;text-transform:uppercase;margin:28px 0 8px">
    {"This week" if edition=="monday" else "This weekend"}</h2>
  <table width="100%" cellpadding="0" cellspacing="0">{fx_html}</table>
  {injury_html}
  <div style="color:#77837F;font-size:12px;margin-top:32px;border-top:1px solid #242B2C;padding-top:16px">
    You're getting this because you signed up at footballers.ie.
    <a href="{SITE_URL}/newsletter.html" style="color:#77837F">Manage your subscription</a>.
  </div>
</div></body></html>'''


def render_text(edition, reports, fixtures):
    lines = [f"FOOTBALLERS.ie — {'Monday round-up' if edition=='monday' else 'Friday preview'}", ""]
    for a in reports[:20]:
        lines.append(f"- {a.get('headline','')}")
        lines.append(f"  {SITE_URL}/news/{a['slug']}.html")
    lines.append("")
    for m in fixtures[:25]:
        lines.append(f"- {(m.get('kickoff') or '')[:10]}: {m.get('home','')} v "
                     f"{m.get('away','')} ({m.get('competition','')})")
    return "\n".join(lines)


# ------------------------------------------------------------ sending

def gmail_config():
    u = os.environ.get("GMAIL_USER", "").strip()
    pw = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    return dict(user=u, pw=pw) if (u and pw) else None


def fetch_subscribers():
    repo = os.environ.get("SUBSCRIBERS_REPO", "").strip()
    token = os.environ.get("SUBSCRIBERS_READ_TOKEN", "").strip()
    if not (repo and token):
        return None
    import urllib.request
    url = f"https://api.github.com/repos/{repo}/contents/subscribers.csv"
    req = urllib.request.Request(url, headers={
        "Authorization": f"token {token}", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    text = base64.b64decode(data["content"]).decode("utf-8")
    import io
    emails = []
    for row in csv.DictReader(io.StringIO(text)):
        e = (row.get("email") or "").strip()
        if e:
            emails.append(e)
    return emails


def send(cfg, subject, html, text, bcc_list):
    for i in range(0, len(bcc_list), BCC_BATCH):
        batch = bcc_list[i:i + BCC_BATCH]
        msg = MIMEMultipart("alternative")
        msg["From"], msg["Subject"] = cfg["user"], subject
        msg["To"] = cfg["user"]      # send-to-self; real recipients go only in
        # send_message's to_addrs below, never in a header - a "Bcc:" header
        # would print every subscriber's address into a field every other
        # recipient can read, which defeats the entire point of BCC.
        msg.attach(MIMEText(text, "plain"))
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
            s.login(cfg["user"], cfg["pw"])
            s.send_message(msg, to_addrs=[cfg["user"]] + batch)
        print(f"  sent to {len(batch)} recipients (batch {i // BCC_BATCH + 1})")


def load_state():
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except Exception:
            pass
    return {}


def run(edition, dry=False, force=False):
    today = dt.date.today().isoformat()
    state = load_state()
    if not force and state.get(edition) == today:
        print(f"already sent the {edition} edition today - nothing to do")
        return 0

    lookback = 4 if edition == "monday" else 3
    ahead = 6 if edition == "monday" else 4
    cutoff = (dt.date.today() - dt.timedelta(days=lookback)).isoformat()

    names = roster_names()
    reports = match_reports_since(cutoff)
    fixtures = upcoming_fixtures(ahead)
    injuries = current_injuries()

    html = render_html(edition, reports, fixtures, injuries, names)
    text = render_text(edition, reports, fixtures)
    subject = (f"{'Monday' if edition=='monday' else 'Friday'} round-up — "
              f"{len(reports)} Irish stor{'y' if len(reports)==1 else 'ies'}")

    print(f"{edition} edition: {len(reports)} match report(s), {len(fixtures)} fixture(s), "
         f"{len(injuries)} injur{'y' if len(injuries)==1 else 'ies'}")

    if dry:
        print("--- text preview ---")
        print(text[:2000])
        return 0

    cfg = gmail_config()
    if not cfg:
        print("GMAIL_USER/GMAIL_APP_PASSWORD not set - not sending")
        return 0
    subscribers = fetch_subscribers()
    if subscribers is None:
        print("SUBSCRIBERS_REPO/SUBSCRIBERS_READ_TOKEN not set - not sending")
        return 0
    if not subscribers:
        print("no subscribers on file")
        return 0

    send(cfg, subject, html, text, subscribers)
    state[edition] = today
    STATE.write_text(json.dumps(state, indent=1))
    print(f"{edition} edition sent to {len(subscribers)} subscriber(s)")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--edition", choices=("monday", "friday"), required=True)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="ignore the already-sent-today guard")
    args = ap.parse_args()
    return run(args.edition, dry=args.dry, force=args.force)


if __name__ == "__main__":
    sys.exit(main())
