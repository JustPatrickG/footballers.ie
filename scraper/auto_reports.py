#!/usr/bin/env python3
"""Automatic match-report articles.

Scans recent results and writes one article per match in which an Irish
player either scored or rated 7.5+, into data/api/articles.csv (the
machine layer - anything hand-written in data/manual/articles.csv with the
same slug overrules it). Each article carries a `weight` so the site can
keep the big games in front: a Premier League masterclass outranks a goal
in the USL however fresh the USL one is.

Rules: League of Ireland Premier Division qualifies, First Division does
not; youth sides and youth competitions never qualify; friendlies never
qualify. All facts come from the same scraped data the site displays.

    python3 scraper/auto_reports.py            # last 3 days
    python3 scraper/auto_reports.py --days N
    python3 scraper/auto_reports.py --dry      # print, write nothing
"""
import argparse
import csv
import datetime as dt
import os
import re
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
AUTHOR = "Jack Deane"

ARTICLE_COLUMNS = ["slug", "date", "tag", "headline", "standfirst", "body",
                   "author", "image", "player_slug", "expires",
                   "use_player_photo", "partner", "partner_url", "weight"]

RATING_BAR = 7.5
COMP_TIER = {
    "champions league": 100, "premier league 2": 12, "premier league": 96,
    "laliga": 92, "la liga": 92, "serie a": 92, "bundesliga": 92,
    "ligue 1": 92, "europa league": 86, "conference league": 76,
    "eredivisie": 72, "liga portugal": 72, "super lig": 70, "süper lig": 70,
    "premiership": 68, "championship": 68, "major league soccer": 58,
    "jupiler pro league": 62, "serie b": 56, "premier division": 62,
    "fai cup": 60, "league one": 48, "efl cup": 54, "fa cup": 54,
    "nb i": 44, "2. liga": 44, "ligue 2": 46, "league two": 40,
    "usl championship": 40, "national league": 32,
}
COMP_DEFAULT = 30

BLOCK = ("friendl", "first division",           # LOI FD + friendlies out
         "u17", "u18", "u19", "u21", "u-17", "u-18", "u-19", "u-21",
         "youth", "academy", "premier league 2", "reserve")


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", s.lower())).strip()


def slugify(name):
    s = unicodedata.normalize("NFKD", str(name or "")).encode(
        "ascii", "ignore").decode()
    s = s.replace("'", "")
    return re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()


