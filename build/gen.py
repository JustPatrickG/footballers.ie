# -*- coding: utf-8 -*-
import os, sys, re, html as H, json, csv, math
import unicodedata
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data")
EMPTY_MS = '<div class="emptystate" style="display:block">Nothing close right now.</div>'

# ---- config ----
SITE_URL   = "https://footballers.ie"
SEASON     = "2026/27"
MATCHWEEK  = ""   # leave blank to work it out from the fixtures
SAMPLE_DATA = False   # set False once every figure on the site is real

# Newsletter: paste your provider's form-action URL here (Buttondown, Beehiiv,
# Mailchimp, Kit — they all give you one). Until then the form shows a notice.
NEWSLETTER_ACTION = ""      # e.g. "https://buttondown.email/api/emails/embed-subscribe/footballers"
NEWSLETTER_FIELD  = "email" # Buttondown/Beehiiv use "email"; Mailchimp uses "EMAIL"


# ---- Transfermarkt bio/contract layer (data/api/tm.csv) ----
# Manual, refreshed every month or two — treat as possibly stale.
# Every field can be blank; blank means unknown.

def _tm_date(s):
    """DD/MM/YYYY -> (iso, 'June 2027'). Returns ('','') if unparseable."""
    s = (s or "").strip()
    if not s: return "", ""
    import datetime
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            d = datetime.datetime.strptime(s, fmt)
            return d.strftime("%Y-%m-%d"), d.strftime("%B %Y")
        except ValueError:
            continue
    return "", s

def _tm_text(v):
    """Agent / club must be words. The TM scraper sometimes drops a date in here — treat that as blank."""
    v = (v or "").strip()
    if not v or re.fullmatch(r"[\d\s./:€£$k-]+", v, re.I): return ""
    return v

def _age_from_dob(dob):
    """A date of birth is checkable; an age scraped off a page is not."""
    import datetime
    try:
        b = datetime.datetime.strptime((dob or "").strip(), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return 0
    t = datetime.date.today()
    a = t.year - b.year - ((t.month, t.day) < (b.month, b.day))
    return a if 5 <= a <= 70 else 0

def _sane_age(age):
    """The feed's age field sometimes carries a year (2025) or plain nonsense.
       A professional footballer is not 54 and definitely not 2025."""
    a = _int(age)
    return a if 14 <= a <= 50 else 0

def _tm_height(cm):
    cm = (cm or "").strip()
    if not cm.replace(".", "").isdigit(): return ""
    try: n = int(float(cm))
    except ValueError: return ""
    inches = round(n / 2.54)
    return f"{n}cm · {inches // 12}ft {inches % 12}in"

def _tm_nations(s):
    return [x.strip() for x in (s or "").split("|") if x.strip()]

def _rows(path):
    full = os.path.join(DATA, path)
    if not os.path.exists(full): return []
    with open(full, newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if any((v or "").strip() for v in r.values())]

def _int(v, d=0):
    try: return int(str(v).strip())
    except: return d

def _yes(v):
    return str(v or "").strip().lower() in ("y","yes","true","1")

MISMATCHED = {}          # slug -> the name the stats feed had for them
SUSPECT_PHOTOS = set()   # ids that were wrong once: the saved photo is theirs
_NICK = {"mikey": ["michael"], "mike": ["michael"], "robbie": ["robert"],
         "tom": ["thomas"], "tommy": ["thomas"], "danny": ["daniel"],
         "dan": ["daniel"], "joe": ["joseph"], "jimmy": ["james"],
         "jamie": ["james"], "jim": ["james"], "will": ["william"],
         "billy": ["william"], "willie": ["william"], "harry": ["harold", "henry"],
         "paddy": ["patrick"], "pat": ["patrick"], "ollie": ["oliver"],
         "chris": ["christopher"], "tayo": ["omotayo"], "vinnie": ["vincent"],
         "nicky": ["nicholas"], "stevie": ["stephen", "steven"],
         "andy": ["andrew"], "matty": ["matthew"], "ben": ["benjamin"],
         "sam": ["samuel"], "alex": ["alexander"], "charlie": ["charles"],
         "freddie": ["frederick"], "ted": ["edward"], "eddie": ["edward"]}

def _same_person(a, b):
    """Is the stats feed's name the same footballer as ours? Spelling and
       short names vary a lot - Mikey/Michael, Tayo/Omotayo, Umeh/Umeh-Chibueze -
       so this is deliberately generous. What it will not accept is a
       different first name on the same surname: that is how Desmond Armstrong
       ended up with Harrison Armstrong's face, club and season."""
    def words(n):
        n = unicodedata.normalize("NFKD", str(n or "")).encode("ascii", "ignore").decode()
        return [w for w in re.sub(r"[^a-z\- ]", " ", n.lower()).split() if w]
    A, B = words(a), words(b)
    if not A or not B: return True               # nothing to check against
    if A == B: return True

    # surnames: every word after the first, so a double-barrel still matches
    def parts(ws): return {p for w in ws for p in w.split("-") if p}
    sa = parts(A[1:]) or parts([A[-1]])
    sb = parts(B[1:]) or parts([B[-1]])
    if not (sa & sb or any(x in y or y in x for x in sa for y in sb)):
        return False                             # different surname

    fa, fb = A[0], B[0]
    if fa == fb: return True
    if fa.startswith(fb) or fb.startswith(fa): return True
    if fb in _NICK.get(fa, []) or fa in _NICK.get(fb, []): return True
    fap, fbp = parts([fa]), parts([fb])
    if fap & fbp: return True                            # Raphael-Pijus / Pijus
    if fa in parts(B) or fb in parts(A): return True     # middle name used first
    return False

def _fotmob_names():
    """slug -> the name FotMob has for that id, from the scraper's id cache.
       An id set by hand has already been checked by a person, so it is left
       alone: put 'id set by hand' in the note column to override this."""
    out = {}
    path = os.path.join(HERE, "..", "scraper", "fotmob_ids.csv")
    if not os.path.exists(path): return out
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if not r.get("slug"):
                continue
            note = (r.get("note") or "").lower()
            if "wrong person" in note or "not used" in note or "by hand" in note:
                SUSPECT_PHOTOS.add(r["slug"])   # whatever is on disk was pulled
                                                # with the id that was wrong
            if "by hand" in note:
                continue                     # a person has already checked this
            if "wrong person" in note or "not used" in note:
                # id was cleared because it belonged to someone else; the old
                # scraped row may still be sitting in players.csv, so make sure
                # the check fires on it
                out[r["slug"]] = "-- a different player --"
                continue
            if (r.get("fotmob_id") or "").strip():
                out[r["slug"]] = (r.get("fotmob_name") or "").strip()
    return out

def _merge_players():
    """Three layers, lowest first:
         scraper/players_list.csv  — the roster (name, club, position)
         data/api/players.csv      — scraped stats, keyed on slug
         data/manual/players.csv   — your edits, always win
       A manual row with locked=yes ignores the scraped data entirely."""
    roster = {}
    roster_path = os.path.join(HERE, "..", "scraper", "players_list.csv")
    if os.path.exists(roster_path):
        with open(roster_path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("slug"): roster[r["slug"]] = r

    # Fields the scraper is authoritative on — these overwrite the hand-typed
    # roster, because clubs and leagues change and the roster goes stale.
    SCRAPED_WINS = ("club", "league", "age", "foot", "avg_rating")

    # Before trusting a scraped row, check it belongs to this player. A wrong
    # id means somebody else's photo, club and season on the page, which is
    # worse than having no stats at all - so a failed check drops the row and
    # the player falls back to the roster and Transfermarkt.
    fm_names = _fotmob_names()

    api = {}
    for r in _rows("api/players.csv"):
        if not r.get("slug"): continue
        ours = (roster.get(r["slug"], {}) or {}).get("name", "")
        theirs = fm_names.get(r["slug"], "")
        if ours and theirs and not _same_person(theirs, ours):
            MISMATCHED[r["slug"]] = theirs
            continue
        base = dict(roster.get(r["slug"], {}))          # name/pos from the roster
        for k, v in r.items():
            if (v or "").strip():
                base[k] = v
            elif k in SCRAPED_WINS and k in base:
                pass                                     # blank scrape: keep what we had
        api[r["slug"]] = base
    for slug, r in roster.items():                       # roster-only players still show
        api.setdefault(slug, dict(r))
    man = {r["slug"]: r for r in _rows("manual/players.csv") if r.get("slug")}
    out = {}
    for slug in set(api) | set(man):
        a, m = api.get(slug, {}), man.get(slug, {})
        if str(m.get("locked","")).strip().lower() in ("y","yes","true","1"):
            out[slug] = dict(a, **{k:v for k,v in m.items() if (v or "").strip()}) if not a else dict(m)
            merged = dict(m)
        else:
            merged = dict(a)
            for k, v in m.items():
                if not (v or "").strip():
                    continue
                # the scraper is the source of truth for club/league — a stale
                # hand-typed value shouldn't override a live one
                if k in ("club", "league") and (a.get(k) or "").strip():
                    continue
                merged[k] = v
        merged["slug"] = slug
        merged["_source"] = "manual" if slug in man and slug not in api else ("api+manual" if slug in man else "api")
        out[slug] = merged

    # --- League of Ireland: one canonical club name + division -----------
    # The feeds disagree on both league ("Premier Division"/"First Division"/
    # "League of Ireland"/blank) and club spelling ("Bohemians" vs "Bohemian
    # FC", "Wexford" vs "Wexford FC"), which split one league into five and one
    # club into two. The club decides the division; one spelling per club.
    PREM = "League of Ireland Premier Division"
    FIRST = "League of Ireland First Division"
    _CANON_CLUBS = {
        PREM: {
            "Shamrock Rovers": [], "Dundalk": [], "Shelbourne": ["Shels"],
            "Derry City": [], "Sligo Rovers": [], "Drogheda United": ["Drogheda"],
            "St. Patrick's Athletic": ["St Patrick's Athletic",
                                       "Saint Patrick's Athletic", "St Pats"],
            "Bohemian FC": ["Bohemians", "Bohs"],
            "Galway United FC": ["Galway United"],
            "Waterford FC": ["Waterford"],
        },
        FIRST: {
            "Cork City": [], "UCD": ["UCD AFC"], "Bray Wanderers": [],
            "Longford Town": ["Longford"], "Cobh Ramblers": ["Cobh"],
            "Kerry FC": ["Kerry"], "Athlone Town": ["Athlone"],
            "Wexford FC": ["Wexford"], "Treaty United": ["Treaty"],
            "Finn Harps": ["Finn Harps FC"],
        },
    }
    def _lk(s):
        s = re.sub(r"[^a-z]", "", (s or "").lower())
        return re.sub(r"(afc|fc)$", "", s)
    _LOI = {}   # club key -> (division, canonical club name)
    for _div, _clubs in _CANON_CLUBS.items():
        for _canon, _variants in _clubs.items():
            for _name in (_canon, *_variants):
                _LOI[_lk(_name)] = (_div, _canon)
    for _p in out.values():
        _hit = _LOI.get(_lk(_p.get("club", "")))
        if _hit:
            _p["league"], _p["club"] = _hit[0], _hit[1]
            _p["tier"] = "loi"
        else:
            if (_p.get("league") or "").strip().lower() == "league of ireland":
                # generic "League of Ireland" on a club we don't recognise as
                # LOI (youth sides, foreign academies) is stale — don't trust
                # it to place the player in Ireland.
                _p["league"] = ""
            if (_p.get("tier") or "").strip() == "loi":
                # tagged League of Ireland but not at an LOI club (they moved
                # abroad) — they belong with the players abroad, not the LOI.
                _p["tier"] = "abroad-lower"
    return out

def _merge_rows(name, key_fields):
    """Manual rows replace API rows with the same key; manual-only rows are appended."""
    api = _rows(f"api/{name}")
    man = _rows(f"manual/{name}")
    keyed = {tuple(r.get(k,"") for k in key_fields): r for r in api}
    for r in man:
        keyed[tuple(r.get(k,"") for k in key_fields)] = r
    return list(keyed.values())

def _first_last(slug):
    """'will-fitzgerald' -> ('will', 'fitzgerald'). Surname is everything after
       the first hyphen, so 'john-ross-wilson' keeps 'ross-wilson' as the surname."""
    parts = slug.split("-", 1)
    return (parts[0], parts[1]) if len(parts) == 2 else (slug, "")

def _nickname_of(a, b):
    """True when one first name is a short form of the other: will/william,
       dan/danny, ed/edward, tom/tommy. Prefix match only — never jamie/tom."""
    if a == b: return False
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return len(short) >= 2 and long_.startswith(short)

def _dedupe_players(players, manual_rows):
    """The roster carries some players twice under a full name and a short one
       (will-fitzgerald / william-fitzgerald). Same club, same surname, one first
       name a short form of the other, and the same season numbers = one person.
       Keep the better-sourced slug; return {dropped_slug: kept_slug} so match
       squads can be remapped."""
    by_key = {}
    for p in players:
        first, last = _first_last(p["slug"])
        by_key.setdefault(((p.get("club") or "").strip().lower(), last), []).append((first, p))

    def _score(p):
        # a hand-edited row wins, then a Transfermarkt row, then more history
        return (1 if p["slug"] in manual_rows else 0,
                1 if p.get("tm") else 0,
                len(p.get("results") or []),
                len(p.get("fixtures") or []))

    alias, dropped = {}, set()
    for group in by_key.values():
        if len(group) < 2: continue
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                (fa, pa), (fb, pb) = group[i], group[j]
                if pa["slug"] in dropped or pb["slug"] in dropped: continue
                if not _nickname_of(fa, fb): continue
                sa, sb = pa["season"], pb["season"]
                same = (sa["mins"], sa["ap"], sa["g"]) == (sb["mins"], sb["ap"], sb["g"])
                blank = not (sa["mins"] or sa["ap"]) or not (sb["mins"] or sb["ap"])
                if not (same or blank): continue          # different people, same surname
                keep, drop = (pa, pb) if _score(pa) >= _score(pb) else (pb, pa)
                alias[drop["slug"]] = keep["slug"]
                dropped.add(drop["slug"])
                # the loser may hold history the winner is missing
                for fld in ("results", "fixtures"):
                    if len(drop.get(fld) or []) > len(keep.get(fld) or []):
                        keep[fld] = drop[fld]
                for fld in ("photo", "tm", "rating", "born", "foot"):
                    if not keep.get(fld) and drop.get(fld): keep[fld] = drop[fld]
    if dropped:
        print(f"  merged {len(dropped)} duplicate player{'s' if len(dropped)!=1 else ''}: "
              + ", ".join(f"{d}→{alias[d]}" for d in sorted(dropped)))
    return [p for p in players if p["slug"] not in dropped], alias

EVENTS = {}
LINEUPS = {}
ALIAS = {}
TRANSFERS = {}
FMIDS = {}

# Transfermarkt writes club names its own way — "St. Pat's", "Man Utd U21",
# "Cambridge Utd.", "Aberdeen FC" — while the badge map is keyed on FotMob's
# names. This is the bridge: a normaliser that strips the punctuation, the
# youth suffix and the FC/AFC noise, plus a short table for the ones that are
# simply different words. Anything still unmatched keeps the grey crest; a
# wrong badge is worse than no badge.
NON_CLUBS = {"without club", "retired", "career break", "unknown", "no club",
             "free agent", "---", "-", "?"}

CLUB_ALIASES = {
    # League of Ireland, as Transfermarkt shortens them
    "pats": "St. Patrick's Athletic", "st pats": "St. Patrick's Athletic",
    "bohemians": "Bohemian FC", "bohs": "Bohemian FC",
    "shels": "Shelbourne", "drogs": "Drogheda United",
    "wfc": "Waterford FC", "waterford united": "Waterford FC",
    "rovers": "Shamrock Rovers", "hoops": "Shamrock Rovers",
    "sligo": "Sligo Rovers", "cork": "Cork City",
    "galway": "Galway United FC", "galway united": "Galway United FC",
    "bray": "Bray Wanderers", "derry": "Derry City",
    "cobh": "Cobh Ramblers", "athlone": "Athlone Town",
    "longford": "Longford Town", "wexford": "Wexford FC",
    "harps": "Finn Harps", "treaty": "Treaty United",
    # British clubs whose short form the id map doesn't carry
    "man united": "Manchester United", "man utd": "Manchester United",
    "man city": "Manchester City", "palace": "Crystal Palace",
    "nottm forest": "Nottingham Forest", "nottingham": "Nottingham Forest",
    "spurs": "Tottenham Hotspur", "wolves": "Wolverhampton Wanderers",
    "west brom": "West Bromwich Albion", "wba": "West Bromwich Albion",
    "sheff united": "Sheffield United", "sheff wed": "Sheffield Wednesday",
    "huddersf": "Huddersfield Town", "southampt": "Southampton",
    "brighton": "Brighton & Hove Albion", "newcastle": "Newcastle United",
    "leeds": "Leeds United", "west ham": "West Ham United",
    "qpr": "Queens Park Rangers", "mk dons": "Milton Keynes Dons",
}

_CLUB_DROP = {"fc", "afc", "cf", "sc", "ac", "if", "fk", "sk", "bk", "sv",
              "club", "de", "cfc", "calcio", "cd", "ud", "ss", "as", "us",
              "ssc", "rc", "nk", "hk", "ik", "bc", "sd", "ca", "the"}
_CLUB_ABBR = {"utd": "united", "utd.": "united", "rgrs": "rangers",
              "rvrs": "rovers", "ath": "athletic", "atl": "atletico",
              "st": "st", "acad": "academy", "res": "", "yth": "youth"}

def _club_norm(n):
    """A club name reduced to the words that identify it."""
    s = (n or "").lower().strip()
    s = re.sub(r"[’']", "", s)                  # o'brien, pat's
    s = re.sub(r"\s+(u\d{2}|academy|acad|reserves?|res|youth|ii|b)$", "", s)
    s = re.sub(r"^\d+\s*\.?\s*(fc|fsv|tsv|vfl|vfb)\s+", "", s)   # 1.FC Köln
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    words = [_CLUB_ABBR.get(w, w) for w in s.split()]
    words = [w for w in words if w and w not in _CLUB_DROP]
    return " ".join(words)

def _load_transfers():
    """data/api/transfers.csv — every club move, oldest first per player —
       with data/manual/transfers.csv laid over the top.

       The manual file exists because Transfermarkt has no record of a whole
       class of real moves: a sixteen-year-old signing for an academy abroad,
       a schoolboy club to a League of Ireland side, anyone going to American
       college football, the lower reaches of the non-league. Those are still
       transfers and people still want to read about them.

       A manual row drops itself the moment the scrape catches up with the
       same move, so the file never has to be tidied by hand. Both files are
       optional; without either, the career block simply doesn't render."""
    out = {}
    for r in _rows("api/transfers.csv"):
        if r.get("slug") and (r.get("to_club") or "").strip():
            out.setdefault(r["slug"], []).append(r)

    for r in _rows("manual/transfers.csv"):
        slug = (r.get("slug") or "").strip()
        to = (r.get("to_club") or "").strip()
        if not slug or not to:
            continue
        d = (r.get("date") or "").strip()[:10]
        # "has the scrape got this one yet?" — same destination, same season
        # or later. Undated rows are read as belonging to the current season.
        year = d[:4] if len(d) == 10 else SEASON[:4]
        key = _club_norm(to)
        if any(_club_norm(x.get("to_club")) == key
               and (x.get("date") or "") >= f"{year}-01-01"
               for x in out.get(slug, [])):
            continue
        out.setdefault(slug, []).append({
            "slug": slug, "date": d,
            "season": (r.get("season") or "").strip(),
            "from_club": (r.get("from_club") or "").strip(), "to_club": to,
            "fee": (r.get("fee") or "").strip(),
            "market_value": (r.get("market_value") or "").strip(),
            "kind": (r.get("kind") or "").strip(), "manual": "1"})

    # an undated row is the most recent thing we know about that player, so
    # it sorts last here (the career block reverses this) and first in the feed
    for rows in out.values():
        rows.sort(key=lambda r: (r.get("date") or "9999-99-99"))
    return out

def club_at(slug, date):
    """Which club a player was at on a given date — the destination of their
       last move on or before it. Loans work out on their own: a loan move puts
       them at the loan club, the end-of-loan move puts them back."""
    club = ""
    for r in TRANSFERS.get(slug, []):
        d = (r.get("date") or "").strip()
        if d and d[:10] <= (date or "")[:10]:
            club = (r.get("to_club") or "").strip()
        else:
            break
    return "" if club.lower() in ("without club", "retired", "") else club

LINE_POS = {"gk":"GK","g":"GK","goalkeeper":"GK",
            "def":"DEF","d":"DEF","defender":"DEF","cb":"DEF","lb":"DEF","rb":"DEF","wb":"DEF",
            "mid":"MID","m":"MID","midfielder":"MID","dm":"MID","cm":"MID","am":"MID",
            "att":"ATT","a":"ATT","f":"ATT","fw":"ATT","forward":"ATT","attacker":"ATT",
            "st":"ATT","cf":"ATT","lw":"ATT","rw":"ATT","w":"ATT"}

def _line_of(pos):
    p = (pos or "").strip().lower()
    if p in LINE_POS: return LINE_POS[p]
    for word, line in (("goal","GK"),("keep","GK"),("defen","DEF"),("back","DEF"),
                       ("midfield","MID"),("wing","ATT"),("attack","ATT"),("forward","ATT"),("strik","ATT")):
        if word in p: return line
    return "MID"

def _fotmob_slugs(live):
    """scraper/fotmob_ids.csv maps our slug to the FotMob player id, which is how
       a lineup row gets matched to a player page. Optional file.

       Two roster rows can carry the same FotMob id — that's exactly what a
       duplicate player looks like — so only map ids onto slugs that survived
       the merge, otherwise the lineup points at a page that doesn't exist."""
    out = {}
    path = os.path.join(HERE, "..", "scraper", "fotmob_ids.csv")
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                fid = str(r.get("fotmob_id") or "").strip()
                slug = ALIAS.get(r.get("slug",""), r.get("slug",""))
                if fid and slug in live and fid not in out:
                    out[fid] = slug
    return out

def _name_key(n):
    n = unicodedata.normalize("NFKD", str(n or "")).encode("ascii","ignore").decode()
    return re.sub(r"[^a-z]", "", n.lower())

def _load_lineups(players):
    """Two files, both optional, both read:

       data/api/lineups.csv        upcoming and current matches — the good one.
         match_id,side,team,formation,status,updated,role,number,name,slug,pos,x,y
         Carries the formation, a confirmed/predicted/last status, and FotMob's
         pitch coordinates (x = distance up the pitch, y = across, both 0-1).

       data/api/match_lineups.csv  finished matches, written by the events pass.
         match_id,team,player,player_id,role,shirt,position
         No side, no formation, no status — team name and position only.

       They cover different matches, so read both; where they overlap the
       richer file wins. No file, or no rows for a match, and the match page
       falls back to the squad list — nothing breaks."""
    rich = _rows("api/lineups.csv")
    plain = [r for r in _rows("api/match_lineups.csv")
             if r.get("match_id") not in {x.get("match_id") for x in rich}]
    rows = rich + plain
    # a teamsheet slug that came from a wrong id points at the wrong player:
    # keep the name on the sheet, drop the link to one of ours
    for r in rows:
        if (r.get("slug") or "").strip() in MISMATCHED:
            r["slug"] = ""
    if not rows: return {}
    live = {p["slug"] for p in players}
    by_id = _fotmob_slugs(live)
    by_name = {}
    for p in players:
        by_name.setdefault(_name_key(p["n"]), p["slug"])
        if p.get("tm") and p["tm"].get("full_name"):
            by_name.setdefault(_name_key(p["tm"]["full_name"]), p["slug"])
    out, seen = {}, set()
    for r in rows:
        mid  = (r.get("match_id") or "").strip()
        name = (r.get("player") or r.get("name") or "").strip()
        team = (r.get("team") or "").strip()
        if not (mid and name): continue
        slug = ((r.get("slug") or "").strip()
                or by_id.get(str(r.get("player_id") or "").strip())
                or by_name.get(_name_key(name), ""))
        slug = ALIAS.get(slug, slug)
        if slug not in live: slug = ""
        role = "bench" if (r.get("role") or "").strip().lower().startswith("b") else "start"
        side = (r.get("side") or "").strip().lower()
        if side not in ("home","away"): side = ""
        # side is resolved later, at page-build time, when club_slug exists;
        # until then group on whichever of side/team the row actually carries
        group = side or _club_key(team)
        if not group: continue
        # one man, one place on the teamsheet, whatever the feed sent twice
        key = (mid, group, role, slug or _name_key(name),
               str(r.get("player_id") or "").strip() or _name_key(name))
        if key in seen: continue
        seen.add(key)
        sd = out.setdefault(mid, {}).setdefault(group, dict(
            team=team, side=side,
            formation=(r.get("formation") or "").strip(),
            status=(r.get("status") or "").strip().lower(),
            start=[], bench=[]))
        if not sd["formation"] and (r.get("formation") or "").strip():
            sd["formation"] = r["formation"].strip()
        if not sd["status"] and (r.get("status") or "").strip():
            sd["status"] = r["status"].strip().lower()
        pos = (r.get("position") or r.get("pos") or "").strip()
        row = dict(name=name, slug=slug,
                   num=(r.get("shirt") or r.get("number") or "").strip(),
                   pos=pos, line=_line_of(pos),
                   x=_num(r.get("x")), y=_num(r.get("y")))
        sd[role].append(row)
    return out

def _club_key(n):
    return re.sub(r"[^a-z0-9]", "", (n or "").lower())

def _match_lineup(m):
    """Put each side of the stored lineup on the right half of the pitch.
       lineups.csv says home/away outright; match_lineups.csv only names the
       club, so fall back to matching that against the fixture."""
    raw = LINEUPS.get(match_id(m))
    if not raw: return None
    out, spare = {}, []
    for sd in raw.values():
        if sd.get("side") in ("home","away") and sd["side"] not in out:
            out[sd["side"]] = sd
        else:
            spare.append(sd)
    for sd in spare:
        k = _club_key(sd["team"])
        for side in ("home","away"):
            want = _club_key(m.get(side, ""))
            if side not in out and k and want and (k == want or k in want or want in k):
                out[side] = sd; break
    if len(out) < 2 and len(spare) == 2 and not out:   # names differ, order them anyway
        out = {"home": spare[0], "away": spare[1]}
    if not any(len(sd["start"]) >= 7 for sd in out.values()): return None
    return out

def _num(v):
    try: return float(str(v).strip())
    except (TypeError, ValueError): return None

def load():
    tmdata = {r["slug"]: r for r in _rows("api/tm.csv") if r.get("slug")}
    global EVENTS, LINEUPS
    EVENTS = {}
    for r in _rows("api/match_events.csv"):
        if r.get("match_id") and r.get("type"):
            EVENTS.setdefault(r["match_id"], []).append(r)
    manual_players = {r["slug"]: r for r in _rows("manual/players.csv") if r.get("slug")}
    fixtures, results = {}, {}
    for r in _merge_rows("fixtures.csv", ("slug","date","opponent")):
        if r.get("slug"):
            fixtures.setdefault(r["slug"], []).append(
                (r["date"], r["opponent"], r["home_away"], r["competition"]))
    for r in _merge_rows("results.csv", ("slug","date","opponent")):
        if r.get("slug"):
            results.setdefault(r["slug"], []).append(
                (r["date"], r["opponent"], r["score"], r["competition"],
                 _int(r["minutes"]), _int(r["goals"]), _int(r["assists"]), (r.get("rating") or "").strip()))

    players = []
    for slug, r in _merge_players().items():
        if not (r.get("name") or "").strip():
            continue                              # a row with no name isn't a player yet
        if _yes(r.get("exclude")):
            continue                              # manual "not a player" flag: managers,
                                                  # coaches, anyone the feed lists wrongly
        youth = []
        for chunk in filter(None, [c.strip() for c in (r.get("youth") or "").split(";")]):
            parts = chunk.split(":")
            if len(parts) == 4: youth.append((parts[0], _int(parts[1]), _int(parts[2]), parts[3]))
            elif len(parts) == 1 and parts[0]: youth.append((parts[0], None, None, ""))
        elig = []
        for chunk in filter(None, [c.strip() for c in (r.get("eligible") or "").split(";")]):
            parts = chunk.split(":")
            if len(parts) == 2: elig.append((parts[0], parts[1]))
        if not elig: elig = [("Republic of Ireland","eligible")]
        lvl = (r.get("ireland_level") or "").strip()
        senior = None
        if str(r.get("senior_caps","")).strip():
            senior = dict(caps=_int(r["senior_caps"]), goals=_int(r.get("senior_goals")),
                          debut=r.get("senior_debut",""))
        elif lvl == "Senior":
            senior = dict(caps=None, goals=None, debut="")   # capped, totals not filled in yet
        if lvl and lvl != "Senior" and not youth:
            youth = [(lvl, None, None, "")]                  # level known, caps not
        tier = (r.get("tier") or "").strip() or ("loi" if "League of Ireland" in (r.get("league") or "") else "abroad-lower")
        players.append(dict(
            slug=slug, n=r["name"], club=(r.get("club") or "Unattached"),
            league=(r.get("league") or "—"), tier=tier,
            pos=(r.get("pos") or "—"), age=_int(r.get("age")), born=r.get("born",""),
            foot=r.get("foot",""), cap_status=(r.get("cap_status") or "uncapped"),
            intl_senior=senior, intl_youth=youth, eligible=elig,
            season=dict(ap=_int(r.get("s_apps")), starts=_int(r.get("s_starts")),
                        g=_int(r.get("s_goals")), a=_int(r.get("s_assists")),
                        mins=_int(r.get("s_mins")), yellow=_int(r.get("s_yellow")), red=_int(r.get("s_red"))),
            career=dict(ap=_int(r.get("c_apps")), g=_int(r.get("c_goals")), a=_int(r.get("c_assists"))),
            injury=(r.get("injury") or "").strip() or None, transfers=[],
            loan=(r.get("on_loan_at") or "").strip(),
            parent_club=(r.get("club") or "").strip(),
            rating=(r.get("avg_rating") or "").strip(),
            season_label=(r.get("season") or "").strip(),
            s_team=(r.get("s_team") or "").strip(),
            s_team_id=(r.get("s_team_id") or "").strip(),
            s_league=(r.get("s_league") or "").strip(),
            s_splits=(r.get("s_splits") or "").strip(),
            source=(r.get("source") or "auto").strip().lower(),
            blanks={k for k in ("s_apps","s_starts","s_goals","s_assists","s_mins",
                                "s_yellow","s_red","c_apps","c_goals","c_assists")
                    if not (r.get(k) or "").strip()},
            photo=(r.get("photo") or "").strip(),
            photo_credit=(r.get("photo_credit") or "").strip(),
            fixtures=([] if slug in MISMATCHED else fixtures.get(slug, [])),
            results=([] if slug in MISMATCHED else results.get(slug, []))))
    # ---- Transfermarkt bio/contract ----
    for p in players:
        t = tmdata.get(p["slug"])
        if not t:
            p["tm"] = None
            p["age"] = _sane_age(p["age"])
            continue
        iso_exp, exp_label = _tm_date(t.get("contract_expires"))
        _, joined_label    = _tm_date(t.get("joined"))
        p["tm"] = dict(
            full_name   = (t.get("tm_name") or "").strip(),
            dob         = (t.get("dob") or "").strip(),
            birthplace  = (t.get("birthplace") or "").strip(),
            height      = _tm_height(t.get("height_cm")),
            nations     = _tm_nations(t.get("citizenship")),
            position    = (t.get("position") or "").strip(),
            foot        = (t.get("foot") or "").strip(),
            agent       = _tm_text(t.get("agent")),
            club        = _tm_text(t.get("tm_club")),
            joined      = joined_label,
            expires     = exp_label,
            expires_iso = iso_exp,
            option      = (t.get("contract_option") or "").strip(),
            value       = (t.get("market_value") or "").strip(),
        )
        # A date of birth beats a scraped age: the feed had teenagers listed as
        # 2025 years old, and a few others out by a decade.
        p["raw_age"] = p["age"]
        p["age"] = _age_from_dob(p["tm"]["dob"]) or _sane_age(p["age"])

        # birthplace is the only source we have for "born"
        if not p.get("born") and p["tm"]["birthplace"]:
            p["born"] = p["tm"]["birthplace"]
        if not p.get("foot") and p["tm"]["foot"]:
            p["foot"] = p["tm"]["foot"].title()

        # club: trust this source over the stats feed, but never over a human edit
        tmclub = p["tm"]["club"]
        manual_row = manual_players.get(p["slug"], {})
        human_set_club = bool((manual_row.get("club") or "").strip())
        if tmclub and not human_set_club and not p.get("loan"):
            if tmclub != p["club"]:
                p["club_was"] = p["club"]        # keep what the stats feed said
            p["club"] = tmclub
            p["parent_club"] = tmclub

    for p in players:
        if p.get("loan"):
            p["club"] = p["loan"]          # display the club they're actually at
    players.sort(key=lambda p: p["n"])
    players, alias = _dedupe_players(players, manual_players)
    global ALIAS, LINEUPS, TRANSFERS
    ALIAS = alias
    LINEUPS = _load_lineups(players)
    TRANSFERS = _load_transfers()
    for p in players:
        p["transfers"] = list(reversed(TRANSFERS.get(p["slug"], [])))

    # The scraper writes: team,kickoff,competition,home,away,home_score,away_score,status
    # The older hand-kept format was: level,type,date,opponent,home_away,score,competition
    # Read either.
    LEVEL_NAME = {"senior":"Senior","u21":"U21","u19":"U19","u17":"U17"}
    ireland = {}
    for r in _rows("manual/ireland.csv"):
        if r.get("team") or r.get("kickoff"):                     # scraper format
            lvl = LEVEL_NAME.get((r.get("team") or "").strip().lower(),
                                 (r.get("team") or "Senior").strip() or "Senior")
            home, away = (r.get("home") or "").strip(), (r.get("away") or "").strip()
            we_are_home = home.lower().startswith("ireland") or home.lower().startswith("republic")
            opp = away if we_are_home else home
            ha  = "H" if we_are_home else "A"
            date = (r.get("kickoff") or "").strip()
            comp = (r.get("competition") or "").strip()
            hs, as_ = (r.get("home_score") or "").strip(), (r.get("away_score") or "").strip()
            done = (r.get("status") or "").strip().lower() in ("ft","finished","played")
            lv = ireland.setdefault(lvl, dict(label=lvl, fixtures=[], results=[]))
            if done and hs != "" and as_ != "":
                ours, theirs = (hs, as_) if we_are_home else (as_, hs)
                lv["results"].append((date, opp, f"{ours}-{theirs}", comp))
            else:
                lv["fixtures"].append((date, opp, ha, comp))
        else:                                                      # original format
            lvl = r.get("level") or "Senior"
            lv = ireland.setdefault(lvl, dict(label=lvl, fixtures=[], results=[]))
            if r.get("type") == "fixture":
                lv["fixtures"].append((r["date"], r["opponent"], r["home_away"], r["competition"]))
            else:
                lv["results"].append((r["date"], r["opponent"], r["score"], r["competition"]))

    # newest results first, soonest fixtures first
    for lv in ireland.values():
        lv["fixtures"].sort(key=lambda x: x[0])
        lv["results"].sort(key=lambda x: x[0], reverse=True)
        lv["results"] = lv["results"][:6]
    news = [(r["tag"], r["headline"], r["standfirst"], r["player_slug"]) for r in _rows("manual/news.csv")]
    matches = _merge_rows("matches.csv", ("kickoff","home","away"))
    # A wrong id also put these players into somebody else's squads. Strip them
    # here, once, so nothing downstream can put them on a match they never
    # played in - and drop any match that was only there because of them.
    if MISMATCHED:
        kept = []
        for m in matches:
            names = [x.strip() for x in (m.get("players") or "").split(";") if x.strip()]
            clean = [x for x in names if x not in MISMATCHED]
            if names and not clean: continue      # nobody real was in this game
            m["players"] = ";".join(clean)
            kept.append(m)
        matches = kept
    # Two layers. manual/articles.csv is yours — its CSV order is the display
    # order you drag in the admin, so it stays exactly as written and comes
    # first. api/articles.csv is machine-written; a slug that exists in both is
    # yours, which is how you overrule anything the pipeline got wrong: publish
    # that slug in the admin and the generated one stops rendering.
    articles = [r for r in _rows("manual/articles.csv") if r.get("slug")]
    _mine = {(r.get("slug") or "").strip().lower() for r in articles}
    _api = [r for r in _rows("api/articles.csv")
            if r.get("slug")
            and (r["slug"] or "").strip().lower() not in _mine]
    # newest day first; within a day the heaviest story leads, so a big-league
    # performance is never buried under a fresher small-league one
    def _aw(r):
        # No weight column = the news pipeline wrote it. Real news sits above
        # all but the biggest auto match reports.
        try: return float(r.get("weight") or 150)
        except ValueError: return 150.0
    _api.sort(key=lambda r: ((r.get("date") or ""), _aw(r)), reverse=True)
    articles += _api
    accounts = _rows("manual/accounts.csv")
    clubgeo  = {r["club"]: r for r in _rows("manual/clubs.csv") if r.get("club")}
    return players, ireland, news, matches, articles, accounts, clubgeo, tmdata, alias

PLAYERS, IRELAND, NEWS, MATCHES, ARTICLES, ACCOUNTS, CLUBGEO, TM, ALIAS = load()
for _p in PLAYERS:
    if (_p.get("club") or "").strip().lower() in ("without club","no club","free agent",""):
        _p["club"] = "Unattached"
        if (_p.get("league") or "").strip().lower() in ("without club","no club","free agent"): _p["league"] = "—"

def _extend_matches():
    """The scraper only writes matches.csv for a 2-week window. Every player fixture
       beyond that still deserves a match page, so build the rest from fixtures.csv."""
    have = {match_id(m) for m in MATCHES}
    def _key(n): return re.sub(r"\s+(fc|afc|u\d{2})$","",club_slug(n).replace("-fc","").replace("-afc",""))
    daykeys = set()
    for m in MATCHES:
        d = (m.get("kickoff") or "")[:10]
        for side in ("home","away"): daykeys.add((d, _key(m.get(side,""))))
    synth = {}
    for p in PLAYERS:
        club = p.get("club") or ""
        if not club: continue
        for d,o,h,cp in p["fixtures"]:
            if len(d) < 10: continue
            home, away = (club, o) if str(h).upper().startswith("H") else (o, club)
            m = dict(kickoff=d, competition=cp, home=home, away=away, home_score="", away_score="",
                     status="scheduled", minute="", players=p["slug"])
            k = match_id(m)
            if k in have: continue
            if (d[:10], _key(o)) in daykeys or (d[:10], _key(club)) in daykeys: continue   # already covered under another spelling
            if k in synth: synth[k]["players"] += ";" + p["slug"]
            else: synth[k] = m
    MATCHES.extend(synth.values())
TIERS = {"abroad-top":"Abroad — top divisions",
         "abroad-lower":"Abroad — second tier & smaller leagues",
         "loi":"League of Ireland"}

def esc(s): return H.escape(str(s), quote=False)
OUT = os.path.join(HERE, "..", "site")   # always repo-root/site, whatever the CWD

CSS = open(os.path.join(HERE, "style.css")).read()
APPJS = open(os.path.join(HERE, "app.js")).read()

NAV = [("News","news.html"),("Players","players.html"),("Transfers","transfers.html"),
       ("Clubs","clubs.html"),("Ireland","ireland.html"),("Fixtures","fixtures.html"),
       ("Alerts","alerts.html")]


# ---- league tables (data/api/tables.csv, written by the tables scrape) ----
def _tbl_key(league):
    k = re.sub(r"[^a-z0-9]", "", (league or "").lower())
    return re.sub(r"^leagueofireland", "", k)


def _load_tables():
    out = {}
    for r in _rows("api/tables.csv"):
        if not (r.get("league") and r.get("team")):
            continue
        out.setdefault(_tbl_key(r["league"]), []).append(r)
    return out


# ---- site controls (data/manual/settings.csv, edited in the admin) ----
SETTINGS = {r.get("key", "").strip(): (r.get("value") or "").strip()
            for r in _rows("manual/settings.csv") if r.get("key")}


def setting(key, default=""):
    v = SETTINGS.get(key, "")
    return v if v != "" else default


def setting_on(key, default=True):
    v = SETTINGS.get(key, "")
    if v == "":
        return default
    return v.strip().lower() in ("yes", "y", "true", "1", "on")


TABLES = _load_tables()
_CLUB_PAGES = {}     # norm club name -> canonical club name
for _p in PLAYERS:
    _c = (_p.get("club") or "").strip()
    if _c and _c not in ("No club", "Unattached", "—"):
        _k = re.sub(r"(afc|fc)$", "", re.sub(r"[^a-z0-9]", "", _c.lower()))
        _CLUB_PAGES.setdefault(_k, _c)


def table_rows_for(league):
    return TABLES.get(_tbl_key(league), [])


def club_href(name, root=""):
    """Path to a club's page, or "" when we don't build one for it."""
    k = re.sub(r"(afc|fc)$", "", re.sub(r"[^a-z0-9]", "",
                                        (name or "").lower()))
    hit = _CLUB_PAGES.get(k)
    return f"{root}club/{club_slug(hit)}.html" if hit else ""


def _row_club_link(team, root):
    """A table team that we track links to its club page."""
    k = re.sub(r"[^a-z0-9]", "", (team or "").lower())
    k = re.sub(r"(afc|fc)$", "", k)
    hit = _CLUB_PAGES.get(k)
    return f'{root}{clink(hit)}' if hit else ""


def league_table_html(league, root="../", around_club="", title=""):
    """FotMob-style standings. around_club trims to that club +-3 places
    (a club page snippet); otherwise the full table. Tracked clubs link
    through and get the highlight bar."""
    rows = table_rows_for(league)
    if not rows:
        return ""
    focus = -1
    if around_club:
        ck = re.sub(r"[^a-z0-9]", "", around_club.lower())
        ck = re.sub(r"(afc|fc)$", "", ck)
        for i, r in enumerate(rows):
            tk = re.sub(r"[^a-z0-9]", "", r["team"].lower())
            tk = re.sub(r"(afc|fc)$", "", tk)
            if tk == ck or (len(ck) > 3 and (ck in tk or tk in ck)):
                focus = i
                break
        if focus < 0:
            return ""
        lo = max(0, min(focus - 3, len(rows) - 7))
        rows = rows[lo:lo + 7]
    body = ""
    for r in rows:
        link = _row_club_link(r["team"], root)
        cls = "ltr"
        tk = re.sub(r"[^a-z0-9]", "", r["team"].lower())
        fk = re.sub(r"[^a-z0-9]", "", (around_club or "").lower())
        fk = re.sub(r"(afc|fc)$", "", fk)
        if around_club and fk and (fk in tk or tk.rstrip("afc") == fk):
            cls += " me"
        elif link:
            cls += " ours"
        gd = str(r.get("gd") or "")
        if gd and not gd.startswith("-") and gd not in ("0",):
            gd = "+" + gd
        cell = (f'<span class="ltp">{esc(r["idx"])}</span>'
                f'{badge_by_id(r.get("team_id"), "sm")}'
                f'<span class="ltn">{esc(r["team"])}</span>'
                f'<span class="ltc">{esc(r.get("played",""))}</span>'
                f'<span class="ltc wdl">{esc(r.get("wins",""))}</span>'
                f'<span class="ltc wdl">{esc(r.get("draws",""))}</span>'
                f'<span class="ltc wdl">{esc(r.get("losses",""))}</span>'
                f'<span class="ltc">{esc(gd)}</span>'
                f'<span class="ltc pts">{esc(r.get("pts",""))}</span>')
        body += (f'<a class="{cls}" href="{link}">{cell}</a>' if link
                 else f'<div class="{cls}">{cell}</div>')
    head = ('<div class="ltr lthead"><span class="ltp">#</span>'
            '<span class="badge sm generic" style="visibility:hidden"></span>'
            '<span class="ltn">Team</span><span class="ltc">P</span>'
            '<span class="ltc wdl">W</span><span class="ltc wdl">D</span>'
            '<span class="ltc wdl">L</span><span class="ltc">+/-</span>'
            '<span class="ltc pts">Pts</span></div>')
    cap = (f'<div class="sec"><h2>{esc(title or "Table")}</h2></div>'
           if title is not None else "")
    return f'{cap}<div class="ltable">{head}{body}</div>'


def league_position_of(club, league):
    for r in table_rows_for(league):
        tk = re.sub(r"[^a-z0-9]", "", r["team"].lower())
        ck = re.sub(r"[^a-z0-9]", "", (club or "").lower())
        tk2 = re.sub(r"(afc|fc)$", "", tk)
        ck2 = re.sub(r"(afc|fc)$", "", ck)
        if tk2 == ck2 or (len(ck2) > 3 and (ck2 in tk2 or tk2 in ck2)):
            return str(r["idx"])
    return ""


def _ord_pos(n):
    try:
        n = int(n)
    except (TypeError, ValueError):
        return ""
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1:'st',2:'nd',3:'rd'}.get(n % 10, 'th')}"


