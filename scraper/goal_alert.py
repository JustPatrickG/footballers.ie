"""Personal goal alerts: email the moment an Irish player scores ABROAD.

Called by match_watch on each poll of a live match. Detects goal events by
players in the roster whose scoring club is NOT a League of Ireland club, and
emails each new one exactly once (deduped by match+player+minute for the run).

Email is sent via Gmail SMTP using repo secrets, so nothing is stored here:
  GMAIL_USER          the gmail address that sends (needs an App Password)
  GMAIL_APP_PASSWORD  a Google App Password for that address
  ALERT_TO            where to send (optional; defaults to GMAIL_USER)
If those aren't set, alerts are logged only (no email), so it never crashes.
"""
import csv, os, re, smtplib, unicodedata
from email.message import EmailMessage
from pathlib import Path

HERE = Path(__file__).resolve().parent

def _key(s):   # name match: accents/punctuation-insensitive
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", s.lower())

def _clubkey(s):
    s = re.sub(r"[^a-z]", "", str(s or "").lower())
    return re.sub(r"(afc|fc)$", "", s)

_LOI = ("Shamrock Rovers", "St Patrick's Athletic", "St. Patrick's Athletic",
        "Bohemians", "Bohemian FC", "Dundalk", "Shelbourne", "Galway United",
        "Galway United FC", "Derry City", "Waterford", "Waterford FC",
        "Drogheda United", "Sligo Rovers", "Cork City", "UCD", "Bray Wanderers",
        "Longford Town", "Cobh Ramblers", "Kerry FC", "Athlone Town",
        "Wexford", "Wexford FC", "Treaty United", "Finn Harps")
_LOIK = {_clubkey(c) for c in _LOI}

def load_irish():
    idx = {}
    with open(HERE / "players_list.csv", newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            slug = (r.get("slug") or "").strip()
            nm = (r.get("name") or "").strip()
            if slug and nm:
                idx[_key(nm)] = (slug, nm)
    return idx

def config():
    u = os.environ.get("GMAIL_USER", "").strip()
    pw = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    to = os.environ.get("ALERT_TO", "").strip() or u
    return dict(user=u, pw=pw, to=to) if (u and pw and to) else None

def send(cfg, subject, body):
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = cfg["user"], cfg["to"], subject
    msg.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=25) as s:
        s.login(cfg["user"], cfg["pw"])
        s.send_message(msg)

def alert_new_goals(m, data, seen, cfg, irish, log=print):
    """m: match dict (fotmob_id, home, away). data: fotmob matchDetails json."""
    import irish_scraper as ir
    home, away = m.get("home", ""), m.get("away", "")
    try:
        events, _ = ir.parse_match_events(data, home, away)
    except Exception:
        return 0
    n = 0
    for e in events:
        if str(e.get("type", "")).lower() != "goal":
            continue
        team = (e.get("team") or "").strip()
        if _clubkey(team) in _LOIK:
            continue                      # League of Ireland goal — not "abroad"
        hit = irish.get(_key(e.get("player")))
        if not hit:
            continue                      # not one of ours
        slug, nm = hit
        key = (str(m.get("fotmob_id")), _key(e.get("player")), str(e.get("minute")))
        if key in seen:
            continue
        seen.add(key)
        opp = away if _clubkey(team) == _clubkey(home) else home
        minute = e.get("minute", "")
        subj = f"GOAL: {nm} ({team}) {minute}'"
        body = (f"{nm} has scored for {team} vs {opp} ({minute}').\n\n"
                f"Match: {home} v {away}\n"
                f"Profile: https://footballers.ie/player/{slug}.html\n")
        if cfg:
            try:
                send(cfg, subj, body)
                log(f"  ALERT emailed: {subj}")
            except Exception as ex:
                log(f"  email failed ({ex}); will retry next goal")
        else:
            log(f"  ALERT (no email configured): {subj}")
        n += 1
    return n