def rows(path):
    p = os.path.join(ROOT, path)
    if not os.path.exists(p):
        return []
    with open(p, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def comp_weight(comp):
    n = norm(comp)
    for key in sorted(COMP_TIER, key=len, reverse=True):
        if key in n:
            v = COMP_TIER[key]
            return int(v * 0.82) if "qualification" in n or "qualifying" in n else v
    return COMP_DEFAULT


def youth_side(name):
    return bool(re.search(r"\bu-?\d{2}\b|\byouth\b|\bacademy\b",
                          str(name or ""), re.I))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    today = dt.date.today()
    lo = (today - dt.timedelta(days=args.days)).isoformat()

    names = {r["slug"]: r.get("name", "") for r in rows("scraper/players_list.csv")}
    pmeta = {r["slug"]: r for r in rows("data/api/players.csv")}
    have = {(r.get("slug") or "").strip().lower()
            for r in rows("data/manual/articles.csv") + rows("data/api/articles.csv")}

    # ---- qualifying performances, grouped per (date, match) --------------
    games = {}
    for r in rows("data/manual/results.csv"):
        d = (r.get("date") or "")[:10]
        if not (lo <= d <= today.isoformat()):
            continue
        comp = r.get("competition") or ""
        if any(b in norm(comp) for b in BLOCK):
            continue
        slug = r.get("slug") or ""
        meta = pmeta.get(slug, {})
        club = meta.get("club") or ""
        if youth_side(club) or youth_side(r.get("opponent")):
            continue
        try:
            goals = int(r.get("goals") or 0)
            assists = int(r.get("assists") or 0)
            mins = int(r.get("minutes") or 0)
        except ValueError:
            goals = assists = mins = 0
        try:
            rating = float(r.get("rating") or 0)
        except ValueError:
            rating = 0.0
        if goals < 1 and rating < RATING_BAR:
            continue
        if not (r.get("score") or "").strip():
            continue                      # no final score, no report
        opp = r.get("opponent") or ""
        key = (d, frozenset((norm(club), norm(opp))))
        g = games.setdefault(key, {"date": d, "comp": comp, "players": []})
        g["players"].append({
            "slug": slug, "name": names.get(slug, slug.replace("-", " ").title()),
            "club": club, "opp": opp, "score": r.get("score", ""),
            "goals": goals, "assists": assists, "mins": mins,
            "rating": rating, "caps": int(meta.get("senior_caps") or 0),
        })

    out = []
    for key, g in games.items():
        ps = sorted(g["players"],
                    key=lambda p: (-p["goals"], -p["rating"]))
        star = ps[0]
        club, opp, sc = star["club"], star["opp"], star["score"]
        m = re.match(r"(\d+)\s*-\s*(\d+)", sc)
        own, oth = (m.group(1), m.group(2)) if m else ("", "")
        try:
            res = ("beat" if int(own) > int(oth)
                   else ("lose to" if int(own) < int(oth) else "draw with"))
        except ValueError:
            res = "draw with"

        # ---- headline -------------------------------------------------
        # a multi-goal star carries the headline alone; otherwise a pair share it
        if star["goals"] >= 2:
            lead = star["name"]
            deed = f"hits {'two' if star['goals'] == 2 else 'a hat-trick' if star['goals'] == 3 else str(star['goals'])}"
        elif len(ps) > 1:
            lead = f'{star["name"]} and {ps[1]["name"]}'
            deed = "score" if star["goals"] else "shine"
        else:
            lead = star["name"]
            deed = "scores" if star["goals"] else "shines"
        if res == "beat":
            headline = f"{lead} {deed} as {club} beat {opp} {own}-{oth}"
        elif res == "lose to":
            headline = (f"{lead} {deed} but {club} fall to {opp} {oth}-{own}"
                        if star["goals"] else
                        f"{lead} {deed} despite {club}'s {own}-{oth} defeat to {opp}")
        else:
            headline = f"{lead} {deed} as {club} draw {own}-{oth} with {opp}"

        bits = []
        for p in ps:
            what = []
            if p["goals"]:
                what.append(f"{p['goals']} goal{'s' if p['goals'] > 1 else ''}")
            if p["assists"]:
                what.append(f"{p['assists']} assist{'s' if p['assists'] > 1 else ''}")
            if p["rating"]:
                what.append(f"rated {p['rating']:.1f}")
            bits.append(f"{p['name']} ({', '.join(what)})")
        standfirst = f"{g['comp']}: " + "; ".join(bits) + "."

        # ---- body ------------------------------------------------------
        date_nice = dt.date.fromisoformat(g["date"]).strftime("%A %d %B")
        lines = [f"{club} {res} {opp} {own}-{oth} in the {g['comp']} "
                 f"on {date_nice}.", ""]
        lines.append("## The Irish on the pitch")
        for p in ps:
            season = pmeta.get(p["slug"], {})
            did = []
            if p["goals"]:
                did.append(f"scored {p['goals']}")
            if p["assists"]:
                did.append(f"set up {p['assists']}")
            did.append(f"played {p['mins']} minutes")
            if p["rating"]:
                did.append(f"finished on a {p['rating']:.1f} match rating")
            season_line = ""
            if season.get("s_apps"):
                season_line = (f" That leaves the season at "
                               f"{season.get('s_goals') or 0} goal"
                               f"{'s' if str(season.get('s_goals')) != '1' else ''}"
                               f" and {season.get('s_assists') or 0} assist"
                               f"{'s' if str(season.get('s_assists')) != '1' else ''}"
                               f" in {season.get('s_apps')} appearances.")
            lines.append(f"- [{p['name']}](/player/{p['slug']}.html) "
                         f"{', '.join(did)}.{season_line}")
        body = "\n".join(lines)

        weight = (comp_weight(g["comp"])
                  + sum(p["goals"] for p in ps) * 25
                  + sum(max(0.0, p["rating"] - RATING_BAR) * 30 for p in ps)
                  + min(star["caps"], 50))
        slug = f"report-{g['date']}-{slugify(club)}-v-{slugify(opp)}"
        if slug.lower() in have:
            continue
        ko = f"{g['date']}T12:00"
        expires = (dt.datetime.fromisoformat(ko)
                   + dt.timedelta(hours=60)).strftime("%Y-%m-%dT%H:%M")
        out.append({
            "slug": slug, "date": g["date"], "tag": "",
            "headline": headline, "standfirst": standfirst, "body": body,
            "author": AUTHOR, "image": "", "player_slug": star["slug"],
            "expires": expires, "use_player_photo": "yes",
            "partner": "", "partner_url": "", "weight": str(int(weight)),
        })

    out.sort(key=lambda a: (a["date"], float(a["weight"])), reverse=True)
    if args.dry:
        for a in out:
            print(f"[{a['weight']:>4}] {a['date']}  {a['headline']}")
        print(f"{len(out)} report(s) (dry run)")
        return
    if not out:
        print("no qualifying performances - nothing written")
        return
    existing = rows("data/api/articles.csv")
    path = os.path.join(ROOT, "data/api/articles.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=ARTICLE_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for a in out + existing:
            w.writerow({c: a.get(c, "") for c in ARTICLE_COLUMNS})
    print(f"wrote {len(out)} report(s), kept {len(existing)} existing")


if __name__ == "__main__":
    main()