def matchweek_label():
    """Work out the label from the data: the window the current fixtures cover."""
    if MATCHWEEK: return MATCHWEEK
    import datetime
    days = []
    for m in MATCHES:
        k = (m.get("kickoff") or "")[:10]
        if len(k) == 10:
            try: days.append(datetime.date.fromisoformat(k))
            except ValueError: pass
    if not days: return "FIXTURES PENDING"
    today = datetime.date.today()
    near = sorted(days, key=lambda d: abs((d - today).days))[:1][0]
    week = [d for d in days if abs((d - near).days) <= 3]
    lo, hi = min(week), max(week)
    mon = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"]
    if lo == hi: return f"{lo.day} {mon[lo.month-1]}"
    if lo.month == hi.month: return f"{lo.day}-{hi.day} {mon[hi.month-1]}"
    return f"{lo.day} {mon[lo.month-1]}-{hi.day} {mon[hi.month-1]}"


LOADER_HTML = '''<div id="fbload" aria-hidden="true">
  <div class="fbl-mark">footballers<i>.ie</i></div>
  <div class="fbl-pitch">
    <div class="fbl-ball"><i></i></div>
    <svg class="fbl-boot" viewBox="0 0 40 44" aria-hidden="true">
      <g class="fbl-leg">
        <line x1="20" y1="2" x2="20" y2="24"/>
        <line x1="20" y1="24" x2="15" y2="38"/>
        <line x1="15" y1="38" x2="31" y2="40"/>
      </g>
    </svg>
  </div>
</div>'''

LOADER_JS = """<script>
/* The kick only plays on a fresh arrival at the homepage — opening the site or
   refreshing it. Coming back from a player or match page skips it. */
(function(){
  var el=document.getElementById('fbload');
  if(!el) return;

  var nav=(performance.getEntriesByType && performance.getEntriesByType('navigation')[0]) || null;
  var kind = nav ? nav.type : (performance.navigation && performance.navigation.type===1 ? 'reload' : 'navigate');
  var internal=false;
  try{ internal = sessionStorage.getItem('fb_nav_v1')==='1'; }catch(e){}

  // already been on the site this tab, and this isn't a reload? don't replay it
  if(internal && kind!=='reload'){
    el.parentNode && el.parentNode.removeChild(el);
    return;
  }

  var t0=Date.now(), MIN=2150;
  function done(){
    var wait=Math.max(0, MIN-(Date.now()-t0));
    setTimeout(function(){
      el.classList.add('out');
      setTimeout(function(){ if(el.parentNode) el.parentNode.removeChild(el); }, 340);
    }, wait);
  }
  if(document.readyState==='complete') done();
  else window.addEventListener('load', done);
  setTimeout(done, 3600);
})();
</script>"""

# ---- analytics --------------------------------------------------------------
# PostHog, EU region. The key is a public write-only key, safe in the repo.
# Anyone who has ever opened the admin gets fb_no_track set and is never
# counted; visiting any page with ?notrack in the url does the same by hand
# (localStorage.removeItem('fb_no_track') in the console undoes it).
ANALYTICS_JS = """<script>
(function(){
  try{
    if(location.search.indexOf('notrack')>-1) localStorage.setItem('fb_no_track','1');
    if(localStorage.getItem('fb_no_track')) return;
  }catch(e){}
  !function(t,e){var o,n,p,r;e.__SV||(window.posthog=e,e._i=[],e.init=function(i,s,a){function g(t,e){var o=e.split(".");2==o.length&&(t=t[o[0]],e=o[1]),t[e]=function(){t.push([e].concat(Array.prototype.slice.call(arguments,0)))}}(p=t.createElement("script")).type="text/javascript",p.crossOrigin="anonymous",p.async=!0,p.src=s.api_host.replace(".i.posthog.com","-assets.i.posthog.com")+"/static/array.js",(r=t.getElementsByTagName("script")[0]).parentNode.insertBefore(p,r);var u=e;for(void 0!==a?u=e[a]=[]:a="posthog",u.people=u.people||[],u.toString=function(t){var e="posthog";return"posthog"!==a&&(e+="."+a),t||(e+=" (stub)"),e},u.people.toString=function(){return u.toString(1)+".people (stub)"},o="init capture identify alias people.set people.set_once set_config register register_once unregister opt_out_capturing has_opted_out_capturing opt_in_capturing reset isFeatureEnabled onFeatureFlags getFeatureFlag getFeatureFlagPayload reloadFeatureFlags group updateEarlyAccessFeatureEnrollment getEarlyAccessFeatures getActiveMatchingSurveys getSurveys onSessionId debug".split(" "),n=0;n<o.length;n++)g(u,o[n]);e._i.push([i,s,a])},e.__SV=1)}(document,window.posthog||[]);
  posthog.init('phc_xtwt9agRRaGnB85uiQHmNrGEQ97cm3iCKULiQx5VBcVJ',{
    api_host:'https://eu.i.posthog.com',
    defaults:'2025-05-24',
    person_profiles:'identified_only'
  });
})();
</script>"""


def og_card(**fields):
    """The share card for a page: /api/og with the handful of fields that card
       needs. Blank fields are left out so the URL stays short, and a page that
       asks for nothing falls back to the one static image."""
    from urllib.parse import urlencode
    q = {k: str(v) for k, v in fields.items() if str(v or "").strip() != ""}
    if not q:
        return f"{SITE_URL}/og-image.png"
    return f"{SITE_URL}/api/og?" + urlencode(q)

def shell(title, desc, root, active, body, extra_head="", canonical="",
          body_attr="", og=""):
    og = og or f"{SITE_URL}/og-image.png"
    is_home = (root == "" and active in ("index.html", ""))
    loader    = LOADER_HTML if is_home else ""
    loader_js = LOADER_JS   if is_home else ""
    nav_items = [(l, h) for l, h in NAV
                 if not (h == "transfers.html" and not setting_on("show_transfers"))]
    links = "".join(f'<a class="{"on" if active==href else ""}" href="{root}{href}">{l}</a>' for l,href in nav_items)
    banner = ""
    if setting("banner"):
        _bl = setting("banner_link")
        _bo = f'<a class="sitebanner" href="{root}{esc(_bl)}">' if _bl else '<div class="sitebanner">'
        _bc = '</a>' if _bl else '</div>'
        banner = f'{_bo}{esc(setting("banner"))}{_bc}'
    if setting_on("maintenance", False):
        body = (f'<div class="maintwrap"><div class="maintcard">'
                f'<div class="mark" style="font-size:26px">footballers<i>.ie</i></div>'
                f'<p>{esc(setting("maintenance_message", "Back shortly — doing a bit of work on the site."))}</p>'
                f'</div></div>')
        banner = ""
    can = f"{SITE_URL}/{canonical}" if canonical else SITE_URL
    return f"""<!DOCTYPE html>
<html lang="en" data-theme="{esc(setting("theme_default", "pitch"))}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(desc)}">
<link rel="canonical" href="{can}">
<meta name="theme-color" content="#0C0F10">
<meta property="og:site_name" content="Footballers">
<meta property="og:type" content="website">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(desc)}">
<meta property="og:url" content="{can}">
<meta property="og:image" content="{esc(og)}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{esc(title)}">
<meta name="twitter:description" content="{esc(desc)}">
<meta name="twitter:image" content="{esc(og)}">
<link rel="icon" href="{root}favicon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="{root}apple-touch-icon.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>{CSS}</style>{ANALYTICS_JS}{extra_head}
</head>
<body{body_attr}>
{loader}
{loader_js}<div class="wrap">
<nav>
  <a class="mark" href="{root}index.html">footballers<i>.ie</i></a>
  <a class="searchbtn" href="{root}search.html" aria-label="Search">
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.6-4.6"/></svg>
  </a>
  <button class="burger" id="burger" aria-label="Menu" aria-expanded="false" aria-controls="navlinks">
    <span></span><span></span><span></span>
  </button>
  <div class="navlinks" id="navlinks">{links}<div class="navfoot">{esc(matchweek_label())} · <b>{len(PLAYERS)}</b> tracked</div></div>
  <div class="navmeta"><span id="navdate">{esc(matchweek_label())}</span> · <b>{len(PLAYERS)}</b> TRACKED</div>
</nav>
<script>
(function(){{
  var b=document.getElementById('burger'),n=document.getElementById('navlinks');
  b.addEventListener('click',function(){{
    var open=n.classList.toggle('open');
    b.setAttribute('aria-expanded',open?'true':'false');
    b.classList.toggle('x',open);
  }});
}})();
</script>
{banner}
{body}
<footer>
  <div class="foothead">
    {'footballers.ie · prototype · sample data' if SAMPLE_DATA else 'footballers.ie'}
    <span class="updated" data-stamp="{DATA_STAMP}">checking for updates…</span>
  </div>
  {esc(setting("footer_text", "Every Irish player at a professional club — abroad, senior international and League of Ireland"))}
  <div class="footlinks">
    <a href="{root}faq.html">FAQ</a>
    <a href="{root}where-are-the-irish.html">Where are the Irish?</a>
    <a href="{root}alerts.html">Alerts</a>
  </div>
</footer>
</div>
<script>window.FB_CLUBS={json.dumps({k: club_slug(v) for k, v in sorted(_CLUB_PAGES.items())})};window.FB_SUBSCRIBE_URL={json.dumps(NEWSLETTER_ACTION)};window.FB_ACCOUNTS={json.dumps([{k:a.get(k,"") for k in ("email","name","role","hash")} for a in ACCOUNTS])};</script>
<script>{APPJS}</script>
</body>
</html>"""

def club_slug(c):
    """Filename-safe slug. Club and league names contain slashes ("Highland / Lowland"),
    ampersands, dots and accents — none of which can go into a path."""
    import unicodedata, re
    s = str(c or "")
    for a, b in (("ø","o"),("Ø","O"),("æ","ae"),("Æ","AE"),("å","a"),("Å","A"),
                 ("ß","ss"),("ð","d"),("Ð","D"),("þ","th"),("Þ","Th"),
                 ("ı","i"),("İ","i"),("ł","l"),("Ł","L")):
        s = s.replace(a, b)
    s = unicodedata.normalize("NFKD", s)
    s = s.encode("ascii", "ignore").decode()          # strip accents
    s = s.replace("'", "").replace("\u2019", "")
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s)              # everything else becomes a hyphen
    return re.sub(r"-{2,}", "-", s).strip("-").lower() or "other"
def plink(p, root=""): return f'{root}player/{p["slug"]}.html'
def clink(c, root=""): return f'{root}club/{club_slug(c)}.html'

CLUB_IDS = {}
CLUB_NORM = {}          # reduced club name -> id, for feeds that abbreviate
ACADEMY_RE = re.compile(r"\s+(u\d{2}|academy|reserves?|ii|b)$", re.I)
def _register_club_ids():
    """club name -> FotMob team id: the scraper-maintained club_ids.csv first,
       then players.csv, clubs.csv, and both sides of every match in
       matches.csv - so any club that ever appears with an id keeps its badge.
       Academy sides (U21/U18/Academy) borrow the senior club's badge."""
    for r in _rows("api/club_ids.csv"):
        if (r.get("club") or "").strip() and (r.get("club_id") or "").strip():
            CLUB_IDS.setdefault(r["club"].strip(), r["club_id"].strip())
    for r in _rows("api/players.csv"):
        if (r.get("club") or "").strip() and (r.get("club_id") or "").strip():
            CLUB_IDS.setdefault(r["club"].strip(), r["club_id"].strip())
    for r in _rows("manual/clubs.csv"):
        if (r.get("club") or "").strip() and (r.get("club_id") or "").strip():
            CLUB_IDS.setdefault(r["club"].strip(), r["club_id"].strip())
    for r in _rows("api/matches.csv"):
        for nk, ik in (("home", "home_id"), ("away", "away_id")):
            if (r.get(nk) or "").strip() and (r.get(ik) or "").strip():
                CLUB_IDS.setdefault(r[nk].strip(), r[ik].strip())
    # a second index keyed on the reduced name, so a club we know as
    # "St. Patrick's Athletic" is still found when a feed calls it "St. Pat's"
    for name, cid in CLUB_IDS.items():
        k = _club_norm(name)
        if k and k not in CLUB_NORM:
            CLUB_NORM[k] = cid
_register_club_ids()

def club_id(name):
    """The badge id for a club, however the source spelled it. Exact name
       first, then the senior club behind an academy side, then the reduced
       name, then the alias table. No guessing beyond that."""
    n = (name or "").strip()
    if not n or n.lower() in NON_CLUBS: return ""
    k = _club_norm(n)
    # a name we have declared by hand outranks anything a search wrote:
    # "Bohemians" in an Irish feed is Dublin, whatever fotmob offers first
    alias = CLUB_ALIASES.get(k)
    if alias:
        cid = CLUB_IDS.get(alias) or CLUB_NORM.get(_club_norm(alias), "")
        if cid: return cid
    if n in CLUB_IDS: return CLUB_IDS[n]
    parent = ACADEMY_RE.sub("", n)
    if parent != n and parent in CLUB_IDS: return CLUB_IDS[parent]
    return CLUB_NORM.get(k, "") if k else ""

def badge_by_id(cid, size="sm"):
    if not cid: return f'<span class="badge {size} generic"></span>'
    return (f'<img class="badge {size}" src="https://images.fotmob.com/image_resources/logo/teamlogo/{cid}.png" '
            f'alt="" loading="lazy" onerror="this.outerHTML=\'<span class=&quot;badge {size} generic&quot;></span>\'">')

def club_badge(name, size="sm"):
    cid = club_id(name)
    if not cid: return f'<span class="badge {size} generic"></span>'
    return (f'<img class="badge {size}" src="https://images.fotmob.com/image_resources/logo/teamlogo/{cid}.png" '
            f'alt="" loading="lazy" onerror="this.outerHTML=\'<span class=&quot;badge {size} generic&quot;></span>\'">')

def ev_str(p):
    r = p["results"][0] if p["results"] else None
    if not r: return "—", 0
    mins,g,a = r[4],r[5],r[6]
    bits = []
    if g: bits.append("⚽"*g)
    if a: bits.append("🅰"*a)
    return (" ".join(bits) if bits else "—"), mins


def stat(p, key, value):
    """Blank in the feed means unknown, not zero."""
    return "—" if key in p.get("blanks", ()) else value

def has_data(p):
    """Some players are tracked but the source has no page for them yet."""
    if p.get("source") == "none": return False
    s = p["season"]
    return any([s["ap"], s["g"], s["a"], s["mins"], p.get("rating"), p["career"]["ap"]])

def player_row(p, root=""):
    ev, mins = ev_str(p)
    return (f'<a class="plrow" href="{plink(p,root)}">{avatar(p, root, "sm")}'
            f'<div class="nm">{esc(p["n"])} '
            f'<span class="cl">{club_badge(p["club"],"xs")}{esc(p["club"])}</span></div><div class="ev">{ev}</div>'
            f'<div class="mn">{rating_chip(p, True)}</div>{star(p)}</a>')





def rating_chip(p, small=False):
    v = p.get("rating","")
    if not v: return '<span class="rate none">—</span>'
    try: f = float(v)
    except: return '<span class="rate none">—</span>'
    cls = "hi" if f >= 7.3 else ("md" if f >= 6.5 else "lo")
    return f'<span class="rate {cls}{" sm" if small else ""}" title="Season average match rating">{f:.2f}</span>'

def star(p):
    return f'<button class="star" data-fav="{p["slug"]}" aria-pressed="false" aria-label="Follow {esc(p["n"])}">★</button>'

def next_fixture(p):
    return f'{p["fixtures"][0][0]} v {p["fixtures"][0][1]}' if p["fixtures"] else ""

def initials(name):
    parts = [p for p in name.split() if p]
    return (parts[0][0] + parts[-1][0]).upper() if len(parts) > 1 else (parts[0][:2].upper() if parts else "?")

IMG_DIR = os.path.join(HERE, "..", "img", "players")
HAVE_IMG = set()
if os.path.isdir(IMG_DIR):
    HAVE_IMG = {f.rsplit(".",1)[0] for f in os.listdir(IMG_DIR) if f.lower().endswith((".png",".jpg",".jpeg",".webp"))}
# an image downloaded against a wrong id is a photo of somebody else - initials
# are the honest fallback until the picture is pulled again under the right id
HAVE_IMG -= set(MISMATCHED) | set(SUSPECT_PHOTOS)

def avatar(p, root="", size="lg"):
    cls = "pavatar" + (" sm" if size == "sm" else "")
    if not p.get("photo") and p["slug"] in HAVE_IMG:
        return (f'<div class="{cls}"><img src="{root}img/players/{p["slug"]}.png" '
                f'alt="{esc(p["n"])}" loading="lazy" '
                f'onerror="this.parentNode.innerHTML=\'<span>{initials(p["n"])}</span>\'"></div>')
    if p.get("photo"):
        src_url = p["photo"] if p["photo"].startswith("http") else f'{root}{p["photo"]}'
        return (f'<div class="{cls}"><img src="{esc(src_url)}" alt="{esc(p["n"])}" loading="lazy" '
                f'onerror="this.parentNode.innerHTML=\'<span>{initials(p["n"])}</span>\'"></div>')
    return f'<div class="{cls}"><span>{initials(p["n"])}</span></div>'

def signup(root="", compact=False):
    if NEWSLETTER_ACTION:
        form = f'''<form class="nlform" action="{NEWSLETTER_ACTION}" method="post" target="_blank">
          <input type="email" name="{NEWSLETTER_FIELD}" placeholder="your@email.ie" required aria-label="Email address">
          <button type="submit">Subscribe</button>
        </form>'''
    else:
        form = '''<form class="nlform">
          <input type="email" placeholder="your@email.ie" required aria-label="Email address">
          <button type="submit">Subscribe</button>
        </form>'''
    if compact:
        return f'''<div class="nlbar">
          <div class="nltext"><b>Two emails a week.</b> Monday round-up, Friday preview.</div>
          {form}
        </div>'''
    return f'''<div class="nlbox">
      <div class="nltag">Newsletter</div>
      <h3 class="nlh">Never miss what happens to an Irish footballer</h3>
      <div class="nlcols">
        <div class="nlcol"><div class="nlday">Monday</div>
          <p>How every Irish player got on at the weekend, plus what's coming up — with a proper look at the midweek games that usually go unnoticed.</p></div>
        <div class="nlcol"><div class="nlday">Friday</div>
          <p>Who's playing this weekend, a recap of the weekday matches, and the latest injury news.</p></div>
      </div>
      {form}
      <div class="nlfine">Free. No spam. Unsubscribe any time.</div>
    </div>'''


def week_activity():
    """Goals and assists from the last few days, split abroad / League of Ireland."""
    import datetime
    ftdates = set()
    for m in MATCHES:
        if (m.get("status") or "") != "ft": continue
        k = (m.get("kickoff") or "")
        try:
            d = datetime.datetime.fromisoformat(k.replace("Z","+00:00"))
        except Exception:
            continue
        if (datetime.datetime.now(datetime.timezone.utc) - d).days <= 5:
            ftdates.add(d.strftime("%d %b").lstrip("0"))

    def norm(s): return (s or "").strip().lstrip("0")
    out = []
    for p in PLAYERS:
        # results carry no year, so "16 Aug" from last season looks identical to
        # this one — only consider each player's most recent handful of games
        for (d, opp, sc, comp, mins, g, a, *_x) in p["results"][-6:]:
            if norm(d) not in ftdates: continue
            if not (g or a): continue
            club = (p["club"] or "").replace(" FC","")
            out.append(dict(p=p, g=g, a=a, opp=(opp or "").replace(" FC",""),
                            score=sc, comp=comp,
                            line=f'{club} {sc} {(opp or "").replace(" FC","")}' if sc else f'v {opp}',
                            loi=(p["tier"] == "loi")))
    out.sort(key=lambda x: (-x["g"], -x["a"]))
    return out

# ================= HOME =================
# Exact names only. Substring matching put "First Division B" (Belgium) and
# "Northern/Southern/Isthmian Premier Division" (English non-league) in the
# League of Ireland filter.
LOI_EXACT = {"premier division","first division","fai cup",
             "president's cup","presidents cup","fai president's cup"}
LOI_PART  = ("league of ireland",)
def is_loi_match(m, squad):
    """A League of Ireland game: nearly every player is Irish, so list them compactly."""
    comp=(m.get("competition") or "").strip().lower()
    if comp in LOI_EXACT or any(c in comp for c in LOI_PART): return True
    tiers=[p.get("tier","") for p in squad]
    return bool(tiers) and sum(1 for t in tiers if t=="loi") > len(tiers)/2

def _pmap():
    return {p["slug"]: p for p in PLAYERS}

def _load_fmids():
    """match -> fotmob id, read from scraper/match_index.json. Every entry's
       url carries the id of the exact leg in its fragment. The id is what
       /api/live asks the source about, so no slug matching is ever involved
       in patching a live score."""
    path = os.path.join(HERE, "..", "scraper", "match_index.json")
    out = {}
    if not os.path.exists(path):
        return out
    try:
        idx = json.load(open(path, encoding="utf-8"))
    except Exception:
        return out
    for e in idx:
        url = e.get("url") or ""
        frag = url.rsplit("#", 1)
        if len(frag) != 2 or not frag[1].isdigit():
            continue
        k = ((e.get("kickoff") or "")[:16],
             _club_key(e.get("home")), _club_key(e.get("away")))
        out[k] = frag[1]
        out[((e.get("kickoff") or "")[:10],) + k[1:]] = frag[1]
    return out

def fotmob_id(m):
    if not FMIDS:
        FMIDS.update(_load_fmids() or {"": ""})   # sentinel so a missing file loads once
    k = ((m.get("kickoff") or "")[:16], _club_key(m.get("home")), _club_key(m.get("away")))
    return FMIDS.get(k) or FMIDS.get(((m.get("kickoff") or "")[:10],) + k[1:], "")

# How much a competition is worth when picking which two matches lead the
# homepage. Matched longest-name-first, so "Champions League Qualification"
# doesn't get scored as "Champions League".
COMP_TIER = {
    "champions league": 100, "premier league 2": 40, "premier league": 96,
    "laliga": 92, "la liga": 92, "serie a": 92, "bundesliga": 92, "ligue 1": 92,
    "europa league": 86,
    "eredivisie": 72, "liga portugal": 72, "super lig": 70, "süper lig": 70,
    "premiership": 68, "championship": 68,
    "conference league": 76,
    "major league soccer": 58, "jupiler pro league": 62, "serie b": 56,
    "premier division": 62, "fai cup": 60,
    "league one": 48, "efl cup": 54, "fa cup": 54,
    "first division": 50, "nb i": 44, "first professional league": 42,
    "2. liga": 44, "ligue 2": 46,
    "league two": 40, "usl championship": 40,
    "national league": 32,
    "1. deild": 30, "isthmian": 30, "national league cup": 30,
}
COMP_DEFAULT = 35

def comp_score(name):
    n = (name or "").strip().lower()
    for key in sorted(COMP_TIER, key=len, reverse=True):
        if key in n:
            v = COMP_TIER[key]
            # a qualifying round is a rung below the competition proper
            return int(v * 0.82) if "qualification" in n or "qualifying" in n else v
    return COMP_DEFAULT

def player_pull(p):
    """Roughly how much of a draw one player is. Caps are the clearest signal
       of a name people know; form and playing in a top division top it up."""
    caps = 0
    if p.get("intl_senior") and p["intl_senior"].get("caps"):
        caps = min(int(p["intl_senior"]["caps"]), 60)
    try: rating = float(p.get("rating") or 0)
    except ValueError: rating = 0.0
    return (caps * 1.2
            + (max(0.0, rating - 6.5) * 20)
            + (12 if p.get("tier") == "abroad-top" else 0))

def match_pull(m, squad):
    """What makes a match worth leading with: the competition, the biggest name
       in it, and how many of ours are involved — the last with heavily
       diminishing returns, so twenty League of Ireland players don't bury a
       Champions League tie, but do beat one man in the Bulgarian second tier."""
    import math
    best = max((player_pull(p) for p in squad), default=0.0)
    return round(comp_score(m.get("competition")) + best * 1.5
                 + 7 * math.log2(1 + len(squad)), 1)

SQUAD_LIST_AT = 8      # this many tracked players is a squad, not a teamsheet

def _lineup_players(m, pmap):
    """The tracked players actually named for this match, starters first then
       bench, in the order they appear on the teamsheet. Empty when there's no
       lineup for it yet."""
    lu = _match_lineup(m)
    if not lu: return []
    out, seen = [], set()
    for role in ("start", "bench"):
        for side in ("home", "away"):
            sd = lu.get(side)
            if not sd: continue
            for pl in sd[role]:
                slug = ALIAS.get(pl["slug"], pl["slug"])
                if not slug or slug in seen: continue
                p = pmap.get(slug)
                if p:
                    seen.add(slug); out.append(p)
    return out

def lineup_settles(m):
    """True when we hold a real teamsheet for BOTH sides.

       One sheet is not enough. If we have Voitsberg's eleven but not
       Liefering's, a Liefering player being absent from what we hold tells us
       nothing — he simply isn't on the sheet we happen to have. Both sides
       out, and an absence is a real absence."""
    lu = _match_lineup(m) or {}
    return sum(1 for sd in lu.values()
               if len(sd.get("start") or []) >= 7) >= 2

def match_squad(m, pmap=None, settled=True):
    """The tracked players attached to a match, resolved to player records.

       The scraper attaches every tracked player at both clubs, not a teamsheet.
       Abroad that's one or two names. At an Irish club it's the whole squad —
       first team plus academy plus whoever else the feed lists — so a card
       saying '31 Irish players' reads like a lineup when it isn't one. Past
       SQUAD_LIST_AT we call it a squad list: drop anyone with no senior minutes
       this season, and put the most-played first so the faces shown are the
       ones actually likely to be involved.

       Note this is separate from whether it's a League of Ireland fixture — a
       Rovers game in Europe is a squad list too.
       Returns (squad, is_loi, is_squad_list)."""
    pmap = pmap or _pmap()
    seen, squad = set(), []
    for s in [x.strip() for x in (m.get("players") or "").split(";")
              if x.strip() and x.strip() not in MISMATCHED]:
        s = ALIAS.get(s, s)                       # merged duplicates point at one slug
        if s in seen: continue
        p = pmap.get(s)
        if p:
            seen.add(s); squad.append(p)
    loi = is_loi_match(m, squad)

    # If there's a teamsheet, that IS the answer. The players column is every
    # tracked player at both clubs, which for an Irish club is the whole roster
    # - thirty-odd names on a card for a game eleven of them will start.
    #
    # And when both sheets are out, an EMPTY answer is still the answer: none
    # of ours made the squad, so the game isn't one of ours either. Callers
    # that just need the page built (rather than the fixture listed) pass
    # settled=False and keep the old club-roster fallback.
    named = _lineup_players(m, pmap)
    if named or (settled and lineup_settles(m)):
        return named, loi, len(named) >= SQUAD_LIST_AT
    sq  = len(squad) >= SQUAD_LIST_AT
    if sq:
        played = [p for p in squad if (p["season"]["mins"] or 0) > 0]
        if played: squad = played
        squad.sort(key=_squad_rank)
        sq = len(squad) >= SQUAD_LIST_AT
    return squad, loi, sq

REGULAR_MINS = 450        # five full games — enough for an average to mean something

def _squad_rank(p):
    """Order a squad list so the names shown first are the ones worth showing:
       regulars by rating, then everyone else by minutes. A 7.3 off two
       substitute appearances shouldn't outrank the captain."""
    try: rating = float(p.get("rating") or 0)
    except ValueError: rating = 0.0
    mins = p["season"]["mins"] or 0
    return (0 if mins >= REGULAR_MINS else 1, -rating, -mins, p["n"])

VENUES, HOME_GROUND = {}, {}
def _register_venues():
    """The events scraper stores the stadium with every finished match. That
       gives (a) the venue for any played match and (b) each club's home
       ground - the place their home games keep happening - which is the best
       available answer for an upcoming fixture."""
    counts = {}
    dated = []
    for mid, evs in EVENTS.items():
        v = next((e.get("venue","") for e in evs if e.get("venue")), "").strip()
        if not v: continue
        VENUES[mid] = v
        # match_id is date-home-v-away
        try:
            rest = mid[11:]
            home = rest.split("-v-")[0]
        except Exception:
            continue
        if home:
            counts.setdefault(home, {}).setdefault(v, 0)
            counts[home][v] += 1
    for home, vc in counts.items():
        HOME_GROUND[home] = max(vc.items(), key=lambda kv: kv[1])[0]

def match_venue(m):
    """Known venue for a played match; the home side's usual ground otherwise."""
    if not VENUES and EVENTS: _register_venues()
    v = VENUES.get(match_id(m))
    if v: return v
    return HOME_GROUND.get(club_slug(m.get("home","")), "")

def match_payload():
    """Every match with at least one tracked player, for the client-side renderer."""
    pmap = _pmap()
    mc = []
    for m in MATCHES:
        if not m.get("kickoff"): continue
        squad, loi, sq = match_squad(m, pmap)
        involved = [dict(slug=p["slug"], n=esc(p["n"]), club=esc(p["club"]),
                         ini=initials(p["n"]), pos=p["pos"],
                         img=(1 if (not p.get("photo") and p["slug"] in HAVE_IMG) else 0),
                         photo=(p.get("photo") or "")) for p in squad]
        if not involved: continue
        mc.append(dict(id=match_id(m), kickoff=m["kickoff"], comp=esc(m.get("competition","")),
                       home=esc(m.get("home","")), away=esc(m.get("away","")),
                       hb=club_id(m.get("home","")), ab=club_id(m.get("away","")),
                       hs=m.get("home_score",""), as_=m.get("away_score",""),
                       status=(m.get("status") or "scheduled"), minute=m.get("minute",""),
                       hp=(m.get("home_pens") or ""), ap=(m.get("away_pens") or ""),
                       loi=(1 if loi else 0), sq=(1 if sq else 0),
                       ven=esc(match_venue(m)),
                       pull=match_pull(m, squad), fmid=fotmob_id(m),
                       players=involved))
    return mc

_EXT_DONE=False
def _ensure_ext():
    global _EXT_DONE
    if not _EXT_DONE:
        _EXT_DONE=True; _extend_matches()

def build_index():
    _ensure_ext()
    GOALS = [p for p in PLAYERS if p["results"] and (p["results"][0][5] or p["results"][0][6])]
    # expiry: an article with `expires` in the past stays on the news page
    # but drops out of the homepage carousel. NOW/LIVE/BREAKING pins first
    # and gets the happening-now treatment.
    import datetime as _dt
    def _unexpired(a):
        e = (a.get("expires") or "").strip()
        if not e: return True
        try:
            exp = _dt.datetime.strptime(e[:16], "%Y-%m-%dT%H:%M") if "T" in e \
                  else _dt.datetime.strptime(e[:10], "%Y-%m-%d") + _dt.timedelta(hours=23, minutes=59)
            return exp > _dt.datetime.utcnow()
        except ValueError:
            return True     # unparseable date never hides an article

    def _is_now(a): return (a.get("tag") or "").strip().upper() in ("NOW","LIVE","BREAKING")

    pool = [a for a in ARTICLES if _unexpired(a)]
    pool.sort(key=lambda a: (0 if _is_now(a) else 1))     # stable: NOW first, then newest
    HEAD = [(a.get("tag",""), a.get("headline",""), a.get("standfirst",""), art_slug(a), _is_now(a), partner_of(a))
            for a in pool[:max(1, _int(setting("carousel_count", "5"), 5))]] \
        or [(t,h,s,sl,False,None) for (t,h,s,sl) in NEWS]
    HEAD_IS_ARTICLE = bool(pool)
    slides = "".join(
      f'<a class="slide{" now" if s[4] else ""}" data-i="{i}" href="{"news/" + s[3] + ".html" if HEAD_IS_ARTICLE else "player/" + s[3] + ".html"}">'
      f'<div class="tag">{"<span class=\"nowdot\"></span>HAPPENING NOW" if s[4] else esc(s[0])}</div>'
      f'<h3>{esc(s[1])}</h3><p>{esc(s[2])}</p></a>'
      for i,s in enumerate(HEAD))
    dots = "".join(f'<button aria-current="{"true" if i==0 else "false"}" data-i="{i}"></button>' for i in range(len(HEAD)))

    gi = ""
    for p in GOALS:
        mins,g,a = p["results"][0][4], p["results"][0][5], p["results"][0][6]
        tags = "".join(f'<span class="gitag">⚽</span>' for _ in range(g)) + \
               "".join(f'<span class="gitag a">🅰</span>' for _ in range(a))
        gi += (f'<a class="gicard" href="{plink(p)}">{avatar(p)}<div class="who">{esc(p["n"])}</div>'
               f'<div class="cl">{esc(p["club"])} · {esc(p["league"])}</div><div class="what">{tags}</div></a>')

    roundup = ""
    for t,label in TIERS.items():
        grp = [p for p in PLAYERS if p["tier"]==t]
        if not grp: continue
        n_played = sum(1 for p in grp if p["results"])
        tag = f'{n_played} played' if n_played else f'{len(grp)} tracked'
        roundup += (f'<div class="tiergroup"><h4><span>{esc(label)}</span><span>{tag}</span></h4>'
                    + "".join(player_row(p) for p in grp) + '</div>')

    # milestones: render a wide pool with who-they-are attributes; the client
    # keeps the first four that are either followed or a name people know
    def _fam(p):
        return 1 if (p.get("intl_senior") or p.get("tier") == "abroad-top") else 0
    # famous names first so they always make the pool; the rest ride along so
    # a followed player's milestone can surface whoever they are
    ms = sorted(milestone_items(), key=lambda m: -_fam(m["p"]))[:40]
    msh = "".join(f'<a class="mscard" href="{plink(m["p"])}" data-slug="{m["p"]["slug"]}" data-fam="{_fam(m["p"])}" style="display:none">'
                  f'<div class="mstag">{esc(m["tag"])}</div>'
                  f'<div class="msn">{esc(m["p"]["n"])}</div><div class="msd">{esc(m["text"])}</div></a>' for m in ms)

    mc = match_payload()

    # ---- abroad / LOI split ----
    abroad = [p for p in PLAYERS if p["tier"].startswith("abroad")]
    loi    = [p for p in PLAYERS if p["tier"] == "loi"]
    def block(title, group, href, limit=9):
        """Lead with the best-rated players this season, not alphabetical."""
        def _r(p):
            try: return float(p.get("rating") or 0)
            except (TypeError, ValueError): return 0.0
        ranked = sorted(group, key=lambda p: (-_r(p), p["n"]))
        rows = "".join(player_row(p) for p in ranked[:limit])
        rated = sum(1 for p in group if _r(p) > 0)
        note = f'<span class="more" style="border:0">top {min(limit, len(ranked))} · season avg rating</span>' if rated else ""
        return (f'<div class="sec"><h2>{title}</h2>{note}'
                f'<a class="more" href="{href}">All {len(group)} →</a></div>'
                f'<div class="tiergroup">{rows}</div>') if group else ""

    news_block = (f'<div class="sec" style="margin-top:8px"><h2>News</h2><a class="more" href="news.html">All news →</a></div>'
                  f'<div class="carousel-wrap"><div class="carousel" id="carousel">{slides}</div>'
                  f'<div class="dots">{dots}</div></div>') if HEAD else ""

    # ---- this week: goals & assists, abroad vs LOI ----
    wk = week_activity()
    def wk_rows(items, kind):
        rows = ""
        for it in items:
            p = it["p"]
            ev = (f'{it["g"]} goal' + ("s" if it["g"] > 1 else "")) if kind == "g" else \
                 (f'{it["a"]} assist' + ("s" if it["a"] > 1 else ""))
            icon = "⚽" if kind == "g" else "🅰"
            rows += (f'<a class="plrow" href="{plink(p)}">{avatar(p,"","sm")}'
                     f'<div class="nm">{esc(p["n"])} '
                     f'<span class="cl">{icon} {ev} · {esc(it["line"])}</span></div>'
                     f'<div class="ev"></div><div class="mn">{rating_chip(p, True)}</div>{star(p)}</a>')
        return rows

    def wk_block(label, arr):
        goals = [x for x in arr if x["g"]][:5]
        assists = [x for x in arr if x["a"] and not x["g"]][:5]
        if not goals and not assists: return ""
        out = f'<div class="wkgroup"><div class="wklabel">{label}</div>'
        if goals:   out += f'<h4 class="wkh">Goals <span>{len(goals)}</span></h4><div class="tiergroup">{wk_rows(goals,"g")}</div>'
        if assists: out += f'<h4 class="wkh">Assists <span>{len(assists)}</span></h4><div class="tiergroup">{wk_rows(assists,"a")}</div>'
        return out + '</div>'

    week_block = ""
    ab = wk_block("Abroad", [x for x in wk if not x["loi"]])
    lo = wk_block("League of Ireland", [x for x in wk if x["loi"]])
    if ab or lo:
        week_block = (f'<div class="sec"><h2>This week</h2>'
                      f'<span class="more" style="border:0">goals &amp; assists</span></div>{ab}{lo}')

    def _next_raw(p):
        return p["fixtures"][0] if p["fixtures"] else None
    fbp = {}
    for p in PLAYERS:
        nx = _next_raw(p)
        fbp[p["slug"]] = dict(
            n=esc(p["n"]), club=esc(p["club"]), ini=initials(p["n"]),
            img=(1 if (not p.get("photo") and p["slug"] in HAVE_IMG) else 0),
            rating=(p.get("rating") or ""),
            nextdate=(nx[0] if nx else ""), nextopp=(esc(nx[1]) if nx else ""),
            nextha=(nx[2] if nx else ""),
            next=next_fixture(p))

    # ---- today / this week, computed server-side ----
    import datetime as _d
    _today = _d.datetime.now(_d.timezone.utc).date()
    def _kd(m):
        try: return _d.datetime.strptime(m["kickoff"][:10], "%Y-%m-%d").date()
        except Exception: return None
    todays = [m for m in mc if _kd(m) == _today]
    live_n = sum(1 for m in todays if m["status"] == "live")
    t_players = len({p["slug"] for m in todays for p in m["players"]})   # a player in two games is one player
    tot_g = sum(x["g"] for x in wk); tot_a = sum(x["a"] for x in wk)
    today_line = (f'<b>{live_n} live now</b> · ' if live_n else "") + \
                 (f'<b>{len(todays)} match{"es" if len(todays)!=1 else ""}</b> with <b>{t_players}</b> Irish players today' if todays else "No Irish players in action today")
    week_line = (f'<b>{tot_g}</b> goal{"s" if tot_g!=1 else ""} and <b>{tot_a}</b> assist{"s" if tot_a!=1 else ""} by Irish players this week' if (tot_g or tot_a) else "")
    nxt_irl = ""
    try:
        upcoming_irl = [f for f in IRELAND.get("senior",{}).get("fixtures",[]) if str(f[0])[:10] >= str(_today)]
        if upcoming_irl:
            d,o,h,cp = upcoming_irl[0]
            nxt_irl = f'Ireland v <b>{esc(o)}</b> · {esc(day_label(d))} · {esc(cp)}'
    except Exception: pass
    by_c = {}
    for p in PLAYERS: by_c.setdefault(player_country(p), []).append(p)
    # "Other" is a bucket, not a country — it sorts last and is the first thing
    # dropped on a narrow screen, where only the top three are shown.
    _order = sorted(by_c, key=lambda c: (c in ("Other", NO_CLUB), -len(by_c[c]), c))[:12]
    _rank = 0
    _tiles = []
    for c in _order:
        cls = "exc"
        if c not in ("Other", NO_CLUB):
            _rank += 1
            if _rank <= 3: cls += " top3"
        _tiles.append(f'<a class="{cls}" href="country/{country_slug(c)}.html">'
                      f'<b>{esc(c)}</b><span>{len(by_c[c])}</span></a>')
    ex = "".join(_tiles)
    ex += '<a class="exc seeall top3" href="clubs.html"><b>See all</b><span>→</span></a>'
    explore = (f'<div class="sec"><h2>Where are the Irish?</h2><a class="more" id="exmore" href="clubs.html">Every country →</a></div>'
               f'<div class="exgrid">{ex}</div>')
    moments = (f'<div class="sec"><h2>Milestones coming up</h2></div><div class="msgrid">{msh}</div>') if msh else ""

    body = f'''

    {news_block}

    <div id="mc-sec">
      <div class="sec"><h2>Match centre</h2><a class="more" id="mc-more" href="fixtures.html" style="display:none">See all →</a></div>
      <div id="mc"></div>
    </div>

    <div id="myplayers-sec" style="display:none">
      <div class="sec"><h2>Your players <span class="cnt" data-fav-count>0</span></h2>
        <a class="more" href="alerts.html">Get alerts →</a></div>
      <div class="tiergroup" id="myplayers"></div>
    </div>
    <div class="followsell" id="myplayers-empty" style="display:none">
      <button class="fsx" id="fsx" aria-label="Hide for now">×</button>
      <div><b>Follow your players</b><p>Tap ★ beside any name. They'll show up here with their next match, and you can get an email when they play, score or get subbed on.</p></div>
      <a class="btn" href="players.html">Pick your players →</a>
    </div>

    <div id="thisweek"></div>
    {week_block}

    {moments}

    {explore}

    <div class="sec" style="margin-top:26px"><h2>Form guide</h2><span class="more" style="border:0">FotMob ratings averaged over this season · <a href="faq.html">how it works</a></span></div>
    {block("Abroad", abroad, "abroad.html")}
    {block("League of Ireland", loi, "league-of-ireland.html")}

    {signup()}
    <script>
      window.FB_PLAYERS={json.dumps(fbp)};
      window.FB_MATCHES={json.dumps(mc)};
      window.FB_SUBSCRIBE_URL={json.dumps(NEWSLETTER_ACTION)};
    </script>
    <script>
    (function(){{
      var track=document.getElementById('carousel');
      if(!track) return;
      var slides=[].slice.call(track.querySelectorAll('.slide'));
      var dots=[].slice.call(document.querySelectorAll('.dots button'));
      var timer, paused=false, raf;

      function current(){{ return Math.round(track.scrollLeft / track.clientWidth); }}
      function paint(){{
        var i=current();
        dots.forEach(function(d,j){{ d.setAttribute('aria-current', j===i ? 'true':'false'); }});
      }}
      function go(i, smooth){{
        track.scrollTo({{left: i*track.clientWidth, behavior: smooth===false ? 'auto':'smooth'}});
      }}
      function next(){{
        if(paused) return;
        var i=current()+1; if(i>=slides.length) i=0;
        go(i);
      }}
      function restart(){{ clearInterval(timer); timer=setInterval(next, 5200); }}

      dots.forEach(function(d,j){{
        d.addEventListener('click', function(e){{ e.preventDefault(); go(j); restart(); }});
      }});

      track.addEventListener('scroll', function(){{
        if(raf) cancelAnimationFrame(raf);
        raf=requestAnimationFrame(paint);
      }}, {{passive:true}});

      ['touchstart','pointerdown','mouseenter'].forEach(function(ev){{
        track.addEventListener(ev, function(){{ paused=true; }}, {{passive:true}});
      }});
      ['touchend','pointerup','mouseleave'].forEach(function(ev){{
        track.addEventListener(ev, function(){{ paused=false; restart(); }}, {{passive:true}});
      }});

      // a drag shouldn't open the article
      var sx=0, sy=0, moved=false;
      track.addEventListener('touchstart', function(e){{
        sx=e.touches[0].clientX; sy=e.touches[0].clientY; moved=false;
      }}, {{passive:true}});
      track.addEventListener('touchmove', function(e){{
        if(Math.abs(e.touches[0].clientX-sx)>8) moved=true;
      }}, {{passive:true}});
      slides.forEach(function(s){{
        s.addEventListener('click', function(e){{ if(moved) e.preventDefault(); }});
      }});

      window.addEventListener('resize', function(){{ go(current(), false); }});
      paint(); restart();
    }})();
    </script>
'''
    return shell("footballers.ie — every Irish professional, tracked",
                 "News, goal involvements and the full weekend round-up for every Irish professional footballer.",
                 "", "index.html", body, canonical="",
                 og=og_card(n="Every Irish footballer,\nin one place.", big=1,
                            l=f"{len(PLAYERS)} players tracked · live scores, ratings, fixtures and lineups",
                            f="footballers.ie", fr="Updated daily"))


def art_slug(a):
    """The article's filename. Whatever gets typed into the slug box — capitals,
       spaces, a stray full stop — becomes a clean url, and because the page,
       the links and the sitemap all come through here they can't disagree."""
    return club_slug((a.get("slug") or "").strip() or a.get("headline") or "article")

def art_link(a, root=""): return f'{root}news/{art_slug(a)}.html'

def art_img(a, root="", cls="artthumb"):
    """No image? Render nothing at all — no placeholder block."""
    if not (a.get("image") or "").strip():
        return ""
    u = a["image"] if a["image"].startswith("http") else f'{root}{a["image"]}'
    return f'<div class="{cls}"><img src="{esc(u)}" alt="" loading="lazy"></div>'

def pretty_date(d):
    try:
        y, m, dd = d.split("-")
        months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
        return f"{int(dd)} {months[int(m)-1]} {y}"
    except Exception:
        return d

def author_slug(name): return club_slug(name or "staff")
WRITERS = {}
def _load_writers():
    for r in _rows("manual/writers.csv"):
        n=(r.get("name") or "").strip()
        if not n: continue
        WRITERS[n.lower()] = dict(name=n, slug=(r.get("slug") or "").strip() or author_slug(n),
                                  bio=(r.get("bio") or "").strip(), photo=(r.get("photo") or "").strip(),
                                  links=[tuple(x.split("|",1)) if "|" in x else (x,x) for x in (r.get("links") or "").split(";") if x.strip()])
_load_writers()

def writer_avatar(w, root="", size="sm"):
    ph = w.get("photo") or ""
    if ph:
        u = ph if ph.startswith("http") else f"{root}{ph}"
        return f'<div class="pavatar {size}"><img src="{esc(u)}" alt="" loading="lazy" onerror="this.parentNode.innerHTML=\'<span>{initials(w["name"])}</span>\'"></div>'
    return f'<div class="pavatar {size}"><span>{initials(w["name"])}</span></div>'

def authors():
    """Every writer profile, plus anyone with a byline but no profile yet."""
    out = {}
    for w in WRITERS.values():
        out[w["slug"]] = dict(w, arts=[])
    for a in ARTICLES:
        n=(a.get("author") or "").strip()
        if not n: continue
        w = WRITERS.get(n.lower())
        slug = w["slug"] if w else author_slug(n)
        out.setdefault(slug, dict(name=n, slug=slug, bio="", photo="", links=[], arts=[]))["arts"].append(a)
    return sorted(out.values(), key=lambda w: -len(w["arts"]))

PARTNERS = {
    "touchline": dict(name="Touchline Studios", url="https://jamescallan3.substack.com", cta="Read more on Substack",
                      blurb="Touchline Studios is James Callan's newsletter on how football clubs grow — governance, money and culture, with fans at the centre. This piece appears on footballers.ie in partnership."),
}
def partner_of(a):
    """Any article can point at where else it lives: a Substack, a paper, a podcast page.
       `partner` is the name shown, `partner_url` the link. 'touchline' has a preset."""
    k = (a.get("partner") or "").strip()
    if not k: return None
    pt = dict(PARTNERS.get(k.lower(), dict(name=k, url="", cta=f"Read more from {k}", blurb="")))
    if (a.get("partner_url") or "").strip(): pt["url"] = a["partner_url"].strip()
    return pt

def art_visual(a, root="", cls="artthumb"):
    """Image if there is one; otherwise the player's photo; otherwise a branded placeholder.
       Never an empty grey box."""
    if (a.get("image") or "").strip():
        u = a["image"] if a["image"].startswith("http") else f'{root}{a["image"]}'
        return f'<div class="{cls}"><img src="{esc(u)}" alt="" loading="lazy"></div>'
    p = next((x for x in PLAYERS if x["slug"] == a.get("player_slug")), None)
    face = ""
    if p and str(a.get("use_player_photo","")).strip().lower() in ("yes","1","true","on"):
        if p.get("photo"): face = p["photo"]
        elif p["slug"] in HAVE_IMG: face = f'{root}img/players/{p["slug"]}.png'
    if face:
        return (f'<div class="{cls} ph"><img src="{esc(face)}" alt="" loading="lazy" class="phface">'
                f'<div class="phtag">{esc(a.get("tag",""))}</div></div>')
    return ""     # no image by default — cards are text-only

def byline(a, root=""):
    n=(a.get("author") or "").strip()
    ws = (WRITERS.get(n.lower()) or {}).get("slug") or author_slug(n)
    return (f'<span class="artby">By <span class="au" data-href="{root}author/{ws}.html">{esc(n)}</span>'
            f' · {esc(pretty_date(a.get("date","")))}</span>') if n else f'<span class="artby">{esc(pretty_date(a.get("date","")))}</span>'

def _ord(n):
    return f'{n}{"th" if 11 <= n % 100 <= 13 else {1:"st",2:"nd",3:"rd"}.get(n % 10, "th")}'

def day_heading(d):
    """'Today', 'Yesterday', else 'Sunday the 24th' - and the full date to sit
       beside it. Anything unparseable keeps whatever the article had."""
    import datetime as _dt
    try:
        day = _dt.date.fromisoformat((d or "").strip()[:10])
    except ValueError:
        return (d or "Undated"), ""
    gap = (_dt.date.today() - day).days
    if gap == 0:   label = "Today"
    elif gap == 1: label = "Yesterday"
    else:          label = f'{day.strftime("%A")} the {_ord(day.day)}'
    return label, pretty_date(day.isoformat())

def by_day(arts):
    """[(date, [articles])] newest day first, keeping the running order the
       admin set inside each day."""
    days, order = {}, []
    for a in arts:
        k = (a.get("date") or "").strip()[:10]
        if k not in days:
            days[k] = []; order.append(k)
        days[k].append(a)
    order.sort(key=lambda k: (k == "", k), reverse=True)
    return [(k, days[k]) for k in order]

def art_card(a, root="", kind="card"):
    t=esc(a.get("tag","")); h=esc(a.get("headline","")); sf=esc(a.get("standfirst",""))
    pt = partner_of(a); pc = ""; pb = ""
    if kind=="lead":
        vis = art_visual(a,root,"nleadimg")
        return (f'<a class="nlead{pc}{" noimg" if not vis else ""}" href="{art_link(a,root)}" data-tag="{t}">{vis}'
                f'<div class="nleadbody"><div class="arttag">{t}{pb}</div><h2>{h}</h2><p>{sf}</p>{byline(a,root)}</div></a>')
    if kind=="row":
        return (f'<a class="nrow{pc}" href="{art_link(a,root)}" data-tag="{t}">{art_visual(a,root,"nrowimg")}'
                f'<div><div class="arttag">{t}{pb}</div><h4>{h}</h4>{byline(a,root)}</div></a>')
    vis = art_visual(a,root)
    return (f'<a class="artcard{pc}{" noimg" if not vis else ""}" href="{art_link(a,root)}" data-tag="{t}">{vis}'
            f'<div class="artbody"><div class="arttag">{t}{pb}</div><h4>{h}</h4><p>{sf}</p>{byline(a,root)}</div></a>')

def writers_block(root=""):
    aus = authors()
    if not aus: return ""
    return ('<div class="sbbox"><div class="sbh">Writers</div>' +
            "".join(f'<a class="writer" href="{root}author/{w["slug"]}.html">{writer_avatar(w, root)}'
                    f'<div><b>{esc(w["name"])}</b><small>{len(w["arts"])} article{"s" if len(w["arts"])!=1 else ""}</small></div></a>' for w in aus) +
            f'<a class="sbcta" href="#" onclick="window.FB_REPORT&&FB_REPORT();return false">Want to write for us? →</a></div>')

def build_news():
    tags = sorted({(a.get("tag") or "").strip().upper() for a in ARTICLES if a.get("tag")})
    tagbar = ('<div class="tagbar" id="tagbar"><button class="on" data-t="">All</button>' +
              "".join(f'<button data-t="{esc(t)}">{esc(t.title())}</button>' for t in tags) + '</div>') if tags else ""
    if not ARTICLES:
        main = '<div class="emptybox">No articles yet.</div>'
    else:
        # One heading per day, newest first, so scrolling down is going back in
        # time. The newest day keeps the big treatment: lead, then a row of
        # three, then anything else that ran that day.
        main = ""
        for i, (date, arts) in enumerate(by_day(ARTICLES)):
            label, full = day_heading(date)
            main += (f'<div class="sec dayhead" data-day="{esc(date)}"><h2>{esc(label)}</h2>'
                     + (f'<span class="more" style="border:0">{esc(full)}</span>' if full else "")
                     + '</div>')
            block, rest = "", arts
            if i == 0:
                block = art_card(arts[0], kind="lead")
                # a lone card stranded in a three-up grid looks like a mistake;
                # below two it reads better as a row
                if len(arts) > 2:
                    block += ('<div class="artgrid ntop">'
                              + "".join(art_card(a) for a in arts[1:4]) + '</div>')
                    rest = arts[4:]
                else:
                    rest = arts[1:]
            if rest:
                block += ('<div class="nlist">'
                          + "".join(art_card(a, kind="row") for a in rest) + '</div>')
            main += f'<div class="daygroup">{block}</div>'
        main += '<div class="emptybox" id="tagempty" style="display:none">Nothing under that tag yet.</div>'
    side = (f'<aside class="nside">{writers_block()}'
            f'<div class="sbbox"><div class="sbh">Newsletter</div><p class="sbp">Every Irish pro, in your inbox twice a week. Monday round-up, Friday preview.</p>{signup(compact=True)}</div>'
            f'</aside>')
    body = f'''
    <div class="pagehead"><h1>News</h1><p>Reporting on Irish players at home and abroad.</p></div>
    {tagbar}
    <div class="nwrap"><div class="nmain">{main}</div>{side}</div>
    <script>
    (function(){{
      document.addEventListener('click',function(e){{var au=e.target.closest('.au');if(au){{e.preventDefault();location.href=au.getAttribute('data-href');}}}});
      var bar=document.getElementById('tagbar');if(!bar)return;
      bar.addEventListener('click',function(e){{var b=e.target.closest('button');if(!b)return;
        [].forEach.call(bar.children,function(x){{x.classList.toggle('on',x===b)}});
        var t=b.getAttribute('data-t'),n=0;
        document.querySelectorAll('.nmain [data-tag]').forEach(function(c){{var ok=!t||c.getAttribute('data-tag').toUpperCase()===t;c.style.display=ok?'':'none';if(ok)n++}});
        // a day with nothing left in it loses its heading too
        document.querySelectorAll('.nmain .daygroup').forEach(function(g){{
          var live=g.querySelectorAll('[data-tag]:not([style*="none"])').length;
          g.style.display=live?'':'none';
          var head=g.previousElementSibling;
          if(head&&head.classList.contains('dayhead')) head.style.display=live?'':'none';
        }});
        document.getElementById('tagempty').style.display=n?'none':'';}});
    }})();
    </script>
    '''
    return shell("News — footballers.ie", "Latest news on Irish professional footballers.",
                 "", "news.html", body, canonical="news.html",
                 og=og_card(n="News", l="Reporting on Irish players at home and abroad.",
                            f=f'{len(ARTICLES)} article{"s" if len(ARTICLES) != 1 else ""}'))

def build_author(w):
    cards = '<div class="artgrid">' + "".join(art_card(a, "../") for a in w["arts"]) + '</div>'
    body = f'''
    <a class="crumb" data-back href="../news.html">← News</a>
    <div class="pagehead authhead">{writer_avatar(w, "../", "lg")}
      <div><h1>{esc(w["name"])}</h1><p>{len(w["arts"])} article{"s" if len(w["arts"])!=1 else ""} for footballers.ie</p></div></div>
    {f'<div class="abox wbio">' + "".join(f"<p>{esc(x.strip())}</p>" for x in w["bio"].replace(chr(92)+"n",chr(10)).split(chr(10)) if x.strip()) + '</div>' if w.get("bio") else ""}
    {f'<div class="wlinks">' + "".join(f'<a href="{esc(u.strip())}" target="_blank" rel="noopener">{esc(l.strip())} →</a>' for l,u in w["links"]) + '</div>' if w.get("links") else ""}
    {cards}
    <script>document.addEventListener('click',function(e){{var au=e.target.closest('.au');if(au){{e.preventDefault();location.href=au.getAttribute('data-href');}}}});</script>
    '''
    return shell(f'{w["name"]} — footballers.ie', f'Articles by {w["name"]}.', "../", "news.html", body,
                 canonical=f'author/{w["slug"]}.html',
                 og=og_card(n=w["name"], gw="gold",
                            l=f'{len(w["arts"])} article{"s" if len(w["arts"]) != 1 else ""} for footballers.ie',
                            f="Writer"))

def safe_url(u, root=""):
    """http(s), site-absolute, or a plain relative path. No javascript:, no
       data:, no climbing out of the site with ../"""
    u = (u or "").strip()
    if ".." in u or not u:
        return ""
    if u.startswith(("http://", "https://", "/")):
        return u
    if re.match(r"^[\w][\w./-]*$", u):
        return root + u
    return ""

def _inline(t, root=""):
    """Bold, italic and links inside a line. The text is escaped first, so the
       only markup that survives is the markup we put back."""
    t = esc(t)

    def link(m):
        u = safe_url(m.group(2), root)
        if not u:
            return m.group(0)
        tgt = ' target="_blank"' if u.startswith("http") else ""
        return f'<a href="{esc(u)}" rel="noopener"{tgt}>{m.group(1)}</a>'

    t = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", link, t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", t)
    return t

def article_body(raw, root="../"):
    """A small markdown subset, written by the admin editor or imported from a
       .docx. Deliberately small: headings, images, quotes, rules, and bold /
       italic / links inline. Everything is escaped before any of it is applied,
       so a writer can't put raw HTML on the site by accident or otherwise.

         ## Heading            h2
         ### Heading           h3
         ![caption](path)      figure, caption optional
         > line                pull quote
         - item                list
         ---                   divider
         blank line            new paragraph
    """
    out, para, items = [], [], []

    def flush_list():
        if items:
            out.append("<ul>" + "".join(f"<li>{_inline(x, root)}</li>" for x in items) + "</ul>")
            items.clear()

    def flush():
        flush_list()
        if para:
            out.append("<p>" + "<br>".join(_inline(x, root) for x in para) + "</p>")
            para.clear()

    for line in (raw or "").replace("\\n", "\n").split("\n"):
        line = line.rstrip()
        t = line.strip()
        if not t:
            flush(); continue
        m = re.match(r"^(#{2,3})\s+(.*)$", t)
        if m:
            flush()
            tag = "h2" if len(m.group(1)) == 2 else "h3"
            out.append(f"<{tag}>{_inline(m.group(2), root)}</{tag}>")
            continue
        m = re.match(r"^!\[([^\]]*)\]\(([^)\s]+)\)$", t)
        if m:
            src = safe_url(m.group(2), root)
            if src:
                flush()
                cap = (f'<figcaption>{_inline(m.group(1), root)}</figcaption>'
                       if m.group(1).strip() else "")
                out.append(f'<figure class="artfig"><img src="{esc(src)}" alt="{esc(m.group(1))}"'
                           f' loading="lazy">{cap}</figure>')
                continue
        if t.startswith(">"):
            flush()
            out.append(f'<blockquote>{_inline(t.lstrip("> ").strip(), root)}</blockquote>')
            continue
        if re.fullmatch(r"-{3,}|\*{3,}", t):
            flush(); out.append('<hr class="artrule">'); continue
        m = re.match(r"^[-*]\s+(.*)$", t)
        if m:
            if para: flush()
            items.append(m.group(1)); continue
        flush_list()
        para.append(t)
    flush()
    return "".join(out)


def build_article(a):
    paras = article_body(a.get("body"), "../")
    p = next((x for x in PLAYERS if x["slug"] == a.get("player_slug")), None)
    related = ""
    if p:
        related = (f'<div class="sec"><h2>Player</h2></div><div class="tiergroup">'
                   f'<a class="plrow" href="../player/{p["slug"]}.html">{avatar(p,"../","sm")}'
                   f'<div class="nm">{esc(p["n"])} <span class="cl">{esc(p["club"])}</span></div>'
                   f'<div class="ev"></div><div class="mn">{rating_chip(p, True)}</div>{star(p)}</a></div>')
    hero = ""
    if a.get("image"):
        u = a["image"] if a["image"].startswith("http") else f'../{a["image"]}'
        hero = f'<div class="arthero"><img src="{esc(u)}" alt=""></div>'
    pt = partner_of(a)
    others=[x for x in ARTICLES if x["slug"]!=a["slug"]][:3]
    if pt:
        more = (f'<div class="partnerbox"><div class="pbl">Also published at</div><div class="pbn">{esc(pt["name"])}</div>'
                f'<p>{esc(pt["blurb"])}</p>'
                + (f'<a class="btn" href="{esc(pt["url"])}" target="_blank" rel="noopener">{esc(pt["cta"])} →</a>' if pt["url"] else "")
                + '</div>')
    else: more = ('<div class="sec"><h2>More news</h2><a class="more" href="../news.html">All news →</a></div><div class="artgrid">'
            + "".join(art_card(x,"../") for x in others) + '</div><script>document.addEventListener("click",function(e){var au=e.target.closest(".au");if(au){e.preventDefault();location.href=au.getAttribute("data-href");}});</script>') if others else ""
    body = f'''
    <a class="crumb" data-back href="../news.html">← Back</a>
    <article class="article">
      <div class="arttag">{esc(a.get("tag",""))} · {esc(pretty_date(a.get("date","")))}</div>
      <h1>{esc(a.get("headline",""))}</h1>
      <p class="standfirst">{esc(a.get("standfirst",""))}</p>
      <div class="artmeta">By <a class="lnk" href="../author/{(WRITERS.get((a.get("author") or "").strip().lower()) or {}).get("slug") or author_slug(a.get("author"))}.html">{esc(a.get("author","") or "footballers.ie")}</a>{f' · <a class="lnk" href="{esc(pt["url"])}" target="_blank" rel="noopener">{esc(pt["name"])}</a>' if pt and pt["url"] else ""}</div>
      {hero}
      <div class="artcontent">{paras}</div>
    </article>
    {related}
    {more}
    {signup("../", compact=True)}
    '''
    ap = _pmap().get((a.get("player_slug") or "").strip())
    return shell(f'{a.get("headline","")} — footballers.ie',
                 a.get("standfirst",""), "../", "news.html", body,
                 canonical=f'news/{art_slug(a)}.html',
                 og=og_card(t="article", g=a.get("tag",""), h=a.get("headline",""),
                            s=a.get("standfirst",""), b=a.get("author",""),
                            p=(ap["slug"] if ap and ap["slug"] in HAVE_IMG else ""),
                            pn=(ap["n"] if ap else "")))


# --- where a player actually plays -----------------------------------------
# A club's country is really the country of the league it plays in, so the
# league name decides it whenever the name means one country. Plenty of names
# don't ("Premiership" is Scotland AND Northern Ireland; half of Europe has a
# "2. Liga"), and plenty of players have no league recorded at all - those are
# settled by where the club's ground is, which we already store in clubs.csv.
LEAGUE_COUNTRY = {
    # Ireland
    "premier division": "Ireland", "first division": "Ireland",
    "league of ireland": "Ireland", "loi": "Ireland",
    "league of ireland premier division": "Ireland",
    "league of ireland first division": "Ireland",
    "premier division relegation": "Ireland",
    # Northern Ireland
    "nifl premiership": "Northern Ireland", "irish premiership": "Northern Ireland",
    "nifl championship": "Northern Ireland", "irish cup": "Northern Ireland",
    # England
    "premier league": "England", "championship": "England",
    "league one": "England", "league two": "England",
    "national league": "England", "national league north": "England",
    "national league south": "England", "national league cup": "England",
    "premier league 2": "England", "premier league u18": "England",
    "u18 premier league": "England", "professional development league": "England",
    "efl cup": "England", "carabao cup": "England", "fa cup": "England",
    "efl trophy": "England", "fa trophy": "England", "community shield": "England",
    "isthmian premier division": "England", "isthmian league": "England",
    "northern premier division": "England", "northern premier league": "England",
    "southern premier division central": "England",
    "southern premier division south": "England", "southern league": "England",
    # Scotland
    "scottish premiership": "Scotland", "scottish championship": "Scotland",
    "scottish league one": "Scotland", "scottish league two": "Scotland",
    "highland / lowland": "Scotland", "highland league": "Scotland",
    "lowland league": "Scotland", "scottish cup": "Scotland",
    "scottish league cup": "Scotland",
    # Wales
    "cymru premier": "Wales", "welsh premier league": "Wales",
    # rest of Europe
    "bundesliga": "Germany", "2. bundesliga": "Germany", "3. liga": "Germany",
    "regionalliga": "Germany", "regionalliga bayern": "Germany",
    "regionalliga west": "Germany", "regionalliga nord": "Germany",
    "dfb pokal": "Germany",
    "laliga": "Spain", "la liga": "Spain", "laliga2": "Spain",
    "segunda division": "Spain", "primera federacion": "Spain",
    "segunda federacion": "Spain", "copa del rey": "Spain",
    "serie a": "Italy", "serie b": "Italy", "serie c": "Italy",
    "serie d": "Italy", "coppa italia": "Italy",
    "ligue 1": "France", "ligue 2": "France", "national": "France",
    "championnat national": "France", "coupe de france": "France",
    "eredivisie": "Netherlands", "eerste divisie": "Netherlands",
    "keuken kampioen divisie": "Netherlands", "knvb beker": "Netherlands",
    "jupiler pro league": "Belgium", "belgian pro league": "Belgium",
    "first division b": "Belgium", "challenger pro league": "Belgium",
    "liga portugal": "Portugal", "liga portugal 2": "Portugal",
    "primeira liga": "Portugal", "liga revelacao u23": "Portugal",
    "super lig": "Turkey", "s\u00fcper lig": "Turkey", "1. lig": "Turkey",
    "nb i": "Hungary", "nb ii": "Hungary",
    "veikkausliiga": "Finland", "ykk\u00f6nen": "Finland",
    "eliteserien": "Norway", "obos-ligaen": "Norway",
    "allsvenskan": "Sweden", "superettan": "Sweden",
    "danish superliga": "Denmark", "1st division": "Denmark",
    "besta deild": "Iceland", "besta deild karla": "Iceland",
    "ekstraklasa": "Poland", "i liga": "Poland",
    "fortuna liga": "Czechia", "fnl": "Czechia", "chance liga": "Czechia",
    "nike liga": "Slovakia",
    "admiral bundesliga": "Austria", "austrian bundesliga": "Austria",
    "first professional league": "Bulgaria", "parva liga": "Bulgaria",
    "liga i": "Romania", "superliga romania": "Romania",
    "super league greece": "Greece", "super league": "Greece",
    "swiss super league": "Switzerland", "challenge league": "Switzerland",
    "hnl": "Croatia", "super league 1": "Greece",
    "premier liga": "Russia", "ukrainian premier league": "Ukraine",
    "a lyga": "Lithuania", "virsliga": "Latvia", "meistriliiga": "Estonia",
    "cypriot first division": "Cyprus", "maltese premier league": "Malta",
    # further afield
    "major league soccer": "United States", "mls": "United States",
    "usl championship": "United States", "usl league one": "United States",
    "leagues cup": "United States", "us open cup": "United States",
    "mls next pro": "United States", "canadian premier league": "Canada",
    "liga mx": "Mexico", "a-league": "Australia", "a-league men": "Australia",
    "isuzu ute a-league": "Australia", "j1 league": "Japan", "j2 league": "Japan",
    "k league 1": "South Korea", "chinese super league": "China",
    "indian super league": "India", "thai league 1": "Thailand",
    "saudi pro league": "Saudi Arabia", "uae pro league": "United Arab Emirates",
    "qatar stars league": "Qatar", "premier soccer league": "South Africa",
    "betway premiership": "South Africa", "npfl": "Nigeria",
    "brasileirao": "Brazil", "serie a betano": "Brazil",
    "liga profesional": "Argentina",
}
# Names that mean different countries in different places: let the ground decide.
AMBIGUOUS_LEAGUES = {
    "premiership", "league cup", "premier league relegation group",
    "premier league relegation", "1. liga", "2. liga", "2. deild", "1. deild",
    "superliga", "prva liga", "v-league", "first league", "super liga",
    "cup", "league", "division 1", "division 2", "reserve league",
}
# Qualifiers a competition name picks up in the feed but that don't change the
# country: "Championship Qualification", "Serie C Grp. C", "... Group 3".
_LEAGUE_TRIM = re.compile(
    r"\s*(?:-\s*)?(?:group [a-z0-9]+|grp\.? *[a-z0-9]+|qualification|qualifying"
    r"|play[- ]?offs?|promotion round|championship round|round \d+)\s*$", re.I)

def _league_key(league):
    l = (league or "").strip().lower()
    for _ in range(3):
        n = _LEAGUE_TRIM.sub("", l).strip(" -\u2013")
        if n == l: break
        l = n
    return l

def country_of(league):
    """Country from the league name alone, "" when the name doesn't settle it."""
    l = _league_key(league)
    if not l or l in AMBIGUOUS_LEAGUES: return ""
    return LEAGUE_COUNTRY.get(l, "")

COUNTRY_REF = [
 # Ireland — spread wide so the border is decided by distance, not a box
 ("Ireland",53.35,-6.26),("Ireland",53.34,-6.38),("Ireland",51.90,-8.47),("Ireland",53.27,-9.05),
 ("Ireland",52.66,-8.62),("Ireland",52.26,-7.11),("Ireland",53.72,-6.35),("Ireland",53.28,-6.13),
 ("Ireland",54.27,-8.47),("Ireland",54.65,-8.11),("Ireland",53.52,-7.34),("Ireland",52.84,-6.94),
 ("Ireland",53.85,-9.30),("Ireland",52.14,-10.27),("Ireland",54.00,-7.33),("Ireland",53.03,-7.30),
 ("Ireland",54.00,-6.40),("Ireland",53.98,-6.55),("Ireland",54.25,-6.97),("Ireland",53.99,-7.36),
 ("Ireland",54.95,-7.73),("Ireland",55.14,-7.45),("Ireland",54.11,-7.72),("Ireland",53.60,-6.19),
 # Northern Ireland
 ("Northern Ireland",54.60,-5.93),("Northern Ireland",54.62,-5.95),("Northern Ireland",55.00,-7.32),
 ("Northern Ireland",54.42,-6.46),("Northern Ireland",54.45,-6.34),("Northern Ireland",54.87,-6.27),
 ("Northern Ireland",55.13,-6.66),("Northern Ireland",54.85,-5.83),("Northern Ireland",54.72,-5.80),
 ("Northern Ireland",54.49,-6.75),("Northern Ireland",54.35,-7.63),("Northern Ireland",54.35,-6.65),
 ("Northern Ireland",54.18,-6.34),("Northern Ireland",54.51,-8.13),("Northern Ireland",54.99,-6.05),
 # Scotland
 ("Scotland",55.86,-4.25),("Scotland",55.95,-3.19),("Scotland",57.15,-2.09),("Scotland",56.46,-2.97),
 ("Scotland",56.40,-3.44),("Scotland",55.60,-4.50),("Scotland",56.00,-3.78),("Scotland",57.48,-4.22),
 ("Scotland",55.78,-3.99),("Scotland",56.12,-3.94),("Scotland",57.54,-2.47),("Scotland",55.07,-3.60),
 ("Scotland",58.59,-3.52),("Scotland",56.82,-5.11),
 # England
 ("England",51.51,-0.13),("England",53.48,-2.24),("England",53.41,-2.98),("England",52.49,-1.89),
 ("England",53.80,-1.55),("England",54.97,-1.61),("England",52.63,-1.13),("England",50.72,-3.53),
 ("England",50.83,-0.14),("England",51.45,-2.59),("England",53.38,-1.47),("England",52.20,0.12),
 ("England",54.90,-2.93),("England",50.37,-4.14),("England",53.96,-1.08),("England",51.75,-1.26),
 ("England",52.63,1.30),("England",50.90,-1.40),("England",53.55,-2.43),("England",54.57,-1.23),
 ("England",51.28,1.08),("England",52.58,-2.13),("England",53.23,-0.54),("England",51.38,-2.36),
 # Wales
 ("Wales",51.48,-3.18),("Wales",51.62,-3.94),("Wales",53.22,-4.13),("Wales",52.41,-4.08),
 ("Wales",53.05,-2.99),("Wales",51.66,-3.02),
 # rest of Europe
 ("France",48.86,2.35),("France",43.30,5.37),("France",45.76,4.84),("France",43.70,7.27),
 ("France",44.84,-0.58),("France",47.22,-1.55),("France",48.58,7.75),("France",50.63,3.06),
 ("France",43.60,1.44),("France",49.49,0.11),("France",45.19,5.72),
 ("Spain",40.42,-3.70),("Spain",41.39,2.17),("Spain",37.39,-5.99),("Spain",39.47,-0.38),
 ("Spain",43.26,-2.93),("Spain",37.63,-0.84),("Spain",36.72,-4.42),("Spain",42.88,-8.54),
 ("Portugal",38.72,-9.14),("Portugal",41.15,-8.61),("Portugal",37.02,-7.93),("Portugal",40.21,-8.43),
 ("Italy",41.90,12.50),("Italy",45.46,9.19),("Italy",45.07,7.69),("Italy",40.85,14.27),
 ("Italy",44.49,11.34),("Italy",43.77,11.26),("Italy",45.44,12.32),("Italy",38.12,13.36),
 ("Italy",42.56,12.64),("Italy",39.31,16.25),("Italy",40.63,17.94),
 ("Germany",52.52,13.40),("Germany",48.14,11.58),("Germany",50.94,6.96),("Germany",50.11,8.68),
 ("Germany",53.55,9.99),("Germany",51.34,12.37),("Germany",51.51,7.47),("Germany",48.78,9.18),
 ("Germany",49.45,11.08),("Germany",51.93,7.63),("Germany",49.28,8.84),("Germany",50.33,10.43),
 ("Germany",54.32,10.14),("Germany",47.99,7.85),
 ("Netherlands",52.37,4.90),("Netherlands",51.92,4.48),("Netherlands",51.44,5.48),
 ("Netherlands",52.09,5.12),("Netherlands",51.35,6.18),("Netherlands",53.22,6.57),
 ("Belgium",50.85,4.35),("Belgium",51.22,4.40),("Belgium",51.05,3.72),("Belgium",50.63,5.57),
 ("Belgium",51.19,3.18),("Belgium",50.46,4.87),
 ("Switzerland",47.38,8.54),("Switzerland",46.20,6.14),("Switzerland",46.95,7.45),("Switzerland",47.56,7.59),
 ("Austria",48.21,16.37),("Austria",47.07,15.44),("Austria",47.80,13.04),("Austria",47.82,13.00),
 ("Austria",47.27,11.39),("Austria",48.31,14.29),
 ("Denmark",55.68,12.57),("Denmark",56.16,10.20),("Denmark",55.40,10.39),("Denmark",57.05,9.92),
 ("Norway",59.91,10.75),("Norway",60.39,5.32),("Norway",63.43,10.40),("Norway",58.97,5.73),
 ("Norway",62.73,7.15),("Norway",69.65,18.96),
 ("Sweden",59.33,18.07),("Sweden",57.71,11.97),("Sweden",55.60,13.00),("Sweden",63.83,20.26),
 ("Finland",60.17,24.94),("Finland",61.50,23.79),("Finland",60.45,22.27),("Finland",62.24,25.75),
 ("Finland",62.79,22.84),("Finland",65.01,25.47),
 ("Iceland",64.15,-21.94),("Iceland",65.68,-18.09),("Iceland",63.44,-19.00),
 ("Faroe Islands",62.01,-6.77),
 ("Poland",52.23,21.01),("Poland",50.06,19.94),("Poland",51.11,17.04),("Poland",54.35,18.65),
 ("Czechia",50.08,14.44),("Czechia",49.20,16.61),("Czechia",49.83,18.28),("Czechia",48.97,14.47),
 ("Slovakia",48.15,17.11),("Slovakia",48.72,21.26),
 ("Hungary",47.50,19.04),("Hungary",47.53,21.63),("Hungary",46.25,20.15),
 ("Slovenia",46.06,14.51),("Slovenia",46.56,15.64),
 ("Croatia",45.81,15.98),("Croatia",43.51,16.44),("Croatia",45.33,18.41),
 ("Serbia",44.79,20.45),("Serbia",45.25,19.83),
 ("Bosnia and Herzegovina",43.86,18.41),("Bosnia and Herzegovina",44.77,17.19),
 ("Romania",44.43,26.10),("Romania",46.77,23.60),("Romania",47.75,26.65),("Romania",45.75,21.23),
 ("Bulgaria",42.70,23.32),("Bulgaria",42.14,24.75),("Bulgaria",43.21,27.91),
 ("Greece",37.98,23.73),("Greece",40.64,22.94),("Greece",38.25,21.73),
 ("Turkey",41.01,28.98),("Turkey",39.93,32.86),("Turkey","38.42",27.13),("Turkey",36.90,30.69),
 ("Turkey",37.00,35.32),("Turkey",40.99,39.72),("Turkey",36.55,32.00),("Turkey",39.75,37.02),
 ("Cyprus",35.17,33.36),("Malta",35.90,14.51),("Luxembourg",49.61,6.13),("Monaco",43.73,7.42),
 ("Gibraltar",36.14,-5.35),("Andorra",42.51,1.52),("San Marino",43.94,12.45),
 ("Albania",41.33,19.82),("North Macedonia",41.99,21.43),("Montenegro",42.44,19.26),
 ("Kosovo",42.66,21.16),("Moldova",47.01,28.86),("Ukraine",50.45,30.52),("Ukraine",49.99,36.23),
 ("Belarus",53.90,27.57),("Lithuania",54.69,25.28),("Latvia",56.95,24.11),("Estonia",59.44,24.75),
 ("Russia",55.76,37.62),("Russia",59.93,30.34),("Georgia",41.72,44.78),("Armenia",40.18,44.51),
 ("Azerbaijan",40.41,49.87),("Kazakhstan",51.13,71.43),("Israel",32.08,34.78),
 # further afield
 ("United States",40.71,-74.01),("United States",34.05,-118.24),("United States",41.88,-87.63),
 ("United States",29.76,-95.37),("United States",47.61,-122.33),("United States",39.74,-104.99),
 ("United States",33.75,-84.39),("United States",42.36,-71.06),("United States",38.90,-77.04),
 ("United States",35.23,-80.84),("United States",44.98,-93.27),("United States",32.78,-96.80),
 ("United States",35.08,-106.65),("United States",36.17,-115.14),("United States",30.27,-97.74),
 ("Canada",43.65,-79.38),("Canada",45.50,-73.57),("Canada",49.28,-123.12),
 ("Mexico",19.43,-99.13),("Mexico",20.67,-103.35),("Mexico",25.69,-100.32),
 ("Australia",-33.87,151.21),("Australia",-37.81,144.96),("Australia",-27.47,153.03),
 ("Australia",-31.95,115.86),("Australia",-34.93,138.60),("Australia",-32.93,151.78),
 ("Australia",-28.00,153.43),("Australia",-35.28,149.13),
 ("New Zealand",-36.85,174.76),("New Zealand",-43.53,172.64),("New Zealand",-41.29,174.78),
 ("Japan",35.68,139.69),("Japan",34.69,135.50),("South Korea",37.57,126.98),("China",39.90,116.41),
 ("China",31.23,121.47),("India",28.61,77.21),("Thailand",13.76,100.50),("Vietnam",21.03,105.83),
 ("Vietnam",10.82,106.63),("Singapore",1.35,103.82),("Malaysia",3.14,101.69),
 ("Indonesia",-6.21,106.85),("Philippines",14.60,120.98),
 ("Saudi Arabia",24.71,46.68),("United Arab Emirates",25.20,55.27),("Qatar",25.29,51.53),
 ("Kuwait",29.38,47.99),("Bahrain",26.23,50.59),("Oman",23.59,58.41),("Iran",35.69,51.39),
 ("Egypt",30.04,31.24),("Egypt",31.20,29.92),("Morocco",33.57,-7.59),("Tunisia",36.81,10.18),
 ("Algeria",36.75,3.06),("South Africa",-26.20,28.05),("South Africa",-33.92,18.42),
 ("South Africa",-29.86,31.02),("Nigeria",6.52,3.38),("Ghana",5.60,-0.19),("Kenya",-1.29,36.82),
 ("Brazil",-23.55,-46.63),("Brazil",-22.91,-43.17),("Argentina",-34.60,-58.38),
 ("Chile",-33.45,-70.67),("Uruguay",-34.90,-56.16),("Colombia",4.71,-74.07),("Peru",-12.05,-77.04),
]

def _country_at(lat, lon):
    """Nearest reference point wins. Enough points near the border that
       Ireland and Northern Ireland come out on the right side of it."""
    try: lat, lon = float(lat), float(lon)
    except (TypeError, ValueError): return ""
    best, bd = "", 1e9
    for name, rlat, rlon in COUNTRY_REF:
        dlat = lat - float(rlat)
        dlon = (lon - float(rlon)) * math.cos(math.radians((lat + float(rlat)) / 2))
        d = dlat * dlat + dlon * dlon
        if d < bd: bd, best = d, name
    return best if bd < 25 else ""

CLUB_PLACE, CLUB_LEAGUE_COUNTRY = {}, {}
_CLUB_NOISE = re.compile(r"\b(fc|afc|cf|sc|ac|f\.c\.|a\.f\.c\.)\b|[^a-z0-9 ]", re.I)
def _cname(n):
    """'Ballymena United FC' and 'Ballymena United' are the same club."""
    n = unicodedata.normalize("NFKD", str(n or "").lower()).encode("ascii","ignore").decode()
    return " ".join(_CLUB_NOISE.sub(" ", n).split())

def _register_places():
    for r in _rows("manual/clubs.csv"):
        c = (r.get("club") or "").strip()
        if c and (r.get("lat") or "").strip() and (r.get("lon") or "").strip():
            where = _country_at(r["lat"], r["lon"])
            if where:
                CLUB_PLACE[c] = where
                CLUB_PLACE.setdefault(_cname(c), where)
    # clubs whose country we know because a team-mate's league says so
    for p in PLAYERS:
        c = (p.get("club") or "").strip()
        k = country_of(p.get("league"))
        if c and k:
            CLUB_LEAGUE_COUNTRY.setdefault(c, k)
            CLUB_LEAGUE_COUNTRY.setdefault(_cname(c), k)

# AS Monaco sit in France's league despite the postcode; anything else
# geography reads wrong goes here too.
CLUB_COUNTRY_FIX = {"Monaco": "France", "AS Monaco": "France"}

NO_CLUB = "No club"
def player_country(p):
    """Where this player plays, best evidence first: the league when its name
       means one country, then where the club's ground is, then the senior club
       for an academy side, then the last domestic league they appeared in."""
    if not CLUB_PLACE: _register_places()
    club = (p.get("club") or "").strip()
    if club.lower().startswith("without club"): club = ""
    parent = ACADEMY_RE.sub("", club).strip()

    for name in (club, parent):
        if name in CLUB_COUNTRY_FIX: return CLUB_COUNTRY_FIX[name]

    c = country_of(p.get("league"))
    if c: return c
    for name in (club, parent, _cname(club), _cname(parent)):
        if name and CLUB_PLACE.get(name): return CLUB_PLACE[name]
    for name in (club, parent, _cname(club), _cname(parent)):
        if name and CLUB_LEAGUE_COUNTRY.get(name): return CLUB_LEAGUE_COUNTRY[name]
    for r in reversed(p.get("results") or []):
        c = country_of(r[3] if len(r) > 3 else "")
        if c: return c
    return NO_CLUB if not club else "Other"

def country_slug(c): return club_slug(c)


MONTHS = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,
          "jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12}



def season_is_current(label):
    """The source mixes formats: '2026/27', '2026', '2025/26', '2025', or blank.
    Work out whether the stats block covers the season we're in now."""
    import datetime
    lab = (label or "").strip()
    if not lab:
        return True                      # nothing said — assume current
    now = datetime.date.today()
    # a split season (Aug-May) is named by the year it started
    start_year = now.year if now.month >= 7 else now.year - 1
    if "/" in lab:                        # 2026/27 or 2026/2027
        head = lab.split("/")[0].strip()
        return head.isdigit() and int(head) == start_year
    if lab.isdigit():                     # calendar-year league: 2026
        return int(lab) == now.year
    return True


def data_stamp():
    """Newest modification time across the data files — shown as 'updated X ago'."""
    import datetime
    newest = 0
    for rel in ("api/players.csv","api/matches.csv","manual/results.csv",
                "manual/fixtures.csv","manual/articles.csv"):
        p = os.path.join(DATA, rel)
        if os.path.exists(p):
            newest = max(newest, os.path.getmtime(p))
    if not newest:
        newest = datetime.datetime.now().timestamp()
    return datetime.datetime.fromtimestamp(newest, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

DATA_STAMP = data_stamp()

def day_label(d):
    """Show dates as '26 Aug' whether the source sends ISO or 'DD Mon'."""
    d = (d or "").strip()
    if not d: return ""
    if len(d) >= 10 and d[4] == "-" and d[7] == "-":
        try:
            y, m, dd = d[:10].split("-")
            mons = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
            return f"{int(dd):02d} {mons[int(m)-1]}"
        except Exception:
            return d
    return d

def date_key(d):
    """Sort 'DD Mon' safely. Anything unexpected sorts last instead of crashing."""
    parts = (d or "").strip().split()
    if len(parts) >= 2:
        try:
            day = int(parts[0])
            mon = MONTHS.get(parts[1][:3].lower(), 13)
            return (mon, day)
        except ValueError:
            pass
    # ISO date, if the scraper ever sends one
    try:
        y, m, dd = (d or "").split("-")
        return (int(m), int(dd))
    except Exception:
        return (99, 99)


FAQ = [
  ("Where does this data come from?",
   "Fotmob, sofascore, transfermarket."),
  ("Who calculates the ratings?",
   "Same answer, Fotmob, sofascore, transfermarket."),
  ("What does a rating of 8.52 actually mean?",
   "to be honest i dont know like we're just taking fotmob's rating."),
  ("Can I compare ratings between leagues?",
   "Yep premier league and the israeli 10th division are completely comparable."),
  ("Which matches are included?",
   "This is a decent question, so the goal is to have every single professional footballer who is eligible for ireland on the site, and all their matches included. At the time of writing we have nearly 700 players on the site and 98% have all of their matches displayed on the site."),
  ("How often is it updated?",
   "There are loads of automated scrapes of data, for make sure that every player\u2019s club is correct once a day, but we update every live match score every 30 seconds."),
  ("How is a player classified as Irish?",
   "As long as they\u2019re eligible to play for ireland they should be here."),
  ("What about Northern Ireland?",
   "You mean the North of Ireland?? Na obviously if they\u2019re not eligible to play for the republic of ireland then they\u2019re not on the site."),
  ("Are the lineups official?",
   "well when a match has started yes they are the correct line up\u2019s but before a match there will either be a predicted line up displayed or the line up from each team\u2019s last match displayed."),
  ("How do I get in touch?",
   "If there\u2019s an issue with the site, you can submit a report, the button\u2019s in the bottom right of every screen. If it\u2019s urgent or you just want to get in touch with us specifically then support@matchweek.ie is your best bet."),
  ("What do you collect about me when I use the site?",
   "visits and actions on the site are measured with an analytics tool (PostHog, stored in the EU); if you give an email \u2014 newsletter, alerts, following a player then your activity is linked to it."),
  ("What do you do with my email?",
   "depends on what you checked when you gave us your email, one or more of these things: we\u2019ll send you newsletter\u2019s, we\u2019ll send you a wide range of player alerts on player\u2019s you follow, again you choose all this when giving us your email and if you want to change your preferences you can submit a report or email privacy@matchweek.ie."),
  ("How do I get my data deleted?",
   "you can submit a report in the report box, or you can email datadeletion@matchweek.ie"),
  ("Do you use cookies?",
   "The analytics tool keeps an identifier in your browser so it can tell a returning visitor from a new one \u2014 that is the only tracking here. No ads, no ad networks, nothing follows you to other sites. The site also saves your followed players and settings in your own browser, not on our servers."),
]

def build_about():
    """The About page merged into the FAQ — this stub keeps old links alive."""
    return ('<!doctype html><meta charset="utf-8">'
            '<meta http-equiv="refresh" content="0; url=/faq.html">'
            '<link rel="canonical" href="https://footballers.ie/faq.html">'
            '<meta name="robots" content="noindex"><title>Moved</title>'
            '<p>This page moved to <a href="/faq.html">the FAQ</a>.</p>')

def build_faq():
    def _faq_id(q):
        return club_slug(q)[:40].strip("-") or "q"
    items = "".join(
        f'<details class="faq" id="{_faq_id(q)}"><summary>{esc(q)}</summary><p>{esc(a)}</p></details>'
        for q, a in FAQ)
    body = (f'<div class="pagehead"><h1>FAQ</h1>'
            f'<p>Straight answers, in the founder\'s own words.</p></div>'
            f'{items}'
            f'<div class="faqfoot">Still not answered? Hit <b>Report</b> in the corner of any page.</div>')
    return shell("FAQ — footballers.ie",
                 "How footballers.ie collects its data, who counts as Irish, and what the site keeps about you.",
                 "", "faq.html", body, canonical="faq.html",
                 og=og_card(n="How it works", gw="gold",
                            l="Where the data comes from, who counts as Irish, and what the site keeps about you.",
                            f="FAQ"))


# rough centre of each country we track, for the map
COUNTRY_POINT = {
 "Ireland":(53.35,-7.7,7), "England":(52.6,-1.3,6), "Scotland":(56.5,-4.2,7),
 "Northern Ireland":(54.6,-6.4,8), "Wales":(52.3,-3.7,8),
 "Italy":(42.8,12.6,6), "France":(46.6,2.4,6), "Germany":(51.2,10.4,6),
 "Netherlands":(52.2,5.3,7), "Belgium":(50.6,4.5,8), "Spain":(40.2,-3.7,6),
 "Portugal":(39.6,-8.0,7), "Turkey":(39.0,35.2,6), "Hungary":(47.2,19.5,7),
 "Denmark":(56.0,10.0,7), "Sweden":(60.0,15.0,5), "Norway":(61.0,9.0,5),
 "Poland":(52.1,19.4,6), "Switzerland":(46.8,8.2,7), "Austria":(47.6,14.1,7),
 "Greece":(39.0,22.0,6), "USA":(39.8,-98.5,4),
 "United States":(39.8,-98.5,4), "Australia":(-25.3,133.8,4),
 "Bulgaria":(42.7,25.5,7), "Czechia":(49.8,15.5,7), "Finland":(61.9,25.7,4),
 "Romania":(45.9,25.0,6), "Slovenia":(46.1,14.8,8), "Vietnam":(16.0,107.8,5),
 "Croatia":(45.1,15.5,7), "Cyprus":(35.1,33.2,9), "Japan":(36.2,138.3,5),
 "Other":(48.0,10.0,4),
}

def build_map():
    """Where are the Irish? — a map you can click into."""
    by_country = {}
    for p in PLAYERS:
        by_country.setdefault(player_country(p), []).append(p)

    points = []
    for c, ps in sorted(by_country.items(), key=lambda kv: -len(kv[1])):
        if c in ("Other", NO_CLUB): continue   # no club, or country unknown — off the map
        if c not in COUNTRY_POINT: continue    # only place a marker where we know the spot
        lat, lon, zoom = COUNTRY_POINT[c]
        clubs = {}
        for p in ps:
            clubs.setdefault(p["club"], 0)
            clubs[p["club"]] += 1
        top = sorted(clubs.items(), key=lambda kv: (-kv[1], kv[0]))
        points.append(dict(
            name=c, lat=lat, lon=lon, zoom=zoom, n=len(ps),
            url=f"country/{country_slug(c)}.html",
            clubs=[dict(name=k, n=v, url=clink(k),
                        lat=(float(CLUBGEO[k]["lat"]) if k in CLUBGEO and CLUBGEO[k].get("lat") else None),
                        lon=(float(CLUBGEO[k]["lon"]) if k in CLUBGEO and CLUBGEO[k].get("lon") else None),
                        town=(CLUBGEO.get(k, {}).get("town") or ""))
                   for k, v in top],
            more=0,
        ))

    total = sum(p["n"] for p in points)
    # players with no club are found by name search only — not shown here
    extra = ""
    legend = "".join(
        f'<button class="cbtn" data-i="{i}"><span class="cn">{esc(p["name"])}</span>'
        f'<span class="cnum">{p["n"]}</span></button>' for i, p in enumerate(points)) + extra

    body = f'''
    <div class="pagehead"><h1>Where are the Irish?</h1>
      <p>{total} tracked players across {len(points)} countries. Tap a marker or a country to zoom in.</p></div>
    <div id="mapwrap">
      <div id="themap"></div>
      <button id="resetmap" title="Back to the full map">Reset</button>
    </div>
    <div class="clegend">{legend}</div>
    <div id="cpanel"></div>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <script>
    var PTS = {json.dumps(points)};
    (function(){{
      if (typeof L === 'undefined') {{
        document.getElementById('mapwrap').innerHTML =
          '<div class="mapfail">Map could not load. The country list below still works.</div>';
        return;
      }}
      var map = L.map('themap', {{ scrollWheelZoom:false, attributionControl:true }}).setView([50,2], 4);
      L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
        attribution:'&copy; OpenStreetMap contributors', subdomains:'abcd', maxZoom:12
      }}).addTo(map);

      function radius(n){{ return Math.max(9, Math.min(34, 7 + Math.sqrt(n)*3.2)); }}

      var panel = document.getElementById('cpanel');
      function openCountry(p){{
        map.flyTo([p.lat, p.lon], p.zoom, {{duration:.7}});
        var pinned = showClubs(p);
        var clubs = p.clubs.map(function(c){{
          return '<a class="crow" href="'+c.url+'"><span>'+c.name+'</span>'+
                 '<b>'+c.n+'</b></a>';
        }}).join('');
        panel.innerHTML =
          '<div class="sec"><h2>'+p.name+'</h2>'+
          '<a class="more" href="'+p.url+'">All clubs →</a></div>'+
          (pinned ? '' : '<div class="cmore">No map pins for these clubs yet — '+
                          'tap a club below.</div>')+
          '<div class="cgrid">'+clubs+'</div>';
        panel.scrollIntoView({{behavior:'smooth', block:'nearest'}});
      }}

      var countryLayer = L.layerGroup().addTo(map);
      var clubLayer = L.layerGroup();
      var current = null;

      function showCountries(){{
        clubLayer.remove();
        countryLayer.addTo(map);
        current = null;
        panel.innerHTML = '';
      }}

      function showClubs(p){{
        countryLayer.remove();
        clubLayer.clearLayers();
        var withGeo = p.clubs.filter(function(c){{ return c.lat && c.lon; }});
        withGeo.forEach(function(c){{
          var m = L.circleMarker([c.lat, c.lon], {{
            radius: Math.max(7, Math.min(20, 5 + Math.sqrt(c.n) * 4)),
            color:'#35D4BF', weight:2, fillColor:'#35D4BF', fillOpacity:.32
          }});
          m.bindTooltip(c.name + ' · ' + c.n + (c.n===1?' player':' players'), {{direction:'top'}});
          m.on('click', function(){{ location.href = c.url; }});
          clubLayer.addLayer(m);
        }});
        clubLayer.addTo(map);
        current = p;
        return withGeo.length;
      }}

      PTS.forEach(function(p, i){{
        // a wide beacon covering the country while zoomed out
        var m = L.circleMarker([p.lat, p.lon], {{
          radius: radius(p.n), color:'#F5C518', weight:2,
          fillColor:'#F5C518', fillOpacity:.22
        }});
        m.bindTooltip(p.name + ' · ' + p.n + (p.n===1?' player':' players'), {{direction:'top'}});
        m.on('click', function(){{ openCountry(p); }});
        countryLayer.addLayer(m);
        p._marker = m;
      }});

      // zooming out on your own goes back to the country view
      map.on('zoomend', function(){{
        if (map.getZoom() <= 4 && current) showCountries();
      }});

      document.querySelectorAll('.cbtn').forEach(function(b){{
        b.addEventListener('click', function(){{ openCountry(PTS[+b.dataset.i]); }});
      }});
      document.getElementById('resetmap').addEventListener('click', function(){{
        map.flyTo([50,2], 4, {{duration:.6}});
        panel.innerHTML = '';
      }});
    }})();
    </script>
    '''
    return shell("Where are the Irish? — footballers.ie",
                 "A map of every country where tracked Irish players are playing.",
                 "", "clubs.html", body, canonical="where-are-the-irish.html",
                 og=og_card(n="Where are the Irish?", gw="gold",
                            l=f"{len(PLAYERS)} tracked players, mapped by the country they play in",
                            f="Map"))

# ================= LIST PAGES =================
def build_list(fname, title, sub, data):
    rows = ""
    for p in data:
        ev, mins = ev_str(p)
        rows += (f'<a class="plrow pl-item" data-name="{esc(p["n"]).lower()} {esc(p["club"]).lower()}" '
                 f'data-pos="{p["pos"]}" data-league="{esc(p["league"])}" '
                 f'data-rating="{p.get("rating","") or 0}" data-goals="{p["season"]["g"]}" '
                 f'href="{plink(p)}">'
                 f'{avatar(p)}'
                 f'<div class="nm">{esc(p["n"])} <span class="cl">{esc(p["club"])}</span></div>'
                 f'<div class="ev">{ev}</div><div class="mn">{rating_chip(p, True)}</div>{star(p)}</a>')
    leagues = sorted(set(p["league"] for p in data))
    lopts = "".join(f'<option>{esc(l)}</option>' for l in leagues)
    body = f'''
    <div class="pagehead"><h1>{esc(title)}</h1><p>{esc(sub)}</p></div>
    <div class="filterbar">
      <input type="search" id="q" placeholder="Search player or club">
      <select id="posf"><option value="">All positions</option><option>GK</option><option>DEF</option><option>MID</option><option>FWD</option></select>
      <select id="lgf"><option value="">All leagues</option>{lopts}</select>
      <select id="sortf">
        <option value="name">A–Z</option>
        <option value="rating">Top rated</option>
        <option value="goals">Most goals</option>
      </select>
    </div>
    <div class="sortnote" id="sortnote" style="display:none">Ratings aren't comparable across
      different leagues — pick a league to rank like for like.</div>
    <div class="tiergroup"><h4><span id="count">{len(data)} players</span><span></span></h4>{rows}</div>
    <div class="emptystate" id="empty">No players match that. Clear the search or try another filter.</div>
    <script>
    var q=document.getElementById('q'),posf=document.getElementById('posf'),lgf=document.getElementById('lgf'),
        items=[].slice.call(document.querySelectorAll('.pl-item')),
        empty=document.getElementById('empty'),count=document.getElementById('count');
    function draw(){{var t=q.value.trim().toLowerCase(),ps=posf.value,lg=lgf.value,s=0;
      items.forEach(function(el){{var ok=(!ps||el.dataset.pos===ps)&&(!lg||el.dataset.league===lg)&&(!t||el.dataset.name.indexOf(t)>-1);
        el.style.display=ok?'':'none';if(ok)s++;}});
      count.textContent=s+' player'+(s===1?'':'s');empty.style.display=s?'none':'block';}}
    var sortf=document.getElementById('sortf'), note=document.getElementById('sortnote');
    function sortRows(){{
      var mode=sortf.value, parent=items.length?items[0].parentNode:null;
      if(!parent) return;
      var arr=items.slice();
      if(mode==='rating') arr.sort(function(a,b){{return (+b.dataset.rating)-(+a.dataset.rating);}});
      else if(mode==='goals') arr.sort(function(a,b){{return (+b.dataset.goals)-(+a.dataset.goals);}});
      else arr.sort(function(a,b){{return a.dataset.name.localeCompare(b.dataset.name);}});
      arr.forEach(function(el){{parent.appendChild(el);}});
      note.style.display = (mode==='rating' && !lgf.value) ? 'block' : 'none';
    }}
    sortf.onchange=function(){{sortRows();draw();}};
    q.oninput=draw;posf.onchange=draw;lgf.onchange=function(){{sortRows();draw();}};
    </script>'''
    return shell(f"{title} — footballers.ie", sub, "", fname, body, canonical=fname,
                 og=og_card(n=title, l=sub, f="Players"))

# ================= CLUBS =================
def build_clubs_index():
    """Top level: pick a country."""
    by_country = {}
    for p in PLAYERS:
        by_country.setdefault(player_country(p), []).append(p)
    order = sorted(by_country, key=lambda c: (-len(by_country[c]), c))
    cards = ""
    for c in order:
        if c in ("Other", NO_CLUB): continue   # no "Other" country tile; clubless -> search
        ps = [p for p in by_country[c] if (p.get("club") or "").strip()]
        if not ps: continue
        leagues = len({p["league"] for p in ps if p["league"] not in ("","—","Other")})
        cards += (f'<a class="clubcard" href="country/{country_slug(c)}.html">'
                  f'<div class="cn">{esc(c)}</div>'
                  f'<div class="cl2">{leagues} league{"s" if leagues!=1 else ""}</div>'
                  f'<div class="cc">{len(ps)} Irish player{"s" if len(ps)!=1 else ""}</div></a>')
    # leagues we hold a live table for, most Irish players first
    lg_counts = {}
    for p in PLAYERS:
        lg = (p.get("league") or "").strip()
        if lg and table_rows_for(lg):
            lg_counts[lg] = lg_counts.get(lg, 0) + 1
    lg_cards = ""
    for lg in sorted(lg_counts, key=lambda x: -lg_counts[x]):
        cn = country_of(lg)
        if not cn:
            continue
        lg_cards += (f'<a class="clubcard" href="league/{club_slug(cn)}-{club_slug(lg)}.html">'
                     f'<div class="cn">{esc(lg)}</div>'
                     f'<div class="cl2">{esc(cn)} · table</div>'
                     f'<div class="cc">{lg_counts[lg]} Irish player{"s" if lg_counts[lg]!=1 else ""}</div></a>')
    lg_html = (f'<div class="sec"><h2>Leagues</h2></div>'
               f'<div class="clubgrid">{lg_cards}</div>'
               f'<div class="sec"><h2>By country</h2></div>') if lg_cards else ""
    body = (f'<div class="pagehead"><h1>Clubs</h1>'
            f'<p>Pick a country, then a league, then a club.</p></div>'
            f'<a class="mapcta" href="where-are-the-irish.html">'
            f'<div><b>Where are the Irish?</b><span>See every country on a map</span></div>'
            f'<span class="go">Open map →</span></a>'
            f'{lg_html}'
            f'<div class="clubgrid">{cards}</div>')
    return shell("Clubs — footballers.ie","Irish players by country, league and club.","", "clubs.html", body,
                 canonical="clubs.html",
                 og=og_card(n="Clubs", l="Every club with a tracked Irish player, by country and league.",
                            f="Clubs"))

def build_country(cname, ps):
    """Second level: leagues within a country."""
    by_league = {}
    for p in ps:
        if not (p.get("club") or "").strip():
            continue                       # clubless: search only, never listed
        lg = (p.get("league") or "").strip()
        if not lg or lg in ("Other", "—"):
            continue                       # no bogus "Other" league bucket
        by_league.setdefault(lg, []).append(p)
    order = sorted(by_league, key=lambda l: (-len(by_league[l]), l))
    shown = sum(len(v) for v in by_league.values())
    cards = ""
    for l in order:
        lp = by_league[l]
        clubs = len({p["club"] for p in lp})
        cards += (f'<a class="clubcard" href="../league/{club_slug(cname)}-{club_slug(l)}.html">'
                  f'<div class="cn">{esc(l)}</div>'
                  f'<div class="cl2">{clubs} club{"s" if clubs!=1 else ""}</div>'
                  f'<div class="cc">{len(lp)} Irish player{"s" if len(lp)!=1 else ""}</div></a>')
    body = (f'<a class="crumb" data-back href="../clubs.html">← Back</a>'
            f'<div class="pagehead"><h1>{esc(cname)}</h1>'
            f'<p>{shown} Irish player{"s" if shown!=1 else ""} across {len(order)} league{"s" if len(order)!=1 else ""}.</p></div>'
            f'<div class="clubgrid">{cards}</div>')
    return shell(f"{cname} — Irish players — footballers.ie",
                 f"Irish players in {cname}, by league.", "../", "clubs.html", body,
                 canonical=f"country/{country_slug(cname)}.html",
                 og=og_card(n=cname, gw="gold",
                            l=f'{len(ps)} Irish player{"s" if len(ps) != 1 else ""} tracked',
                            f="Where are the Irish?"))

def build_league(cname, lname, ps):
    """Third level: clubs within a league."""
    by_club = {}
    for p in ps:
        by_club.setdefault(p["club"], []).append(p)
    order = sorted(by_club, key=lambda c: (-len(by_club[c]), c))
    cards = ""
    for c in order:
        cp = by_club[c]
        cards += (f'<a class="clubcard" href="../{clink(c)}">'
                  f'<div class="cn">{club_badge(c)}{esc(c)}</div>'
                  f'<div class="cl2">{esc(lname)}</div>'
                  f'<div class="cc">{len(cp)} Irish player{"s" if len(cp)!=1 else ""}</div></a>')
    tbl = league_table_html(lname, "../", title="Table")
    body = (f'<a class="crumb" data-back href="../country/{country_slug(cname)}.html">← Back</a>'
            f'<div class="pagehead"><h1>{esc(lname)}</h1>'
            f'<p>{esc(cname)} · {len(ps)} Irish player{"s" if len(ps)!=1 else ""} at {len(order)} club{"s" if len(order)!=1 else ""}.</p></div>'
            f'{tbl}'
            f'<div class="sec"><h2>Irish players by club</h2></div>'
            f'<div class="clubgrid">{cards}</div>')
    return shell(f"{lname} — Irish players — footballers.ie",
                 f"Irish players in the {lname}.", "../", "clubs.html", body,
                 canonical=f"league/{club_slug(cname)}-{club_slug(lname)}.html",
                 og=og_card(n=lname, l=" · ".join(x for x in (
                                f'{len(ps)} Irish player{"s" if len(ps) != 1 else ""} tracked',
                                cname) if x),
                            f="League"))

def build_club(cname, ps):
    league = ps[0]["league"] if ps[0]["league"] not in ("", "—") else ""
    pos = league_position_of(cname, league) if league else ""
    subline = " · ".join(x for x in (
        f"{_ord_pos(pos)} in the {league}" if pos else league,
        f'{len(ps)} Irish player{"s" if len(ps) != 1 else ""} tracked') if x)

    # ---- squad, grouped by position like a team sheet ----
    LINES = [("Goalkeepers", ("GK",)), ("Defenders", ("DEF", "DF")),
             ("Midfielders", ("MID", "MF")), ("Forwards", ("FWD", "FW", "ST"))]
    grouped, used = "", set()
    def _sq_card(p):
        age = f'{p["age"]} yrs' if p.get("age") else ""
        meta = " · ".join(x for x in (age,) if x)
        return (f'<a class="sqpcard" href="../player/{p["slug"]}.html">'
                f'{avatar(p, "../", "sm")}<div class="sq2">'
                f'<b>{esc(p["n"])}</b><span>{esc(meta)}</span></div>'
                f'<div class="mn">{rating_chip(p, True)}</div>{star(p)}</a>')
    for label, codes in LINES:
        grp = [p for p in ps if (p.get("pos") or "").upper() in codes]
        used.update(p["slug"] for p in grp)
        if grp:
            grouped += (f'<div class="sqhead"><b>{label}</b>'
                        f'<span>{len(grp)}</span></div>'
                        f'<div class="squadgrid">'
                        + "".join(_sq_card(p) for p in grp) + '</div>')
    rest = [p for p in ps if p["slug"] not in used]
    if rest:
        grouped += ('<div class="sqhead"><b>Players</b>'
                    f'<span>{len(rest)}</span></div><div class="squadgrid">'
                    + "".join(_sq_card(p) for p in rest) + '</div>')

    # ---- top performers this season ----
    # Only stats earned in THIS shirt, THIS season. A returned loanee's
    # numbers belong to the loan club's page, and last season's numbers
    # belong to nobody's.
    def _here_now(p):
        if not season_is_current(p.get("season_label")):
            return False
        st = (p.get("s_team") or "").strip()
        if st:
            a = re.sub(r"(afc|fc)$", "", re.sub(r"[^a-z0-9]", "", st.lower()))
            b = re.sub(r"(afc|fc)$", "", re.sub(r"[^a-z0-9]", "", cname.lower()))
            if a != b and a not in b and b not in a:
                return False
        return True
    def _leader(key, label):
        cands = [p for p in ps if has_data(p) and _here_now(p)
                 and p["season"].get(key)]
        if not cands:
            return ""
        top = max(cands, key=lambda p: p["season"][key])
        if not top["season"][key]:
            return ""
        return (f'<a class="leader" href="../player/{top["slug"]}.html">'
                f'<b>{top["season"][key]}</b><div class="lwho">'
                f'<span>{esc(top["n"])}</span><i>{label}</i></div></a>')
    def _leader_rt():
        cands = [(p, float(p["rating"])) for p in ps
                 if p.get("rating") and has_data(p) and _here_now(p)
                 and _int(p["season"].get("ap")) >= 3]
        if not cands:
            return ""
        top = max(cands, key=lambda t: t[1])
        return (f'<a class="leader" href="../player/{top[0]["slug"]}.html">'
                f'<b>{top[1]:.2f}</b><div class="lwho">'
                f'<span>{esc(top[0]["n"])}</span><i>Top rated</i></div></a>')
    leaders = (_leader("g", "Goals") + _leader("a", "Assists") + _leader_rt())
    leaders_html = (f'<div class="sec"><h2>Top performers</h2></div>'
                    f'<div class="leaders">{leaders}</div>') if leaders else ""

    # ---- league table around this club ----
    tbl = league_table_html(league, "../", around_club=cname,
                            title="Table") if league else ""
    if tbl:
        _cn = country_of(league)
        if _cn:
            tbl += (f'<a class="lt-foot" href="../league/'
                    f'{club_slug(_cn)}-{club_slug(league)}.html">'
                    f'Full table →</a>')

    fx = ps[0]["fixtures"]
    def _cfx(d, o, h, c):
        # key on the club itself first - keying on the opponent alone can
        # catch the opponent's OTHER match that day
        href = match_href(d, cname) or match_href(d, o)
        tag, at = ("a", f' href="{href}"') if href else ("div", "")
        return (f'<{tag} class="fxrow{" lnk" if href else ""}"{at}>'
                f'<div class="fxd">{esc(day_label(d))}</div><div class="fxo">{esc(o)} '
                f'<span class="ha">{"H" if h=="H" else "A"}</span></div><div class="fxc">{esc(c)}</div></{tag}>')
    fxr = "".join(_cfx(d,o,h,c) for d,o,h,c in fx)

    # recent results from the match centre (finished games this club played)
    _ck = _club_key(cname)
    import datetime as _dt
    _now_iso = _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    _res_rows = ""
    _seen_r = set()
    for m in sorted(MATCHES, key=lambda m: m.get("kickoff",""), reverse=True):
        hk, ak = _club_key(m.get("home","")), _club_key(m.get("away",""))
        if _ck not in (hk, ak) and not (
                len(_ck) > 3 and (_ck in hk or hk in _ck or _ck in ak or ak in _ck)):
            continue
        hs, as_ = str(m.get("home_score","")), str(m.get("away_score",""))
        done = (m.get("status") == "ft") or (hs != "" and as_ != ""
                and (m.get("kickoff","") < _now_iso))
        if not done or hs == "" or as_ == "":
            continue
        mid = match_id(m)
        if mid in _seen_r:
            continue
        _seen_r.add(mid)
        home_side = _ck == hk or (len(_ck) > 3 and (_ck in hk or hk in _ck))
        opp = m.get("away","") if home_side else m.get("home","")
        own, oth = (hs, as_) if home_side else (as_, hs)
        try:
            wdl = "w" if int(own) > int(oth) else ("l" if int(own) < int(oth) else "d")
        except ValueError:
            wdl = "d"
        _res_rows += (f'<a class="fxrow lnk" href="../match/{mid}.html">'
                      f'<div class="fxd">{esc(day_label(m.get("kickoff","")[:10]))}</div>'
                      f'<div class="fxo"><span class="wdl {wdl}">{wdl.upper()}</span> '
                      f'{esc(own)}-{esc(oth)} {esc(opp)} '
                      f'<span class="ha">{"H" if home_side else "A"}</span></div>'
                      f'<div class="fxc">{esc(m.get("competition",""))}</div></a>')
        if len(_seen_r) >= 8:
            break
    results_html = (f'<div class="sec"><h2>Recent results</h2></div>'
                    f'<div class="fxlist">{_res_rows}</div>') if _res_rows else ""
    body = f'''
    <a class="crumb" data-back href="../clubs.html">← Back</a>
    <div class="pagehead"><h1>{club_badge(cname,"lg")}{esc(cname)}</h1><p>{esc(subline)}</p></div>
    {tbl}
    {leaders_html}
    <div class="sec"><h2>{"Squad" if ps[0].get("tier") == "loi" else "Irish players"}</h2><span class="more" style="border:0">{len(ps)}</span></div>
    {grouped}
    {results_html}
    <div class="sec"><h2>Upcoming fixtures</h2></div>
    <div class="fxlist">{fxr or '<div class="emptystate" style="display:block">No fixtures listed.</div>'}</div>
    '''
    faces = [p["slug"] for p in ps if p["slug"] in HAVE_IMG][:4]
    league = ps[0]["league"] if ps[0]["league"] not in ("", "—") else ""
    return shell(f"{cname} — Irish players — footballers.ie",
                 f"Irish professionals at {cname}, plus upcoming fixtures.", "../", "clubs.html", body,
                 canonical=f"club/{club_slug(cname)}.html",
                 og=og_card(t="club", n=cname, cb=club_id(cname),
                            l=" · ".join(x for x in (
                                f'{len(ps)} Irish player{"s" if len(ps) != 1 else ""} tracked',
                                league) if x),
                            p=",".join(faces),
                            m=(len(ps) - len(faces) if faces and len(ps) > len(faces) else "")))

# ================= IRELAND =================
def build_ireland():
    tabs, panels = "", ""
    def rate(p):
        try: return float(p.get("rating") or 0)
        except: return 0.0
    for i,(lvl,info) in enumerate(IRELAND.items()):
        # only players who have actually played at this level
        if lvl == "Senior":
            squad = [p for p in PLAYERS if p["intl_senior"]]
        else:
            squad = [p for p in PLAYERS if any(y[0] == lvl for y in p["intl_youth"])]
        squad.sort(key=lambda p: -rate(p))

        cards = ""
        for p in squad:
            if lvl=="Senior":
                c = p["intl_senior"]["caps"]
                meta = f'{c} caps · {p["intl_senior"]["goals"]} goals' if c is not None else "Capped"
            else:
                y = next(y for y in p["intl_youth"] if y[0]==lvl)
                meta = f'{y[1]} caps · {y[2]} goals' if y[1] is not None else "Played at this level"
            cards += (f'<a class="squadcard" href="{plink(p)}">{avatar(p)}<div class="pos">{p["pos"]}</div>'
                      f'<div class="who">{esc(p["n"])}</div><div class="cl">{esc(p["club"])}</div>'
                      f'<div class="caps">{meta}</div>'
                      f'<div class="sqrate">{rating_chip(p, True)}</div></a>')
        def _ie_row(d, o, mid_extra, tail):
            href = match_href(d, o, "")
            tag, at = ("a", f' href="{href}"') if href else ("div", "")
            return (f'<{tag} class="fxrow{" lnk" if href else ""}"{at}>'
                    f'<div class="fxd">{esc(day_label(d))}</div>'
                    f'<div class="fxo">{esc(o)}{mid_extra}</div>{tail}</{tag}>')
        fxr = "".join(_ie_row(d, o, f' <span class="ha">{h}</span>',
                              f'<div class="fxc">{esc(cp)}</div>')
                      for d,o,h,cp in info["fixtures"])
        rsr = "".join(_ie_row(d, o, "",
                              f'<div class="fxs">{esc(sc)}</div><div class="fxc">{esc(cp)}</div>')
                      for d,o,sc,cp in info["results"])
        tabs += f'<button class="tab {"on" if i==0 else ""}" data-t="{lvl}">{esc(lvl)}</button>'
        panels += f'''<div class="tabpanel {"on" if i==0 else ""}" data-t="{lvl}">
          <div class="sec"><h2>Fixtures</h2></div><div class="fxlist">{fxr or '<div class="emptystate" style="display:block">None listed.</div>'}</div>
          <div class="sec"><h2>Recent results</h2></div><div class="fxlist">{rsr or '<div class="emptystate" style="display:block">None listed.</div>'}</div>
          <div class="sec"><h2>Players capped at this level</h2><span class="more" style="border:0">{len(squad)} · by rating</span></div>
          <div class="squadgrid">{cards or '<div class="emptystate" style="display:block">No tracked players at this level yet.</div>'}</div>
        </div>'''
    body = f'''
    <div class="pagehead"><h1>Republic of <i>Ireland</i></h1><p>Fixtures, results and every tracked player capped at each level.</p></div>
    <div class="tabbar">{tabs}</div>
    {panels}
    <script>
    var tabs=[].slice.call(document.querySelectorAll('.tab')),panels=[].slice.call(document.querySelectorAll('.tabpanel'));
    function openTab(name){{
      var found=false;
      tabs.forEach(function(x){{
        var hit=x.dataset.t===name;
        x.classList.toggle('on',hit); if(hit) found=true;
      }});
      panels.forEach(function(p){{p.classList.toggle('on',p.dataset.t===name)}});
      return found;
    }}
    tabs.forEach(function(t){{t.onclick=function(){{ openTab(t.dataset.t); }}}});
    var want=new URLSearchParams(location.search).get('level');
    if(want) openTab(want);
    </script>'''
    return shell("Republic of Ireland — footballers.ie",
                 "Ireland fixtures, results and capped players at every level.","", "ireland.html", body,
                 canonical="ireland.html",
                 og=og_card(n="Republic of Ireland", gw="gold",
                            l="Fixtures, results and every tracked player capped at each level.",
                            f="Ireland"))

def build_fixtures():
    mc = match_payload()
    body = (f'<div class="pagehead"><h1>Fixtures</h1><p>Every game a tracked Irish player is involved in.</p></div>'
            f'<div class="fxbar"><div class="fxdays" id="fxdays"></div>'
            f'<div class="fxfilt" id="fxfilt"><button data-f="abroad" class="on">Abroad</button>'
            f'<button data-f="loi">League of Ireland</button><button data-f="all">All</button></div></div>'
            f'<div id="fxall"></div>'
            f'<script>window.FB_MATCHES={json.dumps(mc)};</script>')
    return shell("Fixtures — footballers.ie","Every game a tracked Irish player is involved in.","", "fixtures.html", body,
                 canonical="fixtures.html",
                 og=og_card(n="Fixtures", l="Every game a tracked Irish player is involved in.",
                            f="Fixtures"))

# ================= MILESTONES =================
def milestone_items():
    out = []
    for p in PLAYERS:
        c = p["career"]["ap"]
        for mark in (50,100,150,200,250,300,400):
            if 0 < mark - c <= 8:
                out.append(dict(p=p, tag="Club appearances", text=f'{mark-c} away from {mark} career appearances'))
                break
        if False:
            g = p["intl_senior"]["goals"] or 0
            for mark in (5,10,20,30):
                if 0 < mark - g <= 2:
                    out.append(dict(p=p, tag="International goals", text=f'{mark-g} from {mark} Ireland goals'))
                    break
        cg = p["career"]["g"]
        for mark in (25,50,100,150):
            if 0 < mark - cg <= 5:
                out.append(dict(p=p, tag="Career goals", text=f'{mark-cg} from {mark} career goals'))
                break
    return out

def build_milestones():
    items = milestone_items()
    cards = "".join(f'<a class="mscard" href="{plink(m["p"])}"><div class="mstag">{esc(m["tag"])}</div>'
                    f'<div class="msn">{esc(m["p"]["n"])}</div><div class="msd">{esc(m["text"])}</div>'
                    f'<div class="msc">{esc(m["p"]["club"])}</div></a>' for m in items)
    body = (f'<div class="pagehead"><h1>Approaching milestones</h1><p>Players closing in on a round number — caps, goals or appearances.</p></div>'
            f'<div class="msgrid">{cards or EMPTY_MS}</div>')
    return shell("Milestones — footballers.ie","Irish players approaching career and international milestones.","", "milestones.html", body,
                 canonical="milestones.html",
                 og=og_card(n="Approaching milestones", gw="gold",
                            l="Players closing in on a round number of caps, goals or appearances.",
                            f="Milestones"))

# ================= COMPARE =================
def build_compare():
    payload = {p["slug"]: dict(n=p["n"], club=p["club"], league=p["league"], pos=p["pos"], age=p["age"],
                               season=p["season"], career=p["career"],
                               caps=((p["intl_senior"]["caps"] or "—") if p["intl_senior"] else 0),
                               igoals=((p["intl_senior"]["goals"] or "—") if p["intl_senior"] else 0)) for p in PLAYERS}
    opts = "".join(f'<option value="{p["slug"]}">{esc(p["n"])}</option>' for p in PLAYERS)
    body = f'''
    <div class="pagehead"><h1>Compare</h1><p>Put any two tracked players side by side.</p></div>
    <div class="filterbar">
      <select id="a">{opts}</select>
      <select id="b">{opts}</select>
    </div>
    <div id="cmp"></div>
    <script>
    var D={json.dumps(payload)};
    var a=document.getElementById('a'),b=document.getElementById('b'),box=document.getElementById('cmp');
    b.selectedIndex=1;
    var ROWS=[['Club',function(p){{return p.club}}],['League',function(p){{return p.league}}],
      ['Position',function(p){{return p.pos}}],['Age',function(p){{return p.age}}],
      ['Apps (season)',function(p){{return p.season.ap}}],['Goals (season)',function(p){{return p.season.g}}],
      ['Assists (season)',function(p){{return p.season.a}}],['Minutes (season)',function(p){{return p.season.mins}}],
      ['Career apps',function(p){{return p.career.ap}}],['Career goals',function(p){{return p.career.g}}],
      ['Ireland caps',function(p){{return p.caps}}],['Ireland goals',function(p){{return p.igoals}}]];
    function draw(){{
      var A=D[a.value],B=D[b.value];
      var h='<div class="cmpgrid"><div class="cmphead"></div><div class="cmphead">'+A.n+'</div><div class="cmphead">'+B.n+'</div>';
      ROWS.forEach(function(r){{
        var va=r[1](A),vb=r[1](B);
        var na=parseFloat(va),nb=parseFloat(vb);
        var ca=(!isNaN(na)&&!isNaN(nb)&&na>nb)?' win':'',cb=(!isNaN(na)&&!isNaN(nb)&&nb>na)?' win':'';
        h+='<div class="cmpk">'+r[0]+'</div><div class="cmpv'+ca+'">'+va+'</div><div class="cmpv'+cb+'">'+vb+'</div>';
      }});
      box.innerHTML=h+'</div>';
    }}
    a.onchange=draw;b.onchange=draw;draw();
    </script>'''
    return shell("Compare players — footballers.ie","Compare any two Irish professionals side by side.","", "compare.html", body,
                 canonical="compare.html",
                 og=og_card(n="Compare players", l="Any two Irish professionals, side by side.",
                            f="Compare"))


def match_id(m):
    return (m.get("kickoff","")[:10] + "-" + club_slug(m.get("home","")) + "-v-" + club_slug(m.get("away",""))).strip("-")

EV_ICON = {"goal":"⚽","own_goal":"⚽","yellow":"","red":"","second_yellow":"","missed_penalty":"✕"}
EV_LABEL = {"own_goal":"own goal","missed_penalty":"penalty missed","second_yellow":"second yellow","red":"red card","yellow":"yellow card"}
def events_block(m, involved):
    evs = EVENTS.get(match_id(m), [])
    _started = (m.get("status") or "scheduled") in ("live", "ht", "ft")
    _empty_tl = ('<div class="sec"><h2>Timeline</h2></div>'
                 '<div class="timeline"><div class="tlempty">No goals or cards yet.</div></div>'
                 '<div class="rmnote">Irish players in <b class="ir">green</b>. '
                 'Goals, cards and missed penalties only.</div>')
    if not evs:
        return _empty_tl if _started else ""
    venue = next((e.get("venue","") for e in evs if e.get("venue")), "")
    home = m.get("home",""); away = m.get("away","")
    irish = {x["n"] for x in involved} | {p["n"] for p in PLAYERS}
    def _min(e):
        try: return int(str(e.get("minute","")).split("+")[0])
        except ValueError: return 999
    rows = ""
    for e in sorted(evs, key=_min):
        t = e.get("type","") or ""
        if t not in EV_ICON: continue
        side = "h" if (e.get("team","") or "").strip() == home.strip() else "a"
        who = esc(e.get("player",""))
        if e.get("player","") in irish: who = f'<b class="ir">{who}</b>'
        lab = EV_LABEL.get(t, "")
        ic = {"yellow":'<i class="cd y"></i>',"red":'<i class="cd r"></i>',"second_yellow":'<i class="cd y2"></i>'}.get(t, EV_ICON[t])
        cell = f'<span class="evwho">{who}</span>' + (f' <small>{lab}</small>' if lab else "")
        rows += (f'<div class="tl {side} {t}"><div class="tlh">{cell if side=="h" else ""}</div>'
                 f'<div class="tlm">{esc(e.get("minute",""))}\'<span class="tli">{ic}</span></div>'
                 f'<div class="tla">{cell if side=="a" else ""}</div></div>')
    if not rows:
        return _empty_tl if _started else ""
    return (f'<div class="sec"><h2>Timeline</h2>{f"<span class=\"more\" style=\"border:0\">{esc(venue)}</span>" if venue else ""}</div>'
            f'<div class="timeline">{rows}</div>'
            f'<div class="rmnote">Irish players in <b class="ir">green</b>. Goals, cards and missed penalties only.</div>')

STATUS_LABEL = {"confirmed":"Confirmed lineup",
                "predicted":"Predicted lineup",
                "last":"Last lineup",
                "":"Lineup"}

def _lines(side, mirror=False):
    """Split a starting eleven into pitch rows, best source first.

       1. FotMob's coordinates. x is distance up the pitch, y across, both 0-1,
          and every player on a given line shares an x exactly — a 4-3-3 comes
          back as 0.1 / 0.357 / 0.613 / 0.87. That beats any guess.
       2. The formation string, taking the starters in the order they're listed.
       3. Position grouping — GK, defence, midfield, attack.

       `mirror` flips each row left-to-right. The two teams attack in opposite
       directions, so without it one side's right-back ends up above the other's
       right-back instead of facing it."""
    st = side["start"]
    if not st: return []

    def order(rows):
        for r in rows:
            if any(pl["y"] is not None for pl in r):
                r.sort(key=lambda pl: -(pl["y"] if pl["y"] is not None else 0))
            if mirror: r.reverse()
        return [r for r in rows if r]

    if all(pl["x"] is not None for pl in st):
        bands = {}
        for pl in st: bands.setdefault(round(pl["x"], 3), []).append(pl)
        if 2 <= len(bands) <= 6:
            return order([bands[k] for k in sorted(bands)])

    nums = [int(n) for n in re.findall(r"\d+", side.get("formation") or "")]
    if nums and sum(nums) in (len(st) - 1, len(st)):
        rows, i = ([[st[0]]], 1) if sum(nums) == len(st) - 1 else ([], 0)
        for n in nums:
            rows.append(st[i:i+n]); i += n
        return order(rows)

    idx = {"GK":0,"DEF":1,"MID":2,"ATT":3}
    rows = [[] for _ in range(4)]
    for pl in st:
        if not (pl.get("pos") or "").strip(): return []   # no positions either
        rows[idx.get(pl["line"], 2)].append(pl)
    rows = [r for r in rows if r]
    if len(rows) < 2: return []          # one undifferentiated row is not a pitch
    return order(rows)

def _short_names(lu):
    """Surname only, unless two men on the pitch share one — then M. McClean."""
    names = [pl["name"] for side in lu.values() for pl in side["start"] + side["bench"]]
    surnames = {}
    for n in names:
        surnames[n.split()[-1]] = surnames.get(n.split()[-1], 0) + 1
    out = {}
    for n in names:
        parts = n.split()
        last = parts[-1]
        out[n] = f"{parts[0][0]}. {last}" if (surnames[last] > 1 and len(parts) > 1) else last
    return out

def _pitch_player(pl, pmap, short_names, root="../", subs=None):
    p = pmap.get(pl["slug"]) if pl["slug"] else None
    face = (avatar(p, root, "sm") if p else
            f'<span class="lunum">{esc(pl["num"] or "")}</span>')
    short = short_names.get(pl["name"], pl["name"])
    arr = ""
    if subs:
        om = subs["off"].get(_name_key(pl["name"]))
        if om: arr = f'<span class="soff">\u25bc {esc(om)}\u2032</span>'
    inner = (f'{face}<span class="lun">{esc(short)}</span>{arr}')
    if p:
        return (f'<a class="lup ir" href="{root}player/{p["slug"]}.html" '
                f'data-mstat="{p["slug"]}" title="{esc(pl["name"])}">{inner}</a>')
    return (f'<span class="lup" data-mstat="{_ukey(pl["name"])}" '
            f'title="{esc(pl["name"])}">{inner}</span>')

def _bench_name(pl, pmap, root="../", subs=None):
    p = pmap.get(pl["slug"]) if pl["slug"] else None
    num = f'<span class="bn">{esc(pl["num"])}</span>' if pl["num"] else ""
    arr = ""
    if subs:
        k = _name_key(pl["name"])
        if subs["on"].get(k):  arr += f'<span class="son">\u25b2 {esc(subs["on"][k])}\u2032</span>'
        if subs["off"].get(k): arr += f'<span class="soff">\u25bc {esc(subs["off"][k])}\u2032</span>'
    if p:
        return (f'<a class="bp ir" href="{root}player/{p["slug"]}.html" '
                f'data-mstat="{p["slug"]}">{num}{esc(pl["name"])}{arr}</a>')
    return (f'<span class="bp" data-mstat="{_ukey(pl["name"])}">'
            f'{num}{esc(pl["name"])}{arr}</span>')

def _sub_key_min(v):
    try: return int(str(v).split("+")[0])
    except (ValueError, TypeError): return 0

def match_subs(m, lu=None):
    """{'on': {namekey: minute}, 'off': {namekey: minute}} for this match.
       The scraper writes sub_on/sub_off rows in fotmob's swap order (coming
       on first); the teamsheet is the ground truth, so anyone listed as a
       starter can only go OFF and anyone on the bench can only come ON -
       flip any row that disagrees. A bench player with two rows did both."""
    evs = EVENTS.get(match_id(m), [])
    raw = [e for e in evs if e.get("type") in ("sub_on", "sub_off") and e.get("player")]
    if not raw: return {"on": {}, "off": {}}
    starters, bench = set(), set()
    if lu is None: lu = _match_lineup(m)
    for sd in (lu or {}).values():
        starters |= {_name_key(pl["name"]) for pl in sd["start"]}
        bench    |= {_name_key(pl["name"]) for pl in sd["bench"]}
    by_name = {}
    for e in raw:
        by_name.setdefault(_name_key(e["player"]), []).append(e)
    on, off = {}, {}
    for k, lst in by_name.items():
        lst.sort(key=lambda e: _sub_key_min(e.get("minute")))
        if k in starters:
            off[k] = lst[0].get("minute", "")
        elif k in bench:
            on[k] = lst[0].get("minute", "")
            if len(lst) > 1: off[k] = lst[-1].get("minute", "")
        else:                                    # not on the sheet - trust the label
            for e in lst:
                (on if e["type"] == "sub_on" else off)[k] = e.get("minute", "")
    return {"on": on, "off": off}

def _ukey(name):
    import re as _re
    return "u-" + _re.sub(r"[^a-z0-9]+", "-", _name_key(name)).strip("-")

def lineup_block(m, involved):
    """The teamsheet, when the scraper has one. Returns (html, players_left_over)
       so the page can list the rest of the tracked squad underneath."""
    lu = _match_lineup(m)
    if not lu: return "", involved
    subs = match_subs(m, lu)
    pmap = {p["slug"]: p for p in PLAYERS}
    short_names = _short_names(lu)
    named = set()
    for side in lu.values():
        for pl in side["start"] + side["bench"]:
            if pl["slug"]: named.add(pl["slug"])

    # A pitch needs to know where people stood. The upcoming-match file carries
    # coordinates and a formation; the finished-match one carries neither, and
    # its position column is empty, so eleven names would draw as one flat line
    # across the pitch. Fall back to listing the eleven instead — still useful,
    # and it doesn't pretend to a shape it hasn't got.
    drawn = {k: _lines(sd, mirror=(k == "home")) for k, sd in lu.items()}
    halves = []
    if all(drawn.get(k) for k in lu):
        for side_key, cls in (("away","top"), ("home","bot")):
            side = lu.get(side_key)
            if not side: continue
            rows = drawn[side_key]
            if cls == "bot": rows = list(reversed(rows))   # home attacks up the page
            halves.append(
                f'<div class="luhalf {cls}">' +
                "".join('<div class="lurow">' + "".join(_pitch_player(pl, pmap, short_names, subs=subs) for pl in r) + '</div>'
                        for r in rows) + '</div>')

    played = (m.get("status") or "") in ("ft","live")

    def head(side_key):
        side = lu.get(side_key)
        if not side: return '<div class="luteam"></div>'
        st = STATUS_LABEL.get(side["status"] or ("confirmed" if played else ""), "Lineup")
        _tn = side["team"] or m.get(side_key, "")
        _th = club_href(_tn, "../")
        _o = f'<a class="teamlink" href="{_th}">' if _th else ''
        _c = '</a>' if _th else ''
        return (f'<div class="luteam">{_o}{club_badge(_tn,"sm")}'
                f'<b>{esc(_tn)}</b>{_c}'
                f'<span class="luform">{esc(side["formation"])}</span>'
                f'<span class="lustat {esc(lu[side_key]["status"])}">{st}</span></div>')

    kinds = {v.get("status","") for v in lu.values()}
    if kinds == {"confirmed"} or (played and kinds == {""}):
        note = ""                       # the game happened; this was the team
    elif "" in kinds:
        note = "This may be a predicted or last-known eleven rather than a confirmed team."
    else:
        note = "Not a confirmed team — see the label on each side."

    def columns(which, heading):
        cols = []
        for side_key in ("home","away"):
            side = lu.get(side_key)
            if side and side[which]:
                cols.append(f'<div class="benchcol"><h4>{esc(side["team"] or m.get(side_key,""))}'
                            + (f' <span class="luform">{esc(side["formation"])}</span>' if side["formation"] else "")
                            + '</h4>'
                            + "".join(_bench_name(pl, pmap, subs=subs) for pl in side[which]) + '</div>')
        if not cols: return ""
        return f'<div class="sec"><h2>{heading}</h2></div><div class="benchgrid">{"".join(cols)}</div>'

    if halves:
        block = (f'<div class="sec"><h2>Lineups</h2></div>'
                 f'<div class="lucard">{head("away")}'
                 f'<div class="pitch">{"".join(halves)}</div>'
                 f'{head("home")}</div>'
                 + f'<div class="rmnote">{esc(note)} Irish players in <b class="ir">green</b>.</div>')
    else:
        xi = columns("start", "Starting eleven")
        if not xi: return "", involved
        block = xi + f'<div class="rmnote">{esc(note)} Irish players in <b class="ir">white</b>.</div>'

    left = [p for p in involved if p["slug"] not in named]
    return block + columns("bench", "Bench"), left

def squad_groups(players, heading, root="../"):
    """Tracked players split by club, so a League of Ireland match reads as two
       squads instead of one long list."""
    if not players: return ""
    by = {}
    for p in players: by.setdefault(p["club"], []).append(p)
    order = sorted(by, key=lambda c: (-len(by[c]), c))
    out = f'<div class="sec"><h2>{heading}</h2><span class="more" style="border:0">{len(players)}</span></div>'
    for c in order:
        _ch = club_href(c, root)
        _o = f'<a class="teamlink" href="{_ch}">' if _ch else ''
        _c2 = '</a>' if _ch else ''
        out += (f'<div class="sqhead">{_o}{club_badge(c,"sm")}<b>{esc(c)}</b>{_c2}'
                f'<span>{len(by[c])}</span></div><div class="tiergroup">'
                + "".join(
                    f'<a class="plrow" href="{root}player/{p["slug"]}.html" data-mstat="{p["slug"]}">{avatar(p,root,"sm")}'
                    f'<div class="nm">{esc(p["n"])}</div>'
                    f'<div class="ev">{p["pos"]}</div><div class="mn">{rating_chip(p, True)}</div>{star(p)}</a>'
                    for p in by[c])
                + '</div>')
    return out

def match_player_stats(m, involved):
    """What each tracked player did in THIS match, for the tap-a-player panel.
       A played match gets the results.csv row (minutes, goals, assists,
       rating) plus their timeline events; an unplayed one gets season context
       so the panel still says something worth reading."""
    mid = match_id(m)
    date = (m.get("kickoff") or "")[:10]
    home_k, away_k = _club_key(m.get("home")), _club_key(m.get("away"))
    evs = EVENTS.get(mid, [])
    lu = _match_lineup(m)
    subs = match_subs(m, lu)
    out = {}
    for p in involved:
        row = None
        for r in p["results"]:
            if (r[0] or "")[:10] != date: continue
            opp_k = _club_key(r[1])
            if opp_k and (opp_k in (home_k, away_k)
                          or home_k.find(opp_k) >= 0 or away_k.find(opp_k) >= 0):
                row = r; break
            if row is None: row = r          # same day, name variant — take it
        my_evs = [dict(min=e.get("minute",""), type=e.get("type",""))
                  for e in evs
                  if e.get("type") and _name_key(e.get("player")) in
                     {_name_key(p["n"]),
                      _name_key((p.get("tm") or {}).get("full_name") or "")}]
        d = dict(n=p["n"], club=p["club"], pos=p["pos"],
                 ini=initials(p["n"]),
                 img=(1 if (not p.get("photo") and p["slug"] in HAVE_IMG) else 0),
                 photo=(p.get("photo") or ""),
                 srating=(p.get("rating") or ""),
                 sap=p["season"]["ap"], sg=p["season"]["g"], sa=p["season"]["a"],
                 evs=my_evs)
        if row:
            d.update(mins=row[4], g=row[5], a=row[6],
                     rating=(row[7] if len(row) > 7 else ""))
        nk = _name_key(p["n"])
        if subs["on"].get(nk):  d["son"]  = subs["on"][nk]
        if subs["off"].get(nk): d["soff"] = subs["off"][nk]
        out[p["slug"]] = d

    # everyone else on the teamsheet gets a lighter card: name, shirt, what
    # they did in this game (goals, cards, sub on/off) and minutes worked out
    # from the sub times, so tapping any player answers something
    tracked_keys = {_name_key(p["n"]) for p in involved}
    done = (m.get("status") or "") == "ft"
    for side_key, sd in (lu or {}).items():
        team = sd["team"] or m.get(side_key, "")
        for role, pls in (("start", sd["start"]), ("bench", sd["bench"])):
            for pl in pls:
                nk = _name_key(pl["name"])
                if not nk or nk in tracked_keys: continue
                on_m, off_m = subs["on"].get(nk), subs["off"].get(nk)
                pevs = [dict(min=e.get("minute",""), type=e.get("type",""))
                        for e in evs
                        if e.get("type") not in ("sub_on","sub_off")
                        and _name_key(e.get("player")) == nk]
                d = dict(n=pl["name"], club=team, pos=(pl.get("pos") or ""),
                         ini=initials(pl["name"]), u=1, num=(pl.get("num") or ""),
                         evs=pevs)
                if on_m:  d["son"]  = on_m
                if off_m: d["soff"] = off_m
                if done:
                    if role == "start":
                        d["mins"] = _sub_key_min(off_m) if off_m else 90
                    elif on_m:
                        d["mins"] = max(0, 90 - _sub_key_min(on_m))
                out[_ukey(pl["name"])] = d
    return out

def _team_link(name, size="md", right=False):
    inner = (f'<span>{esc(name)}</span>{club_badge(name, size)}' if right
             else f'{club_badge(name, size)}<span>{esc(name)}</span>')
    href = club_href(name, "../")
    return f'<a class="teamlink" href="{href}">{inner}</a>' if href else inner


def build_match(m, involved, squad_list=False):
    hs, as_ = m.get("home_score",""), m.get("away_score","")
    status = (m.get("status") or "scheduled")
    when = f'<span class="ko-local" data-ko="{esc(m.get("kickoff",""))}">{m.get("kickoff","")[11:16]} · {m.get("kickoff","")[:10]}</span>'
    chip = (f'<span class="mcstat live"><i></i>{esc(m.get("minute",""))}\'</span>' if status=="live"
            else '<span class="mcstat ft">Full time</span>' if status=="ft"
            else f'<span class="mcstat soon">{when}</span>')
    pens = (f'<div class="mpens">{esc(m.get("home_pens",""))}–{esc(m.get("away_pens",""))} on penalties</div>'
            if (m.get("home_pens") or "").strip() and (m.get("away_pens") or "").strip() else "")
    scoreline = (f'<div class="mscore">{esc(hs)}<span>–</span>{esc(as_)}</div>{pens}'
                 if status != "scheduled" and str(hs) != "" else
                 f'<div class="mscore ko"><span class="ko-time" data-ko="{esc(m.get("kickoff",""))}">'
                 f'{esc(m.get("kickoff","")[11:16])}</span></div>')
    lineups, rest = lineup_block(m, involved)
    if lineups and rest and lineup_settles(m):
        # both sheets are out and these players are on neither of them —
        # calling that "rest of the squad" says something the teamsheet
        # contradicts
        squads = (squad_groups(rest, "Not in the matchday squad")
                  + '<p class="squadnote">Named in neither the starting eleven '
                    'nor the bench. This game is on their profile because their '
                    'club played it, not because they were involved.</p>')
    elif lineups:
        squads = squad_groups(rest, "Rest of the squad")
    else:
        squads = squad_groups(involved,
                              "Irish players in these squads" if squad_list
                              else "Irish players in this match")
    body = f'''
    <a class="crumb" data-back href="../fixtures.html">← Back</a>
    <div class="matchhead" id="mhead" data-mid="{match_id(m)}">
      <div class="mcrow"><span class="mccomp">{esc(m.get("competition",""))}</span><span id="mchip">{chip}</span></div>
      {f'<div class="mvenue">{esc(match_venue(m))}</div>' if match_venue(m) else ''}
      <div class="mteams">
        <div class="mteam">{_team_link(m.get("home",""), "md")}</div>
        <div id="mscorewrap">{scoreline}</div>
        <div class="mteam right">{_team_link(m.get("away",""), "md", True)}</div>
      </div>
    </div>
    <div id="mtl">{events_block(m, involved)}</div>
    <script>window.FB_MATCHES=[{json.dumps(dict(id=match_id(m), kickoff=m.get("kickoff",""), comp=esc(m.get("competition","")), home=esc(m.get("home","")), away=esc(m.get("away","")), hs=hs, as_=as_, status=status, minute=m.get("minute",""), players=[], loi=0, fmid=fotmob_id(m)))}];
    window.FB_MSTATS={json.dumps(match_player_stats(m, involved))};</script>
    <div class="mactions"><button class="starbtn" data-favm="{match_id(m)}" aria-pressed="false">★ <span>Follow match</span></button>
      <span class="mhint">Email updates when the score changes</span></div>
    {lineups}
    {squads}
    '''
    title = f'{m.get("home","")} v {m.get("away","")} — Irish players'
    n_irish = len(involved)
    ko_txt = ""
    if status == "scheduled" and m.get("kickoff"):
        try:
            import datetime as _d
            _k = _d.datetime.fromisoformat(m["kickoff"].replace("Z", "+00:00"))
            ko_txt = _k.strftime("%a %H:%M")
        except Exception:
            ko_txt = m.get("kickoff", "")[11:16]
    return shell(f"{title} — footballers.ie",
                 f"Irish players involved in {m.get('home','')} v {m.get('away','')}.",
                 "../", "fixtures.html", body, canonical=f"match/{match_id(m)}.html",
                 og=og_card(t="match", h=m.get("home",""), a=m.get("away",""),
                            hb=club_id(m.get("home","")), ab=club_id(m.get("away","")),
                            c=m.get("competition",""), fm=fotmob_id(m),
                            st=status, hs=hs, **{"as": as_},
                            k=ko_txt, v=match_venue(m),
                            f=f'{n_irish} Irish player{"s" if n_irish != 1 else ""}'))

# ================= PLAYER =================

def bio_block(p):
    """Birthplace, height, citizenship, agent, contract — all optional."""
    t = p.get("tm")
    if not t: return ""
    rows = []
    def add(label, value):
        if value: rows.append(f'<div class="biorow"><div class="biol">{label}</div>'
                              f'<div class="biov">{value}</div></div>')

    if t["dob"]:
        try:
            import datetime
            d = datetime.datetime.strptime(t["dob"], "%Y-%m-%d")
            born = d.strftime("%-d %B %Y")
        except (ValueError, TypeError):
            born = t["dob"]
        add("Born", esc(born) + (f' · {esc(t["birthplace"])}' if t["birthplace"] else ""))
    elif t["birthplace"]:
        add("Born", esc(t["birthplace"]))

    add("Height", esc(t["height"]))
    if t["nations"]:
        add("Citizenship", " ".join(f'<span class="nat">{esc(n)}</span>' for n in t["nations"]))
    add("Position", esc(t["position"]))
    if t["foot"]: add("Foot", esc(t["foot"].title()))
    add("Agent", esc(t["agent"]))
    add("At club since", esc(t["joined"]))

    if t["expires"]:
        soon = ""
        if t["expires_iso"]:
            import datetime
            try:
                left = (datetime.datetime.strptime(t["expires_iso"], "%Y-%m-%d")
                        - datetime.datetime.now()).days
                if left < 0:    soon = ' <span class="cexp out">expired</span>'
                elif left < 190: soon = ' <span class="cexp soon">under 6 months</span>'
            except ValueError:
                pass
        add("Contract until", esc(t["expires"]) + soon +
            (f'<span class="copt">{esc(t["option"])}</span>' if t["option"] else ""))
    add("Market value", esc(t["value"]))

    if not rows: return ""
    return ('<div class="sec"><h2>Profile</h2>'
            '<span class="more" style="border:0">updated periodically</span></div>'
            f'<div class="biogrid">{"".join(rows)}</div>')

def result_class(sc):
    try:
        a,b = [int(x) for x in str(sc).replace("–","-").split("-")[:2]]
        return "w" if a>b else "l" if a<b else "d"
    except Exception:
        return ""

def rating_pill(rt):
    if not rt: return '<span class="rt none">—</span>'
    try: v=float(rt)
    except ValueError: return f'<span class="rt none">{esc(rt)}</span>'
    c = "hi" if v>=7.5 else "md" if v>=6.5 else "lo"
    return f'<span class="rt {c}">{v:.1f}</span>'

_MATCH_LOOKUP = None
def match_href(date, team, root="../"):
    """Path to the match page for (date, either team's name), or "" when the
    match centre doesn't hold that game."""
    global _MATCH_LOOKUP
    if _MATCH_LOOKUP is None:
        _MATCH_LOOKUP = {}
        for m in MATCHES:
            k = m.get("kickoff","")[:10]
            for side in ("home","away"):
                _MATCH_LOOKUP.setdefault((k, club_slug(m.get(side,""))), match_id(m))
    mid = _MATCH_LOOKUP.get(((date or "")[:10], club_slug(team)))
    return f"{root}match/{mid}.html" if mid else ""


def match_page_for(p, date, opponent):
    """Find the match page for a player's result row, if the match centre has that game."""
    h = match_href(date, opponent)
    return h.split("/")[-1][:-5] if h else None

def _season_of(date):
    """'2026-08-29' -> '2026/27'. Football seasons turn over in July."""
    d = (date or "").strip()
    if len(d) < 7 or not d[:4].isdigit(): return ""
    y = int(d[:4])
    try: m = int(d[5:7])
    except ValueError: m = 1
    start = y if m >= 7 else y - 1
    return f"{start}/{str(start + 1)[2:]}"


# Where a Transfermarkt position sits on a vertical pitch, attacking upward.
# (short label, left %, top %)
_POS_SPOT = {
    "centre-forward": ("ST", 50, 14), "second striker": ("SS", 50, 24),
    "left winger": ("LW", 16, 22), "right winger": ("RW", 84, 22),
    "attacking midfield": ("AM", 50, 34), "central midfield": ("CM", 50, 48),
    "defensive midfield": ("DM", 50, 60), "left midfield": ("LM", 16, 44),
    "right midfield": ("RM", 84, 44), "left-back": ("LB", 16, 74),
    "right-back": ("RB", 84, 74), "centre-back": ("CB", 50, 78),
    "goalkeeper": ("GK", 50, 92), "sweeper": ("SW", 50, 84),
}
_POS_FALLBACK = {"GK": ("GK", 50, 92), "DEF": ("CB", 50, 78),
                 "MID": ("CM", 50, 48), "FWD": ("ST", 50, 14)}

def _pos_spot(p):
    t = (p.get("tm") or {}).get("position") or ""
    detail = t.split("-", 1)[-1].strip().lower() if "-" in t else t.strip().lower()
    for key, spot in _POS_SPOT.items():
        if key in detail: return spot, (t.split("-", 1)[-1].strip() or None)
    fb = _POS_FALLBACK.get(p.get("pos") or "")
    return (fb, None) if fb else (None, None)


PLIVE_JS = """<script>
/* Live season-stat updates on a player profile. */
(function(){
  var L = window.FB_PLIVE; if (!L || !L.fmid) return;
  function norm(t){ return String(t||'').normalize('NFKD').replace(/[\u0300-\u036f]/g,'')
    .replace(/[^a-z ]/gi,'').toLowerCase().trim(); }
  var me = norm(L.name), lastMin = null, lastStamp = 0,
      involved = false, started = false, stillOn = false, onAt = 0;
  function minutes(){
    if (lastMin != null)
      return stillOn ? lastMin + Math.min(25, Math.floor((Date.now()-lastStamp)/60000)) : lastMin;
    var mm = Math.floor((Date.now() - Date.parse(L.ko)) / 60000);
    return started ? Math.max(0, mm) : Math.max(0, mm - onAt);
  }
  function apply(){
    if (!involved) return;
    var mn = minutes();
    document.querySelectorAll('[data-live]').forEach(function(el){
      var base = parseInt(el.getAttribute('data-base'),10) || 0, k = el.getAttribute('data-live');
      if (k === 'apps')   el.textContent = base + 1;
      else if (k === 'starts') el.textContent = base + (started ? 1 : 0);
      else if (k === 'mins')   el.textContent = base + Math.max(0, mn);
    });
  }
  async function poll(){
    try {
      var r = await fetch('/api/live?ids=' + L.fmid + '&full=1', { cache:'no-store' });
      if (!r.ok) return;
      var d = await r.json(), m = d.matches && d.matches[L.fmid];
      if (!m || m.status === 'scheduled') { involved = false; return; }
      var offAt = null, on = null;
      (m.ev || []).forEach(function(e){
        if (e.type !== 'sub') return;
        if (norm(e.player) === me) offAt = parseInt(e.min, 10);
        if (norm(e.sin) === me)    on    = parseInt(e.min, 10);
      });
      var ps = m.pstats && m.pstats[me];
      involved = !!ps || offAt != null || on != null;
      if (!involved) return;
      started = (on == null);
      onAt = on || 0;
      stillOn = (offAt == null) && m.status !== 'ft';
      lastMin = (ps && ps.min != null) ? ps.min : null;
      lastStamp = Date.now();
      apply();
    } catch (e) {}
  }
  poll(); setInterval(poll, 30000); setInterval(apply, 60000);
})();
</script>"""

def build_player(p):
    s, c = p["season"], p["career"]
    t = p.get("tm") or {}
    badge = "League of Ireland" if p["tier"]=="loi" else ("Abroad · top flight" if p["tier"]=="abroad-top" else "Abroad")

    # ---------------- PROFILE: bio card ----------------
    import datetime as _dt
    dob_disp = ""
    if t.get("dob"):
        try: dob_disp = _dt.datetime.strptime(t["dob"], "%Y-%m-%d").strftime("%-d %b %Y")
        except (ValueError, TypeError): dob_disp = t["dob"]
    cells = []
    def cell(big, small):
        if big: cells.append(f'<div class="bic"><div class="bib">{big}</div><div class="bis">{small}</div></div>')
    import re as _reh
    _hraw = t.get("height") or ""
    _hm = _reh.search(r"(\d+)ft (\d+)in", _hraw)
    cell(esc(f'{_hm.group(1)}\'{_hm.group(2)}"' if _hm else _hraw), "Height")
    if p["age"]: cell(f'{p["age"]} yrs', esc(dob_disp) if dob_disp else "Age")
    nats = t.get("nations") or (["Ireland"] if p["eligible"] else [])
    if nats: cell(esc(nats[0]), "Country" if len(nats) == 1 else esc(" · ".join(nats[1:])))
    spot, pos_label = _pos_spot(p)
    if spot: cell(esc(spot[0]), "Position")
    if t.get("foot") or p["foot"]: cell(esc((t.get("foot") or p["foot"]).title()), "Preferred foot")
    if t.get("value"): cell(esc(t["value"]), "Transfer value")
    bio_card = f'<div class="pcard biocard"><div class="bigrid">{"".join(cells)}</div></div>' if cells else ""

    # ---------------- PROFILE: season summary card ----------------
    if has_data(p):
        stale = "" if season_is_current(p.get("season_label")) else '<span class="stale">last season</span>'
        # One card for the whole season: totals across every competition and
        # every shirt. The badges say WHERE the football was played - most
        # recent shirt on top when a season crossed clubs.
        splits = []
        for _sp in (p.get("s_splits") or "").split(";"):
            _f = _sp.split("|")
            if len(_f) >= 8 and _f[0]:
                splits.append(_f)
        if splits:
            tap = tg = ta = tmins = 0
            wr = wn = 0.0
            for _f in splits:
                _ap = _int(_f[3])
                tap += _ap; tg += _int(_f[4]); ta += _int(_f[5]); tmins += _int(_f[6])
                try:
                    wr += float(_f[7]) * _ap; wn += _ap
                except ValueError:
                    pass
            _rt = (wr / wn) if wn else None
            if _rt is not None:
                _rtchip = ('<span class="rate %s" title="Season average match rating">%.2f</span>'
                           % ("hi" if _rt >= 7.3 else ("md" if _rt >= 6.5 else "lo"), _rt))
            else:
                _rtchip = rating_chip(p)
            # shirts by recency: last match date, newest first
            _seen, shirts = set(), []
            for _f in sorted(splits, key=lambda f: f[8] if len(f) > 8 else "",
                             reverse=True):
                if _f[0] not in _seen:
                    _seen.add(_f[0]); shirts.append((_f[0], _f[1]))
            badges = "".join((badge_by_id(tid) if tid else club_badge(nm))
                             for nm, tid in shirts[:3])
            if len(shirts) > 1:
                badges = f'<span class="badgestack">{badges}</span>'
            names = " · ".join(nm for nm, _ in shirts[:3])
            season_card = f'''<div class="pcard">
      <div class="pchead">{badges}<b>{esc(names)}</b><span class="stale">{esc(p.get("season_label") or SEASON)}</span>{stale}</div>
      <div class="sumgrid">
        <div><b>{tg}</b><span>Goals</span></div>
        <div><b>{ta}</b><span>Assists</span></div>
        <div>{_rtchip}<span>Rating</span></div>
        <div><b data-live="apps" data-base="{tap}">{tap}</b><span>Matches</span></div>
        <div><b data-live="starts" data-base="{s["starts"] or 0}">{stat(p,"s_starts",s["starts"])}</b><span>Started</span></div>
        <div><b data-live="mins" data-base="{tmins}">{tmins}</b><span>Minutes played</span></div>
      </div></div>'''
        else:
            season_card = f'''<div class="pcard">
      <div class="pchead">{club_badge(p["club"])}<b>{esc(p.get("s_league") or p["league"])} {esc(p.get("season_label") or SEASON)}</b>{stale}</div>
      <div class="sumgrid">
        <div><b>{stat(p,"s_goals",s["g"])}</b><span>Goals</span></div>
        <div><b>{stat(p,"s_assists",s["a"])}</b><span>Assists</span></div>
        <div>{rating_chip(p)}<span>Rating</span></div>
        <div><b data-live="apps" data-base="{s["ap"] or 0}">{stat(p,"s_apps",s["ap"])}</b><span>Matches</span></div>
        <div><b data-live="starts" data-base="{s["starts"] or 0}">{stat(p,"s_starts",s["starts"])}</b><span>Started</span></div>
        <div><b data-live="mins" data-base="{s["mins"] or 0}">{stat(p,"s_mins",s["mins"])}</b><span>Minutes played</span></div>
      </div></div>'''
    elif p["slug"] in MISMATCHED:
        season_card = ('<div class="pcard nodata">We haven\'t matched this player to a record on our stats '
            'source yet — the closest one belongs to a different player, so we\'re showing nothing rather '
            'than someone else\'s numbers. Know where to find them? '
            '<a href="#" onclick="window.FB_REPORT&&FB_REPORT();return false">Tell us</a>.</div>')
    else:
        season_card = ('<div class="pcard nodata">We track this player, but our data source doesn\'t cover '
            'their club yet — their profile will fill in as soon as it does. Think that\'s wrong? '
            '<a href="#" onclick="window.FB_REPORT&&FB_REPORT();return false">Report it</a>.</div>')

    # ---------------- PROFILE: position card ----------------
    position_card = ""
    if spot:
        sh, sx, sy = spot
        position_card = f'''<div class="pcard"><div class="pct">Position</div>
      <div class="poswrap"><div class="poslbl"><span class="bis">Primary</span><br><b>{esc(pos_label or sh)}</b></div>
      <div class="minipitch"><i></i><i class="mp2"></i><span class="posdot" style="left:{sx}%;top:{sy}%">{esc(sh)}</span></div></div></div>'''

    # ---------------- PROFILE: contract card ----------------
    contract_card = ""
    if t.get("expires"):
        bar = ""
        try:
            j = _dt.datetime.strptime(t.get("joined") or "", "%d/%m/%Y")
            e = _dt.datetime.strptime(t.get("expires_iso") or "", "%Y-%m-%d")
            total = (e - j).days
            done = (_dt.datetime.now() - j).days
            if total > 0:
                pct = max(2, min(100, round(done * 100 / total)))
                bar = f'<div class="cbar"><i style="width:{pct}%"></i></div>'
        except (ValueError, TypeError):
            pass
        soon = ""
        if t.get("expires_iso"):
            try:
                left = (_dt.datetime.strptime(t["expires_iso"], "%Y-%m-%d") - _dt.datetime.now()).days
                if left < 0: soon = ' <span class="cexp out">expired</span>'
                elif left < 190: soon = ' <span class="cexp soon">under 6 months</span>'
            except ValueError: pass
        contract_card = f'''<div class="pcard"><div class="pct">Contract · {esc(p["club"])}</div>
      <div class="cdates"><span><b>Joined</b> {esc(t.get("joined") or "—")}</span><span><b>Ends</b> {esc(t["expires"])}{soon}</span></div>{bar}</div>'''

    # ---------------- eligibility + international (site-specific, kept) ----------------
    intl = ""
    if p["intl_senior"] or p["intl_youth"]:
        blocks = ""
        if p["intl_senior"]:
            i = p["intl_senior"]
            blocks += (f'<a class="intlblock lnk" href="../ireland.html?level=Senior">'
                       f'<div class="ilvl">Senior <span>{i["caps"]} caps · {i["goals"]} goals · debut {i["debut"]}</span>'
                       f'<span class="go">View squad →</span></div></a>')
        for lvl, caps, goals, since in p["intl_youth"]:
            txt = f'{caps} caps · {goals} goals · from {since}' if caps is not None else "Called up at this level"
            blocks += (f'<a class="intlblock lnk" href="../ireland.html?level={esc(lvl)}">'
                       f'<div class="ilvl">{esc(lvl)} <span>{txt}</span><span class="go">View squad →</span></div></a>')
        intl = f'<div class="sec"><h2>International</h2><a class="more" href="../ireland.html">Ireland hub →</a></div>{blocks}'

    elig = ""
    shown = set()
    tied_to = next((cn for cn, st in p["eligible"] if st == "tied"), None)
    for country, status in p["eligible"]:
        if status == "blocked": cls, note = "elig blocked", "No longer available"
        elif status == "tied":  cls, note = "elig tied", "Cap-tied · committed"
        elif p["intl_youth"] and country == "Republic of Ireland":
            cls, note = "elig youth", "Played underage · can still switch"
        else: cls, note = "elig open", "Eligible · never played"
        shown.add(country.lower()); shown.add(country.lower().replace("republic of ",""))
        elig += f'<div class="{cls}"><span class="ec">{esc(country)}</span><span class="en">{note}</span></div>'
    for nat in t.get("nations", []) or []:
        n = nat.strip()
        if not n or n.lower() in shown or n.lower() in ("ireland","republic of ireland") and "republic of ireland" in shown: continue
        shown.add(n.lower())
        if tied_to: elig += f'<div class="elig off"><span class="ec">{esc(n)}</span><span class="en">Closed · cap-tied elsewhere</span></div>'
        else: elig += f'<div class="elig open"><span class="ec">{esc(n)}</span><span class="en">Also eligible</span></div>'
    tie_note = ("Cap-tied to Ireland — a competitive senior appearance means the other associations below are closed off."
                if p["cap_status"]=="senior_comp" else
                "Youth caps and senior friendlies don't cap-tie a player, so a switch is still possible under FIFA rules."
                if p["cap_status"] in ("youth","senior_friendly") or p["intl_youth"] else
                "Uncapped at any level — free to commit to any association they qualify for.")

    # ---------------- MATCHES tab ----------------
    # p["fixtures"]/p["results"] come from the per-player feed, which misses two
    # things: a match that's ON RIGHT NOW (not a finished result, not a future
    # fixture) and, sometimes, upcoming games the feed didn't return. Fill both
    # from the match centre (MATCHES), which is keyed on the players column.
    import datetime as __dt
    _now = __dt.datetime.now(__dt.timezone.utc)
    def _ko(mm):
        try: return __dt.datetime.fromisoformat((mm.get("kickoff") or "").replace("Z", "+00:00"))
        except ValueError: return None
    _resdays = {(r[0] or "")[:10] for r in p["results"]}
    _fixkeys = {((d or "")[:10], _club_key(o)) for d,o,h,cp in p["fixtures"]}
    ongoing_m, _extra_up = [], []
    for _m in MATCHES:
        if p["slug"] not in (_m.get("players") or "").split(";"):
            continue
        k = _ko(_m)
        if not k:
            continue
        _date = (_m.get("kickoff") or "")[:10]
        _myclub = club_at(p["slug"], _date) or p["club"]
        if _club_key(_m.get("away")) == _club_key(_myclub):
            _opp, _ha = _m.get("home",""), "A"
        else:
            _opp, _ha = _m.get("away",""), "H"
        _cp = _m.get("competition","")
        if k > _now:
            if (_date, _club_key(_opp)) not in _fixkeys:
                _extra_up.append((_date, _opp, _ha, _cp))
        elif (_now - k).total_seconds() < 3.5 * 3600 and _date not in _resdays:
            ongoing_m.append((_m, _opp, _ha, _cp))

    upcoming = list(p["fixtures"]) + _extra_up
    upcoming.sort(key=lambda x: x[0])

    ongr = ""
    for _m, _opp, _ha, _cp in ongoing_m:
        mid = match_id(_m)
        ongr += (f'<a class="mxrow lnk ongoing" href="../match/{mid}.html">'
                 f'<div class="mxtop"><span class="livedot">\u25cf LIVE</span>'
                 f'<span class="mxcomp">{esc(_cp)}</span></div>'
                 f'<div class="mxmain">{club_badge(_opp)}<span class="mxopp">{esc(_opp)}</span>'
                 f'<span class="mxr"><span class="mxmin">tap for live</span></span></div></a>')

    plive = "null"
    if ongoing_m:
        _lm = ongoing_m[0][0]
        _fid = fotmob_id(_lm)
        if _fid:
            plive = json.dumps({"fmid": _fid, "ko": _lm.get("kickoff", ""), "name": p["n"]})

    def _fxrow(d,o,h,cp):
        at = club_at(p["slug"], d) or p["club"]
        mid = match_page_for(p, d, at) or match_page_for(p, d, o)
        tag, href = ("a", f' href="../match/{mid}.html"') if mid else ("div", "")
        where = f'<span class="atclub">{esc(at)}</span>' if at else ""
        return (f'<{tag} class="fxrow when{" lnk" if mid else ""}"{href} data-when="{esc(d)}" data-opp="{esc(o)}" data-ha="{h}">'
                f'<div class="fxwhen">{esc(day_label(d))}</div>'
                f'<div class="fxc">{esc(cp)}{where}</div></{tag}>')
    fxr = "".join(_fxrow(d,o,h,cp) for d,o,h,cp in upcoming)

    mxr, cur_block = "", None
    for row in reversed(p["results"]):          # newest first
        d,o,sc,cp,mins,g,a = row[:7]
        rt = row[7] if len(row) > 7 else ""
        at = club_at(p["slug"], d) or p["club"]
        if at != cur_block:
            mxr += f'<div class="mxclub">{club_badge(at)}<b>{esc(at)}</b></div>'
            cur_block = at
        ev = "".join('<span class="evi g" title="Goal">⚽</span>' for _ in range(g or 0)) + \
             "".join('<span class="evi a" title="Assist">A</span>' for _ in range(a or 0))
        res = result_class(sc)
        mid = match_page_for(p, d, at) or match_page_for(p, d, o)
        tag, href = ("a", f' href="../match/{mid}.html"') if mid else ("div", "")
        mxr += (f'<{tag} class="mxrow{" lnk" if mid else ""}"{href}>'
                f'<div class="mxtop"><span>{esc(day_label(d))}</span><span class="mxcomp">{esc(cp)}</span></div>'
                f'<div class="mxmain">{club_badge(o)}<span class="mxopp">{esc(o)}</span>'
                f'<span class="wdl {res}">{ {"w":"W","d":"D","l":"L"}.get(res,"·") }</span><span class="mxsc">{esc(sc)}</span>'
                f'<span class="mxr">{ev}<span class="mxmin">{mins}\'</span>{rating_pill(rt)}</span></div></{tag}>')

    # ---------------- STATS tab ----------------
    comp_rows, comp_agg = "", {}
    season_start = f'{SEASON[:4]}-07-01'
    for row in p["results"]:
        d,o,sc,cp,mins,g,a = row[:7]
        rt = row[7] if len(row) > 7 else ""
        if (d or "") < season_start: continue
        agg = comp_agg.setdefault(cp or "—", [0,0,0,0,[]])
        agg[0]+=1; agg[1]+=g or 0; agg[2]+=a or 0; agg[3]+=int(mins or 0)
        if rt:
            try: agg[4].append(float(rt))
            except ValueError: pass
    for cp, (ap_,g_,a_,m_,rts) in sorted(comp_agg.items(), key=lambda kv:-kv[1][0]):
        avg = f"{sum(rts)/len(rts):.2f}" if rts else "—"
        comp_rows += (f'<div class="strow"><div class="stc">{esc(cp)}</div><div>{ap_}</div>'
                      f'<div>{g_}</div><div>{a_}</div><div>{m_}</div><div>{rating_pill(avg if rts else "")}</div></div>')
    stats_pane = f'''{season_card}
      {f'<div class="pcard"><div class="pct">By competition · {esc(SEASON)}</div><div class="sttbl"><div class="strow sthead"><div class="stc">Competition</div><div>Apps</div><div>G</div><div>A</div><div>Mins</div><div>Rating</div></div>{comp_rows}</div></div>' if comp_rows else ''}
      {f'<div class="pcard"><div class="pct">Discipline</div><div class="sumgrid three"><div><b><span class="card yel">{stat(p,"s_yellow",s["yellow"])}</span></b><span>Yellow</span></div><div><b><span class="card red">{stat(p,"s_red",s["red"])}</span></b><span>Red</span></div><div><b>{stat(p,"s_starts",s["starts"])}</b><span>Starts</span></div></div></div>' if has_data(p) else ''}
      {f'<div class="pcard"><div class="pct">Career</div><div class="sumgrid three"><div><b>{stat(p,"c_apps",c["ap"])}</b><span>Apps</span></div><div><b>{stat(p,"c_goals",c["g"])}</b><span>Goals</span></div><div><b>{stat(p,"c_assists",c["a"])}</b><span>Assists</span></div></div></div>' if has_data(p) else ''}'''

    # ---------------- CAREER tab ----------------
    seas_agg = {}
    for row in p["results"]:
        d,o,sc,cp,mins,g,a = row[:7]
        rt = row[7] if len(row) > 7 else ""
        sn = _season_of(d)
        if not sn: continue
        club = club_at(p["slug"], d) or p["club"]
        agg = seas_agg.setdefault((sn, club), [0,0,0,[]])
        agg[0]+=1; agg[1]+=g or 0; agg[2]+=a or 0
        if rt:
            try: agg[3].append(float(rt))
            except ValueError: pass
    seas_rows = ""
    for (sn, club), (ap_,g_,a_,rts) in sorted(seas_agg.items(), key=lambda kv: kv[0][0], reverse=True):
        avg = f"{sum(rts)/len(rts):.1f}" if rts else ""
        seas_rows += (f'<div class="crow">{club_badge(club)}<div class="crn"><b>{esc(club)}</b><span>{esc(sn)}</span></div>'
                      f'<div class="crs"><span>{ap_}</span><span>{g_}</span><span>{a_}</span>{rating_pill(avg)}</div></div>')
    seasons_view = (f'<div class="pcard"><div class="pct">Senior career <span class="crk">apps · goals · assists · rating</span></div>{seas_rows}</div>'
                    if seas_rows else '<div class="pcard nodata">Season-by-season data builds up from here — we hold match records from 2025/26 on.</div>')

    moves = sorted((tr for tr in p["transfers"] if (tr.get("date") or "").strip()),
                   key=lambda tr: tr["date"])
    spells, today = [], _dt.date.today().isoformat()
    for i, tr in enumerate(moves):
        club_ = (tr.get("to_club") or "").strip()
        if not club_ or club_.lower() in ("without club", "retired", "career break"): continue
        start = tr["date"][:10]
        end = moves[i+1]["date"][:10] if i+1 < len(moves) else ""
        kind = (tr.get("kind") or "").strip()
        note = " (on loan)" if kind == "loan" else (" (back from loan)" if kind == "loan end" else "")
        ap_ = g_ = 0; seen = False
        for row in p["results"]:
            d = row[0][:10]
            if start <= d and (not end or d < end):
                seen = True; ap_ += 1; g_ += row[5] or 0
        def _my(dstr):
            try: return _dt.datetime.strptime(dstr, "%Y-%m-%d").strftime("%b %Y")
            except ValueError: return dstr[:7]
        span = f'{_my(start)} – {_my(end) if end else "now"}'
        spells.append(f'<div class="crow">{club_badge(club_)}<div class="crn"><b>{esc(club_)}{note}</b><span>{span}</span></div>'
                      f'<div class="crs two"><span>{ap_ if seen else "—"}</span><span>{g_ if seen else "—"}</span></div></div>')
    club_view = (f'<div class="pcard"><div class="pct">Senior career <span class="crk">apps · goals (from 2025/26 on)</span></div>{"".join(reversed(spells))}</div>'
                 if spells else "")

    nat_rows = ""
    if p["intl_senior"]:
        i = p["intl_senior"]
        nat_rows += (f'<div class="crow"><span class="natflag"></span><div class="crn"><b>Ireland</b>'
                     f'<span>debut {esc(str(i["debut"] or ""))}</span></div>'
                     f'<div class="crs two"><span>{i["caps"]}</span><span>{i["goals"]}</span></div></div>')
    for lvl, caps, goals, since in p["intl_youth"]:
        nat_rows += (f'<div class="crow"><span class="natflag"></span><div class="crn"><b>Ireland {esc(lvl)}</b>'
                     f'<span>from {esc(str(since or ""))}</span></div>'
                     f'<div class="crs two"><span>{caps if caps is not None else "—"}</span><span>{goals if goals is not None else "—"}</span></div></div>')
    nat_card = f'<div class="pcard"><div class="pct">National team <span class="crk">caps · goals</span></div>{nat_rows}</div>' if nat_rows else ""

    # ---------------- assemble ----------------
    body = f'''
    <a class="crumb" data-back href="../players.html">← Back</a>
    <div class="pdhead">
      <div class="pdid">
        {avatar(p, "../")}
        <div>
        <div class="pdname">{esc(p["n"])}</div>
        <div class="pdmeta"><a class="clubchip" href="{clink(p["club"],"../")}">{club_badge(p["club"])}{esc(p["club"])}</a>
          {f'<span class="loanfrom">on loan from {esc(p["parent_club"])}</span>' if p.get("loan") and p.get("parent_club") and p["parent_club"] != p["club"] else ""}</div>
        {f'<div class="pdfull">{esc(p["tm"]["full_name"])}</div>' if t.get("full_name") and t["full_name"] != p["n"] else ""}
        {f'<div class="pcredit">Photo: {esc(p["photo_credit"])}</div>' if p.get("photo_credit") else ""}
        </div>
      </div>
      <div class="pdactions">
        <button class="starbtn" data-fav="{p["slug"]}" aria-pressed="false">★ <span>Follow</span></button>
        <div class="pdbadge">{badge}</div>
      </div>
    </div>

    <div class="ptabs" role="tablist">
      <button class="ptab on" data-pane="profile">Profile</button>
      <button class="ptab" data-pane="matches">Matches</button>
      <button class="ptab" data-pane="pstats">Stats</button>
      <button class="ptab" data-pane="career">Career</button>
    </div>

    <div class="ppane" id="pane-profile">
      {bio_card}
      {season_card}
      {position_card}
      {contract_card}
      {intl}
      <div class="sec"><h2>Eligibility</h2></div>
      <div class="eliglist">{elig}</div>
      <p class="eligNote">{tie_note}</p>
    </div>

    <div class="ppane" id="pane-matches" hidden>
      <div class="sec"><h2>Upcoming</h2>{f'<span class="more" style="border:0">{len(upcoming)} listed</span>' if upcoming else ''}</div>
      <div class="fxlist">{fxr or '<div class="emptystate" style="display:block">No fixtures listed.</div>'}</div>
      {f'<div class="sec"><h2>On now</h2></div><div class="mxlist">{ongr}</div>' if ongr else ''}
      <div class="sec"><h2>Recent matches</h2><span class="more" style="border:0">{len(p["results"])} on record</span></div>
      <div class="mxlist">{mxr or '<div class="emptystate" style="display:block">No appearances yet.</div>'}</div>
      <div class="rmnote">Only games they were on the pitch for. Unused subs and squad omissions aren\'t listed.
        Something wrong? <a href="#" onclick="window.FB_REPORT&&FB_REPORT();return false">Report it</a>.</div>
    </div>

    <div class="ppane" id="pane-pstats" hidden>
      {stats_pane}
    </div>

    <div class="ppane" id="pane-career" hidden>
      <div class="segtoggle"><button class="seg on" data-view="seasons">Seasons</button><button class="seg" data-view="club">Club</button></div>
      <div id="cv-seasons">{seasons_view}</div>
      <div id="cv-club" hidden>{club_view or '<div class="pcard nodata">No transfer history on record.</div>'}</div>
      {nat_card}
    </div>

    <script>
    (function(){{
      var tabs=document.querySelectorAll('.ptab'),panes=document.querySelectorAll('.ppane');
      function show(k){{tabs.forEach(function(b){{b.classList.toggle('on',b.dataset.pane===k)}});
        panes.forEach(function(pn){{pn.hidden=(pn.id!=='pane-'+k)}});
        if(history.replaceState)history.replaceState(null,'','#'+k);}}
      tabs.forEach(function(b){{b.addEventListener('click',function(){{show(b.dataset.pane)}})}});
      var h=(location.hash||'').slice(1);
      if(['matches','pstats','career'].indexOf(h)>=0)show(h);
      var segs=document.querySelectorAll('.seg');
      segs.forEach(function(b){{b.addEventListener('click',function(){{
        segs.forEach(function(x){{x.classList.toggle('on',x===b)}});
        document.getElementById('cv-seasons').hidden=b.dataset.view!=='seasons';
        document.getElementById('cv-club').hidden=b.dataset.view!=='club';}})}});
    }})();
    </script>
    <script>window.FB_PLIVE={plive};</script>
    {PLIVE_JS}
    '''
    return shell(f"{p['n']} — footballers.ie",
                 f"{p['n']} ({p['club']}, {p['league']}) — season stats, fixtures, results and international record.",
                 "../", "", body, canonical=f"player/{p['slug']}.html",
                 body_attr=f' data-player="{p["slug"]}"',
                 og=og_card(t="player", n=p["n"], c=(p["club"] if p["club"] != "Unattached" else ""),
                            l=(p["league"] if p["league"] != "—" else ""),
                            x=" · ".join(x for x in (p["pos"] if p["pos"] != "—" else "",
                                                     str(p["age"]) if p["age"] else "") if x),
                            p=(p["slug"] if p["slug"] in HAVE_IMG else ""),
                            cb=club_id(p["club"]),
                            ap=p["season"]["ap"] or "", gl=p["season"]["g"] or "",
                            **{"as": p["season"]["a"] or ""},
                            rt=(p.get("rating") or ""), f=f"Season {SEASON}"))


# ================= TRANSFERS =================
# data/api/transfers.csv is every club move Transfermarkt has for a tracked
# player — 4,000-odd rows going back to the eighties. The page is one feed,
# newest first, cut by window / kind / search. Rendering all of it as HTML
# would be ~8,000 badge images, so the feed ships as a compact payload and
# the browser draws sixty at a time; the newest few are baked into the HTML
# so the page says something before any script runs.

def transfer_window(date):
    """('2026S', 'Summer 2026') — which window a move belongs to. Two a year:
       the summer rebuild (May onward, where loans expire and squads turn
       over) and the winter one. Rough at the edges, but it is how people
       actually talk about transfers."""
    d = (date or "").strip()[:10]
    if len(d) != 10 or not d[:4].isdigit():
        # a reported move with no date on it belongs to the window we are in
        y = SEASON[:4]
        return (f"{y}S", f"Summer {y}")
    y = d[:4]
    try: m = int(d[5:7])
    except ValueError: return ("", "Undated")
    return (f"{y}S", f"Summer {y}") if m >= 5 else (f"{y}W", f"Winter {y}")

_FEE_RE = re.compile(r"€\s*([\d.,]+)\s*([mk])?", re.I)

def fee_value(fee):
    """The euro figure in a fee cell as a number. Free, loan and unknown all
       come back 0 — the chip says which of those it was."""
    m = _FEE_RE.search(str(fee or ""))
    if not m: return 0.0
    try: n = float(m.group(1).replace(",", ""))
    except ValueError: return 0.0
    return n * {"m": 1_000_000, "k": 1_000}.get((m.group(2) or "").lower(), 1)

def money(v):
    if v >= 1_000_000: return "€" + f"{v/1_000_000:.2f}".rstrip("0").rstrip(".") + "m"
    if v >= 1_000:     return f"€{round(v/1_000):g}k"
    return f"€{round(v):g}" if v else ""

def transfer_label(t):
    """(chip text, css class). Money when there was any, the shape of the
       deal when there wasn't. A hand-entered move never claims a fee was
       undisclosed - we simply weren't told one."""
    k, fee = t["kind"], t["fee"]
    if k == "fee":      return (money(fee_value(fee)) or "Fee", "fee")
    if k == "free":     return ("Free", "free")
    if k == "loan":     return ("Loan", "loan")
    if k == "loan end": return ("Loan ended", "end")
    if t.get("manual"): return ("Signed", "move")
    return (("Undisclosed" if fee in ("?", "") else "Signed"), "move")

def transfer_feed():
    """Every move we can attach to a tracked player, newest first."""
    pmap = {p["slug"]: p for p in PLAYERS}
    out = []
    for slug, rows in TRANSFERS.items():
        p = pmap.get(slug)
        if not p: continue
        for r in rows:
            d = (r.get("date") or "").strip()[:10]
            to = (r.get("to_club") or "").strip()
            manual = bool(r.get("manual"))
            # a move with no confirmed date is hidden from the feed - undated
            # rows used to pile at the very top and bury everything dated.
            if not to or len(d) != 10: continue
            out.append(dict(p=p, date=d, frm=(r.get("from_club") or "").strip(),
                            to=to, fee=(r.get("fee") or "").strip(),
                            kind=(r.get("kind") or "").strip(), manual=manual))
    out.sort(key=lambda t: (t["date"], t["p"]["n"]), reverse=True)
    return out

_TMONTHS = ("January", "February", "March", "April", "May", "June", "July",
            "August", "September", "October", "November", "December")

def transfer_day(d):
    """Heading for a day of moves. Recent days get named, older ones get the
       date — 'Wednesday the 3rd' means nothing about a signing in 2019."""
    import datetime as _dt
    if not (d or "").strip():
        return "Reported · date not confirmed"
    try: day = _dt.date.fromisoformat(d)
    except ValueError: return d
    today = _dt.date.today()
    gap = (today - day).days
    if gap == 0: return "Today"
    if gap == 1: return "Yesterday"
    if 0 < gap < 7: return day.strftime("%A")
    base = f"{day.day} {_TMONTHS[day.month - 1]}"
    return base if day.year == today.year else f"{base} {day.year}"

def club_label(n):
    """What to print for a club cell. Transfermarkt uses 'Without Club' for a
       free agent and 'Retired'/'Career break' for the end of one."""
    s = (n or "").strip()
    return "No club" if s.lower() in ("without club", "unknown", "") else s

def transfer_row(t, root=""):
    p = t["p"]
    lab, cls = transfer_label(t)
    frm = club_label(t["frm"])
    pos = p["pos"] if p["pos"] not in ("", "—") else ""
    posbit = f'<span class="trp">{esc(pos)}</span>' if pos else ""
    frmbadge = club_badge(t["frm"], "xs") if club_id(t["frm"]) else ""
    return (f'<a class="trrow" href="{plink(p, root)}">'
            f'{avatar(p, root, "sm")}'
            f'<div class="trm">'
            f'<div class="trn">{esc(p["n"])}{posbit}</div>'
            f'<div class="trmv">{frmbadge}<span>{esc(frm)}</span><i class="tra">→</i>'
            f'{club_badge(t["to"], "xs") if club_id(t["to"]) else ""}<b>{esc(club_label(t["to"]))}</b></div></div>'
            f'<div class="trr"><span class="tchip {cls}">{esc(lab)}</span></div></a>')

def build_transfers():
    feed = transfer_feed()
    live = [t for t in feed if t["kind"] != "loan end"]  # loan returns off by default

    # the headline numbers come from the newest window, which is the one
    # people mean when they say "this window"
    cur_key, cur_label = (transfer_window(feed[0]["date"]) if feed else ("", "—"))
    cur = [t for t in live if transfer_window(t["date"])[0] == cur_key]
    fees = sorted(cur, key=lambda t: fee_value(t["fee"]), reverse=True)
    top = fees[0] if fees and fee_value(fees[0]["fee"]) else None
    tiles = [(str(len(cur)), f"moves · {cur_label}"),
             (str(sum(1 for t in cur if t["kind"] in ("fee", "free", ""))), "permanent"),
             (str(sum(1 for t in cur if t["kind"] == "loan")), "loans"),
             (money(fee_value(top["fee"])) if top else "—",
              f'biggest fee · {top["p"]["n"]}' if top else "no fee disclosed")]
    tilehtml = "".join(f'<div class="ttile"><div class="n">{esc(a)}</div>'
                       f'<div class="l">{esc(b)}</div></div>' for a, b in tiles)

    wins, seen = [], set()
    for t in feed:
        k, l = transfer_window(t["date"])
        if k and k not in seen:
            seen.add(k); wins.append((k, l))
    wopts = "".join(f'<option value="{esc(k)}">{esc(l)}</option>' for k, l in wins)

    # ---- the payload the browser draws from ------------------------------
    clubs, cidx = [], {}
    for t in feed:
        for c in (t["frm"], t["to"]):
            if c and c not in cidx:
                cidx[c] = len(clubs); clubs.append(c)
    plist, pidx = [], {}
    for t in feed:
        p = t["p"]
        if p["slug"] not in pidx:
            pidx[p["slug"]] = len(plist)
            plist.append([p["slug"], p["n"],
                          p["pos"] if p["pos"] not in ("", "—") else "",
                          initials(p["n"]),
                          (p.get("photo") or "") if p.get("photo")
                          else ("*" if p["slug"] in HAVE_IMG else "")])
    trows = [[pidx[t["p"]["slug"]], t["date"], cidx.get(t["frm"], -1),
              cidx[t["to"]], transfer_label(t)[1], transfer_label(t)[0]]
             for t in feed]
    payload = {"w": transfer_window("")[0],
               "c": [club_label(c) for c in clubs],
               "b": [club_id(c) for c in clubs],
               "p": plist, "t": trows}

    seed, day = "", None
    for t in live[:40]:
        if t["date"] != day:
            day = t["date"]
            seed += (f'<h4 class="tday{"" if day else " soft"}">'
                     f'{esc(transfer_day(day))}</h4>')
        seed += transfer_row(t)

    sub = ("Every move by a tracked Irish player — window by window, "
           "back through the archive.")
    body = ('<div class="pagehead"><h1>Transfers</h1><p>' + esc(sub) + '</p></div>'
            '<div class="tsum">' + tilehtml + '</div>'
            '<div class="filterbar tfbar">'
            '<input type="search" id="tq" placeholder="Search player or club">'
            '<select id="twin"><option value="">All windows</option>' + wopts + '</select>'
            '<select id="tkind"><option value="">Every kind</option>'
            '<option value="fee">Fees</option><option value="free">Free transfers</option>'
            '<option value="loan">Loans</option><option value="move">Undisclosed</option>'
            '</select>'
            '<label class="tends"><input type="checkbox" id="tendsx"> Loan returns</label>'
            '</div>'
            '<div class="tcount" id="tcount">' + f"{len(live)} moves" + '</div>'
            '<div id="tfeed">' + seed + '</div>'
            '<button class="tmore" id="tmore" style="display:none">Load more</button>'
            '<div class="emptystate" id="tempty">No moves match that. Clear the search '
            'or pick another window.</div>'
            '<p class="tnote">Fees and dates from Transfermarkt. A move appears here once '
            'it is done — nothing on this page is a rumour.</p>'
            '<script>window.FB_TF=' + json.dumps(payload, separators=(",", ":")) + ';</script>'
            '<script>' + TRANSFERS_JS + '</script>')
    return shell("Transfers — footballers.ie", sub, "", "transfers.html", body,
                 canonical="transfers.html",
                 og=og_card(n="Transfers", big="1",
                            l=f"{len(cur)} Irish moves · {cur_label}",
                            f="footballers.ie", fr="TRANSFERS"))


TRANSFERS_JS = '\n(function(){\n  var D=window.FB_TF; if(!D||!D.t) return;\n  var feed=document.getElementById(\'tfeed\'), more=document.getElementById(\'tmore\'),\n      cnt=document.getElementById(\'tcount\'), empty=document.getElementById(\'tempty\'),\n      q=document.getElementById(\'tq\'), wf=document.getElementById(\'twin\'),\n      kf=document.getElementById(\'tkind\'), ends=document.getElementById(\'tendsx\');\n  if(!feed||!more) return;\n  // null, not \'\': an undated row\'s date IS the empty string, and\n  // starting lastDay at \'\' would swallow its heading\n  var PAGE=60, view=[], shown=0, lastDay=null;\n  var M=[\'January\',\'February\',\'March\',\'April\',\'May\',\'June\',\'July\',\'August\',\n         \'September\',\'October\',\'November\',\'December\'];\n  var DAYS=[\'Sunday\',\'Monday\',\'Tuesday\',\'Wednesday\',\'Thursday\',\'Friday\',\'Saturday\'];\n  var today=new Date(); today.setHours(0,0,0,0);\n\n  function esc(t){return String(t==null?\'\':t).replace(/[&<>"]/g,function(c){\n    return {\'&\':\'&amp;\',\'<\':\'&lt;\',\'>\':\'&gt;\',\'"\':\'&quot;\'}[c];});}\n  // an undated row is a reported move in the window we are in\n  function win(d){return d?d.slice(0,4)+(+d.slice(5,7)>=5?\'S\':\'W\'):(D.w||\'\');}\n  function heading(d){\n    if(!d) return \'Reported · date not confirmed\';\n    var p=d.split(\'-\'), dt=new Date(+p[0],+p[1]-1,+p[2]);\n    var gap=Math.round((today-dt)/86400000);\n    if(gap===0) return \'Today\';\n    if(gap===1) return \'Yesterday\';\n    if(gap>0&&gap<7) return DAYS[dt.getDay()];\n    var base=(+p[2])+\' \'+M[+p[1]-1];\n    return dt.getFullYear()===today.getFullYear()?base:base+\' \'+p[0];\n  }\n  function badge(i){\n    if(i<0) return \'\';\n    var id=D.b[i], nm=D.c[i];\n    // a state, not a club - nothing to put a crest on\n    if(nm===\'No club\'||nm===\'Retired\'||nm===\'Career break\') return \'\';\n    if(!id) return \'<span class="badge xs generic"></span>\';\n    return \'<img class="badge xs" loading="lazy" alt="" src="https://images.fotmob.com/\'+\n      \'image_resources/logo/teamlogo/\'+id+\'.png" onerror="this.outerHTML=&#39;<span \'+\n      \'class=&quot;badge xs generic&quot;></span>&#39;">\';\n  }\n  function face(p){\n    var src = p[4]===\'*\' ? \'img/players/\'+p[0]+\'.png\' : p[4];\n    if(!src) return \'<div class="pavatar sm"><span>\'+esc(p[3])+\'</span></div>\';\n    return \'<div class="pavatar sm"><img loading="lazy" alt="\'+esc(p[1])+\'" src="\'+\n      esc(src)+\'" onerror="this.parentNode.innerHTML=&#39;<span>\'+esc(p[3])+\n      \'</span>&#39;"></div>\';\n  }\n  function row(r){\n    var p=D.p[r[0]], from=r[2]<0?\'No club\':D.c[r[2]], to=D.c[r[3]];\n    return \'<a class="trrow" href="player/\'+esc(p[0])+\'.html">\'+face(p)+\n      \'<div class="trm"><div class="trn">\'+esc(p[1])+\n      (p[2]?\'<span class="trp">\'+esc(p[2])+\'</span>\':\'\')+\'</div>\'+\n      \'<div class="trmv">\'+badge(r[2])+\'<span>\'+esc(from)+\'</span>\'+\n      \'<i class="tra">→</i>\'+badge(r[3])+\'<b>\'+esc(to)+\'</b></div></div>\'+\n      \'<div class="trr"><span class="tchip \'+r[4]+\'">\'+esc(r[5])+\'</span></div></a>\';\n  }\n\n  function build(){\n    var t=q.value.trim().toLowerCase(), w=wf.value, k=kf.value, le=ends.checked;\n    view=D.t.filter(function(r){\n      if(!le && r[4]===\'end\') return false;\n      if(k && r[4]!==k) return false;\n      if(w && win(r[1])!==w) return false;\n      if(t){\n        var p=D.p[r[0]];\n        var hay=(p[1]+\' \'+(r[2]<0?\'\':D.c[r[2]])+\' \'+D.c[r[3]]).toLowerCase();\n        if(hay.indexOf(t)<0) return false;\n      }\n      return true;\n    });\n    feed.innerHTML=\'\'; shown=0; lastDay=null;\n    cnt.textContent=view.length+\' move\'+(view.length===1?\'\':\'s\');\n    empty.style.display=view.length?\'none\':\'block\';\n    draw();\n  }\n  function draw(){\n    var end=Math.min(shown+PAGE, view.length), html=\'\';\n    for(var i=shown;i<end;i++){\n      var r=view[i];\n      if(r[1]!==lastDay){ lastDay=r[1];\n        html+=\'<h4 class="tday\'+(r[1]?\'\':\' soft\')+\'">\'+esc(heading(r[1]))+\'</h4>\'; }\n      html+=row(r);\n    }\n    feed.insertAdjacentHTML(\'beforeend\', html);\n    shown=end;\n    more.style.display = shown<view.length ? \'\' : \'none\';\n    more.textContent=\'Load more · \'+(view.length-shown)+\' left\';\n  }\n  more.onclick=draw;\n  q.oninput=build; wf.onchange=build; kf.onchange=build; ends.onchange=build;\n  // the tail of the list loads itself once the reader gets near it\n  window.addEventListener(\'scroll\', function(){\n    if(more.style.display===\'none\') return;\n    if(more.getBoundingClientRect().top < window.innerHeight + 400) draw();\n  }, {passive:true});\n  build();\n})();\n'


def build_newsletter():
    body = f'''
    <div class="pagehead"><h1>The <i>newsletter</i></h1>
      <p>Two emails a week on every Irish professional — written by hand, every week.</p></div>
    {signup()}
    <div class="sec"><h2>What you get</h2></div>
    <div class="nlgrid">
      <div class="nlcard"><div class="nlday">Monday morning</div>
        <ul><li>How every Irish player got on at the weekend</li>
            <li>Goals, assists, minutes, debuts</li>
            <li>What's coming up this week — with a specific focus on the weekday games</li></ul></div>
      <div class="nlcard"><div class="nlday">Friday morning</div>
        <ul><li>Every Irish player in action this weekend</li>
            <li>A recap of the midweek matches</li>
            <li>Injury news and who's back in contention</li></ul></div>
    </div>'''
    return shell("Newsletter — footballers.ie",
                 "Two emails a week on every Irish professional footballer — Monday round-up and Friday preview.",
                 "", "newsletter.html", body, canonical="newsletter.html",
                 og=og_card(n="The newsletter", gw="gold",
                            l="Two emails a week: Monday round-up, Friday preview.",
                            f="Newsletter"))


def build_alerts():
    opts = lambda checked: "".join(
        f'<label class="alertopt"><input type="checkbox" name="alert_{k}"{" checked" if k in checked else ""}> '
        f'<span><b>{t}</b>{d}</span></label>'
        for k, t, d in (
            ("lineup", "About to play", "An hour before kick-off when one of your players is named in the squad."),
            ("goal", "Goal or assist", "The moment they\'re involved."),
            ("rating", "Full-time rating", "Their match rating and minutes once the game\'s done."),
            ("news", "Transfers &amp; injuries", "Moves, call-ups and fitness news.")))
    form_open = f'<form class="alertform" action="{NEWSLETTER_ACTION}" method="post" target="_blank">' if NEWSLETTER_ACTION \
                else '<form class="alertform" onsubmit="event.preventDefault();">'
    body = f'''
    <div class="pagehead"><h1>Alerts &amp; <i>account</i></h1>
      <p>Follow players with the ★ anywhere on the site, then get an email when they\'re involved.</p></div>

    <div class="nlbox" id="acct" style="display:none">
      <div class="nltag">Your account</div>
      <h3 class="nlh" id="acctmail"></h3>
      <div class="alertgrid" id="acctprefs">{opts(())}</div>
      <div class="alertwho" id="acctfollow"></div>
      <div class="acctbtns">
        <button class="acctbtn save" id="acctsave">Save changes</button>
        <button class="acctbtn" id="acctout">Sign out on this device</button>
        <button class="acctbtn danger" id="acctdel">Delete my account</button>
      </div>
      <div class="nlnote" id="acctnote" style="display:none"></div>
      <div class="nlfine">Deleting removes your email and everything tied to it from our records.
        You can also email datadeletion@matchweek.ie.</div>
    </div>

    <div class="nlbox" id="signup">
      <div class="nltag">Alerts</div>
      <h3 class="nlh">Your players, straight to your inbox</h3>
      <div class="alertgrid">{opts(("lineup", "goal"))}</div>
      <div class="alertwho">Following <b data-fav-count>0</b> player<span data-fav-plural></span>.
        <span id="alertnames"></span></div>
      {form_open}
        <input type="email" name="{NEWSLETTER_FIELD}" placeholder="your@email.ie" required aria-label="Email address">
        <input type="hidden" name="players" id="alertplayers">
        <button type="submit">Turn on alerts</button>
      </form>
      <div class="nlnote" id="alertnote" style="display:none"></div>
      <div class="nlfine">Coming to the app as push notifications.</div>
    </div>

    <div class="sec"><h2>How it works</h2></div>
    <div class="nlgrid">
      <div class="nlcard"><div class="nlday">1 · Follow</div>
        <ul><li>Tap the ★ beside any player</li><li>Saved in your browser — no account needed</li>
            <li>They appear at the top of the homepage</li></ul></div>
      <div class="nlcard"><div class="nlday">2 · Get alerted</div>
        <ul><li>Email before they play</li><li>Email when they score or assist</li>
            <li>Full-time rating if you want it</li></ul></div>
    </div>
    <script>
    (function(){{
      function upd(){{
        var f = (window.FB ? FB.read() : []);
        var el = document.getElementById('alertplayers');
        if (el) el.value = f.join(',');
        var names = document.getElementById('alertnames');
        var pl = document.querySelectorAll('[data-fav-plural]');
        for (var i=0;i<pl.length;i++) pl[i].textContent = f.length===1 ? '' : 's';
        if (names) names.textContent = f.length ? '' : 'Tap the ★ on any player first.';
      }}
      document.addEventListener('favschange', upd);
      if (document.readyState==='loading') document.addEventListener('DOMContentLoaded', upd); else upd();

      /* ---- the account view: shown once this browser has an email saved ---- */
      function email(){{ try {{ return localStorage.getItem('fb_email_v1') || ''; }} catch(e) {{ return ''; }} }}
      function favs(){{ try {{ return JSON.parse(localStorage.getItem('fb_favs_v1')) || []; }} catch(e) {{ return []; }} }}
      function mfavs(){{ try {{ return JSON.parse(localStorage.getItem('fb_favm_v1')) || []; }} catch(e) {{ return []; }} }}
      function pretty(slug){{
        return slug.split('-').map(function(w){{ return w.charAt(0).toUpperCase() + w.slice(1); }}).join(' ');
      }}
      function note(t, ok){{
        var n = document.getElementById('acctnote');
        n.style.display = 'block'; n.textContent = t;
        n.style.color = ok ? '' : 'var(--red)';
      }}
      function prefStr(){{
        return [].map.call(document.querySelectorAll('#acctprefs input:checked'),
          function(c){{ return c.name.replace('alert_',''); }}).join(';');
      }}
      async function api(payload){{
        var r = await fetch('/api/subscribe', {{ method:'POST',
          headers: {{'Content-Type':'application/json'}}, body: JSON.stringify(payload) }});
        var out = await r.json().catch(function(){{ return {{}}; }});
        if (!r.ok) throw new Error(out.error || 'That didn\\'t work. Try again shortly.');
        return out;
      }}
      async function boot(){{
        var em = email();
        if (!em) return;
        document.getElementById('acct').style.display = '';
        document.getElementById('signup').style.display = 'none';
        document.getElementById('acctmail').textContent = em;
        var flw = document.getElementById('acctfollow');
        var f = favs();
        flw.innerHTML = 'Following <b>' + f.length + '</b> player' + (f.length===1?'':'s')
          + (f.length ? ': ' + f.slice(0,12).map(pretty).join(', ') + (f.length>12?'…':'') : '')
          + (mfavs().length ? ' · <b>' + mfavs().length + '</b> match' + (mfavs().length===1?'':'es') : '');
        try {{
          var acc = await api({{ action:'get', email: em }});
          var set = (acc.prefs || 'lineup;goal').split(';');
          [].forEach.call(document.querySelectorAll('#acctprefs input'), function(c){{
            c.checked = set.indexOf(c.name.replace('alert_','')) > -1;
          }});
        }} catch(e) {{
          ['lineup','goal'].forEach(function(k){{
            var c = document.querySelector('#acctprefs input[name=alert_'+k+']');
            if (c) c.checked = true;
          }});
        }}
      }}
      document.getElementById('acctsave').addEventListener('click', async function(){{
        var b = this; b.disabled = true; b.textContent = 'Saving…';
        try {{
          await api({{ action:'save', email: email(), source:'account',
                      prefs: prefStr(), players: favs().join(';'), matches: mfavs().join(';') }});
          note('Saved.', true);
          if (window.FB_TRACK) FB_TRACK('account_prefs_saved', {{}});
        }} catch(e) {{ note(e.message); }}
        b.disabled = false; b.textContent = 'Save changes';
      }});
      document.getElementById('acctout').addEventListener('click', function(){{
        try {{ localStorage.removeItem('fb_email_v1'); }} catch(e) {{}}
        location.reload();
      }});
      document.getElementById('acctdel').addEventListener('click', async function(){{
        var b = this;
        if (!b.dataset.armed) {{
          b.dataset.armed = '1'; b.textContent = 'Tap again to confirm — this is permanent';
          setTimeout(function(){{ delete b.dataset.armed; b.textContent = 'Delete my account'; }}, 6000);
          return;
        }}
        b.disabled = true; b.textContent = 'Deleting…';
        try {{
          await api({{ action:'delete', email: email() }});
          try {{ localStorage.removeItem('fb_email_v1'); }} catch(e) {{}}
          if (window.FB_TRACK) FB_TRACK('account_deleted', {{}});
          note('Your account is gone. Your ★ follows stay on this device unless you clear them.', true);
          setTimeout(function(){{ location.reload(); }}, 2500);
        }} catch(e) {{ note(e.message); b.disabled = false; b.textContent = 'Delete my account'; }}
      }});
      if (document.readyState==='loading') document.addEventListener('DOMContentLoaded', boot); else boot();
    }})();
    </script>'''
    return shell("Alerts & account — footballers.ie",
                 "Follow Irish players, choose your alerts, and manage or delete your account.",
                 "", "alerts.html", body, canonical="alerts.html",
                 og=og_card(n="Player alerts", l="Follow any Irish player and get an email when they play, score or assist.",
                            f="Alerts"))

# ================= 404 / SITEMAP / ROBOTS =================
def build_search_index():
    """Everything on the site, one row each, for the search page. Written as
       its own file so only the search page pays for it."""
    rows = []
    for p in PLAYERS:
        rows.append(dict(t="Player", n=p["n"], u=f"player/{p['slug']}.html",
                         x=" · ".join(filter(None, [p["pos"] if p["pos"] not in ("","—") else "",
                                                    p["club"] if p["club"] != "Unattached" else "Unattached"]))))
    clubs = {}
    for p in PLAYERS: clubs.setdefault(p["club"], []).append(p)
    for c, ps in clubs.items():
        if c == "Unattached": continue
        rows.append(dict(t="Club", n=c, u=f"club/{club_slug(c)}.html",
                         x=f"{len(ps)} Irish player{'s' if len(ps)!=1 else ''}"))
    for _n, _u, _x in (("Transfers", "transfers.html",
                        "Every move by a tracked Irish player"),):
        rows.append(dict(t="Page", n=_n, u=_u, x=_x))
    leagues, countries = {}, {}
    for p in PLAYERS:
        if p["league"] not in ("", "—"): leagues.setdefault(p["league"], 0)
        countries.setdefault(player_country(p), 0)
        leagues[p["league"]] = leagues.get(p["league"], 0) + 1
        countries[player_country(p)] += 1
    for l, n in leagues.items():
        rows.append(dict(t="League", n=l, u=f"league/{club_slug(l)}.html", x=f"{n} players"))
    for c, n in countries.items():
        if c in ("Other", NO_CLUB): continue
        rows.append(dict(t="Country", n=c, u=f"country/{country_slug(c)}.html", x=f"{n} players"))
    for a in ARTICLES:
        rows.append(dict(t="Article", n=a.get("headline",""), u=f"news/{art_slug(a)}.html",
                         x=" · ".join(filter(None, [a.get("author",""), pretty_date(a.get("date",""))]))))
    for w in authors():
        rows.append(dict(t="Writer", n=w["name"], u=f"author/{w['slug']}.html",
                         x=f"{len(w['arts'])} article{'s' if len(w['arts'])!=1 else ''}"))
    seen_m = set()
    for m in MATCHES:
        mid = match_id(m)
        if mid in seen_m or not m.get("kickoff"): continue
        seen_m.add(mid)
        rows.append(dict(t="Match", n=f"{m.get('home','')} v {m.get('away','')}",
                         u=f"match/{mid}.html",
                         x=" · ".join(filter(None, [pretty_date(m.get("kickoff","")[:10]),
                                                    m.get("competition","")]))))
    return rows

SEARCH_JS = r"""
(function(){
  var box=document.getElementById('sq'), out=document.getElementById('sres');
  var DATA=null, ORDER=['Player','Club','Article','Writer','League','Country','Match'];
  var HINT='Players, clubs, matches, articles, leagues, countries, writers — anything on the site.';
  function norm(t){
    return String(t||'').normalize('NFKD').replace(/[\u0300-\u036f]/g,'')
      .replace(/['\u2019]/g,'').toLowerCase();
  }
  function esc(t){ return String(t).replace(/&/g,'&amp;').replace(/</g,'&lt;'); }
  function score(hay, q, words){
    var h=norm(hay);
    if(h===q) return 100;
    if(h.indexOf(q)===0) return 80;
    var s=0;
    for(var i=0;i<words.length;i++){
      var w=words[i]; if(!w) continue;
      var at=h.indexOf(w);
      if(at<0) return -1;
      s += (at===0 || h.charAt(at-1)===' ') ? 30 : 12;
    }
    return s;
  }
  function run(){
    var q=norm(box.value.trim());
    if(q.length<2){ out.innerHTML='<div class="shint">'+HINT+'</div>'; return; }
    var words=q.split(/\s+/);
    var hits=[];
    for(var i=0;i<DATA.length;i++){
      var r=DATA[i];
      var sc=score(r.n,q,words);
      if(sc<0 && r.x){ sc=score(r.x,q,words); if(sc>0) sc-=10; }
      if(sc>=0) hits.push([sc,r]);
    }
    hits.sort(function(a,b){
      if(b[0]!==a[0]) return b[0]-a[0];
      return ORDER.indexOf(a[1].t)-ORDER.indexOf(b[1].t);
    });
    var groups={}, order=[];
    hits.slice(0,80).forEach(function(h){
      if(!groups[h[1].t]){ groups[h[1].t]=[]; order.push(h[1].t); }
      if(groups[h[1].t].length<8) groups[h[1].t].push(h[1]);
    });
    if(!order.length){ out.innerHTML='<div class="shint">Nothing matched “'+esc(box.value.trim())+'”. Player missing? Hit Report.</div>'; return; }
    out.innerHTML=order.map(function(t){
      var label={'Match':'Matches','Country':'Countries'}[t]||t+'s';
      return '<div class="sgroup"><h3>'+(groups[t].length>1?label:t)+'</h3>'+
        groups[t].map(function(r){
          return '<a class="srow" href="'+r.u+'"><b>'+esc(r.n)+'</b>'+(r.x?'<span>'+esc(r.x)+'</span>':'')+'</a>';
        }).join('')+'</div>';
    }).join('');
  }
  var t;
  box.addEventListener('input', function(){ clearTimeout(t); t=setTimeout(run,120); });
  box.addEventListener('keydown', function(e){
    if(e.key==='Enter'){ var f=out.querySelector('.srow'); if(f) location.href=f.getAttribute('href'); }
  });
  fetch('search.json').then(function(r){return r.json();}).then(function(d){
    DATA=d;
    var q=new URLSearchParams(location.search).get('q');
    if(q){ box.value=q; }
    run(); box.focus();
  });
})();
"""

def build_search():
    body = f'''
    <div class="pagehead"><h1>Search</h1></div>
    <input id="sq" class="sinput" type="search" placeholder="A player, a club, a match, anything\u2026" autocomplete="off" autofocus>
    <div id="sres"><div class="shint">Players, clubs, matches, articles, leagues, countries, writers \u2014 anything on the site.</div></div>
    <script>{SEARCH_JS}</script>
    '''
    return shell("Search — footballers.ie",
                 "Search every player, club, match and article on footballers.ie.",
                 "", "search.html", body, canonical="search.html",
                 og=og_card(n="Search", l="Every player, club, match and article on the site.",
                            f="Search"))

def build_404():
    body = """
    <div class="pagehead" style="padding-top:60px">
      <h1>That page doesn't exist</h1>
      <p>The link may be out of date, or the player may not be tracked here. Try a search instead.</p>
    </div>
    <div class="filterbar" style="max-width:520px">
      <a class="tab" href="/search.html">Search</a>
      <a class="tab" href="/players.html">All players</a>
      <a class="tab" href="/abroad.html">Abroad</a>
      <a class="tab" href="/league-of-ireland.html">League of Ireland</a>
      <a class="tab" href="/index.html">Home</a>
    </div>"""
    return shell("Page not found — footballers.ie", "That page doesn't exist on Footballers.", "/", "", body,
                 og=og_card(n="Page not found", l="That link is out of date — try a search instead.",
                            f="404"))

def build_sitemap():
    urls = ["", "news.html", "faq.html", "search.html", "where-are-the-irish.html", "players.html", "abroad.html", "league-of-ireland.html", "clubs.html", "transfers.html",
            "ireland.html", "fixtures.html", "milestones.html", "compare.html", "newsletter.html", "alerts.html"]
    urls += [f"club/{club_slug(c)}.html" for c in sorted(set(p["club"] for p in PLAYERS))]
    urls += [f"player/{p['slug']}.html" for p in PLAYERS]
    urls += [f"news/{art_slug(a)}.html" for a in ARTICLES]
    urls += [f"author/{w['slug']}.html" for w in authors()]
    items = "".join(f"  <url><loc>{SITE_URL}/{u}</loc></url>\n" for u in urls)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{items}</urlset>\n'

def build_robots():
    return f"User-agent: *\nAllow: /\n\nSitemap: {SITE_URL}/sitemap.xml\n"

FAVICON = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<rect width="64" height="64" rx="12" fill="#0C0F10"/>
<text x="32" y="45" font-family="ui-monospace,monospace" font-size="42" font-weight="700"
      fill="#EFF3F1" text-anchor="middle">F</text>
</svg>'''

# ================= BUILD =================
os.makedirs(f"{OUT}/player", exist_ok=True)
os.makedirs(f"{OUT}/club", exist_ok=True)

_ensure_ext()
open(f"{OUT}/index.html","w").write(build_index())
open(f"{OUT}/players.html","w").write(build_list("players.html","All players","Every professional Irish player on the books — abroad and at home.",PLAYERS))
open(f"{OUT}/abroad.html","w").write(build_list("abroad.html","Abroad","Irish players at clubs outside Ireland, top flight to smaller leagues.",[p for p in PLAYERS if p["tier"].startswith("abroad")]))
open(f"{OUT}/league-of-ireland.html","w").write(build_list("league-of-ireland.html","League of Ireland","Every Irish pro playing their football at home.",[p for p in PLAYERS if p["tier"]=="loi"]))
open(f"{OUT}/clubs.html","w").write(build_clubs_index())
open(f"{OUT}/faq.html","w").write(build_faq())
open(f"{OUT}/search.html","w").write(build_search())
json.dump(build_search_index(), open(f"{OUT}/search.json","w"), separators=(",",":"))
open(f"{OUT}/about.html","w").write(build_about())
open(f"{OUT}/where-are-the-irish.html","w").write(build_map())
os.makedirs(f"{OUT}/country", exist_ok=True)
os.makedirs(f"{OUT}/league", exist_ok=True)
_by_country = {}
for _p in PLAYERS: _by_country.setdefault(player_country(_p), []).append(_p)
for _c, _ps in _by_country.items():
    if _c in ("Other", NO_CLUB): continue          # no Other/No-club country pages
    _ps = [_p for _p in _ps if (_p.get("club") or "").strip()]
    if not _ps: continue
    open(f"{OUT}/country/{country_slug(_c)}.html","w").write(build_country(_c, _ps))
    _by_league = {}
    for _p in _ps:
        _lg = (_p.get("league") or "").strip()
        if not _lg or _lg in ("Other", "—"): continue
        _by_league.setdefault(_lg, []).append(_p)
    for _l, _lp in _by_league.items():
        open(f"{OUT}/league/{club_slug(_c)}-{club_slug(_l)}.html","w").write(build_league(_c, _l, _lp))
open(f"{OUT}/news.html","w").write(build_news())
os.makedirs(f"{OUT}/news", exist_ok=True)
os.makedirs(f"{OUT}/author", exist_ok=True)
for _w in authors():
    open(f"{OUT}/author/{_w['slug']}.html","w").write(build_author(_w))
for _a in ARTICLES:
    open(f"{OUT}/news/{art_slug(_a)}.html","w").write(build_article(_a))
open(f"{OUT}/ireland.html","w").write(build_ireland())
open(f"{OUT}/fixtures.html","w").write(build_fixtures())
open(f"{OUT}/milestones.html","w").write(build_milestones())
open(f"{OUT}/compare.html","w").write(build_compare())
open(f"{OUT}/transfers.html","w").write(build_transfers())
open(f"{OUT}/newsletter.html","w").write(build_newsletter())
open(f"{OUT}/alerts.html","w").write(build_alerts())
open(f"{OUT}/404.html","w").write(build_404())
open(f"{OUT}/sitemap.xml","w").write(build_sitemap())
open(f"{OUT}/robots.txt","w").write(build_robots())
open(f"{OUT}/favicon.svg","w").write(FAVICON)

clubs = {}
for p in PLAYERS: clubs.setdefault(p["club"], []).append(p)
for cname, ps in clubs.items():
    open(f"{OUT}/club/{club_slug(cname)}.html","w").write(build_club(cname, ps))
for p in PLAYERS:
    open(f"{OUT}/player/{p['slug']}.html","w").write(build_player(p))

# ---- old URLs ----------------------------------------------------------
# When a duplicate roster entry is merged away its page stops being built and
# every link to it — search results, a shared post, the newsletter — starts
# 404ing. Leave a redirect behind instead. data/manual/redirects.csv (from,to)
# is read too, so a future removal needs a CSV row rather than a code change.
RETIRED = {
    "will-fitzgerald":   "william-fitzgerald",
    "tommy-lonergan":    "tom-lonergan",
    "danny-mcnamara":    "dan-mcnamara",
    "ed-mccarthy":       "edward-mccarthy",
    "josh-okpolokpo":    "aaron-okpolokpo",
}
for _r in _rows("manual/redirects.csv"):
    if (_r.get("from") or "").strip() and (_r.get("to") or "").strip():
        RETIRED[_r["from"].strip()] = _r["to"].strip()
RETIRED.update({k: ALIAS[k] for k in ALIAS})       # anything this build merged

_live = {p["slug"] for p in PLAYERS}
_redir = 0
for _from, _to in RETIRED.items():
    if _from in _live or _to not in _live: continue
    open(f"{OUT}/player/{_from}.html","w").write(
        f'<!doctype html><meta charset="utf-8">'
        f'<meta http-equiv="refresh" content="0; url=/player/{_to}.html">'
        f'<link rel="canonical" href="https://footballers.ie/player/{_to}.html">'
        f'<meta name="robots" content="noindex">'
        f'<title>Moved</title>'
        f'<p>This page has moved to <a href="/player/{_to}.html">/player/{_to}.html</a>.</p>')
    _redir += 1
if _redir: print(f"  + {_redir} redirect{'s' if _redir!=1 else ''} for merged slugs")

os.makedirs(f"{OUT}/match", exist_ok=True)
_PM = _pmap()
_nmatch = 0
for m in MATCHES:
    # settled=False on purpose: the page is still wanted for a game a tracked
    # player's club played and he sat out, because his own page links to it.
    # It's the fixture list he comes off, not the record of the match.
    inv, _loi, _sq = match_squad(m, _PM, settled=False)
    if not inv: continue
    open(f"{OUT}/match/{match_id(m)}.html","w").write(build_match(m, inv, _sq))
    _nmatch += 1
print(f"  + {_nmatch} match pages")

if MISMATCHED:
    print(f"  ! {len(MISMATCHED)} players whose stats record belongs to someone else "
          f"- stats and photo withheld:")
    for _s, _n in sorted(MISMATCHED.items()):
        print(f"      {_s} -> feed had '{_n}'")

# Same name, but the two sources disagree about when they were born. One of
# them has the wrong player - or the wrong birthday. Worth a human look, so
# list them rather than guessing which source to believe.
_dobclash = []
for _p in PLAYERS:
    _t = _p.get("tm") or {}
    _a = _sane_age((_p.get("raw_age") or ""))
    _b = _age_from_dob(_t.get("dob", ""))
    if _a and _b and abs(_a - _b) >= 4:
        _dobclash.append((_p["slug"], _a, _b, _t.get("dob", "")))
if _dobclash:
    print(f"  ? {len(_dobclash)} players where the stats feed's age and the "
          f"date of birth disagree - check these are the same person:")
    for _s, _a, _b, _d in sorted(_dobclash):
        print(f"      {_s}: feed says {_a}, born {_d} makes them {_b}")
print(f"Built {9 + len(clubs) + len(PLAYERS)} pages ({len(clubs)} clubs, {len(PLAYERS)} players)")

# ---- assets: make build/site a complete, servable site ----
import shutil
# The news pipeline keeps its bookkeeping in data/api/ so it survives between
# runs on a throwaway runner. None of it belongs on the live domain: drafts are
# copy nobody has read yet, and the rest is internal. Everything else in data/
# is the site's own source and stays.
NOT_FOR_DEPLOY = {"news_drafts.json", "news_seen.json", "news_skipped.log"}
def _skip_internal(_dir, names):
    return [n for n in names if n in NOT_FOR_DEPLOY]

for _d in ("img", "images", "photos", "data"):
    _src = os.path.join(HERE, "..", _d)
    if os.path.isdir(_src):
        _dst = os.path.join(OUT, _d)
        if os.path.isdir(_dst): shutil.rmtree(_dst)
        shutil.copytree(_src, _dst, ignore=_skip_internal)

# the admin lives under /build/ on the live site
os.makedirs(os.path.join(OUT, "build"), exist_ok=True)
for _f in ("admin.html",):
    _p = os.path.join(HERE, _f)
    if os.path.exists(_p):
        shutil.copy2(_p, os.path.join(OUT, "build", _f))

for _f in ("og-image.png", "apple-touch-icon.png", "favicon.svg", "favicon.ico", "live.json"):
    _p = os.path.join(HERE, "..", _f)
    if os.path.exists(_p):
        shutil.copy2(_p, os.path.join(OUT, _f))

print(f"  + assets copied into {OUT}")
