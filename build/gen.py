# -*- coding: utf-8 -*-
import os, sys, re, html as H, json, csv
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

    api = {}
    for r in _rows("api/players.csv"):
        if not r.get("slug"): continue
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
    return out

def _merge_rows(name, key_fields):
    """Manual rows replace API rows with the same key; manual-only rows are appended."""
    api = _rows(f"api/{name}")
    man = _rows(f"manual/{name}")
    keyed = {tuple(r.get(k,"") for k in key_fields): r for r in api}
    for r in man:
        keyed[tuple(r.get(k,"") for k in key_fields)] = r
    return list(keyed.values())

EVENTS = {}
def load():
    tmdata = {r["slug"]: r for r in _rows("api/tm.csv") if r.get("slug")}
    global EVENTS
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
            source=(r.get("source") or "auto").strip().lower(),
            blanks={k for k in ("s_apps","s_starts","s_goals","s_assists","s_mins",
                                "s_yellow","s_red","c_apps","c_goals","c_assists")
                    if not (r.get(k) or "").strip()},
            photo=(r.get("photo") or "").strip(),
            photo_credit=(r.get("photo_credit") or "").strip(),
            fixtures=fixtures.get(slug, []), results=results.get(slug, [])))
    # ---- Transfermarkt bio/contract ----
    for p in players:
        t = tmdata.get(p["slug"])
        if not t:
            p["tm"] = None
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
    articles = [r for r in _rows("manual/articles.csv") if r.get("slug")]   # CSV order = display order (drag to reorder in the admin)
    accounts = _rows("manual/accounts.csv")
    clubgeo  = {r["club"]: r for r in _rows("manual/clubs.csv") if r.get("club")}
    return players, ireland, news, matches, articles, accounts, clubgeo, tmdata

PLAYERS, IRELAND, NEWS, MATCHES, ARTICLES, ACCOUNTS, CLUBGEO, TM = load()
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

NAV = [("News","news.html"),("Players","players.html"),
       ("Clubs","clubs.html"),("Ireland","ireland.html"),("Fixtures","fixtures.html"),("Alerts","alerts.html")]


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

def shell(title, desc, root, active, body, extra_head="", canonical="", body_attr=""):
    is_home = (root == "" and active in ("index.html", ""))
    loader    = LOADER_HTML if is_home else ""
    loader_js = LOADER_JS   if is_home else ""
    links = "".join(f'<a class="{"on" if active==href else ""}" href="{root}{href}">{l}</a>' for l,href in NAV)
    can = f"{SITE_URL}/{canonical}" if canonical else SITE_URL
    return f"""<!DOCTYPE html>
<html lang="en" data-theme="pitch">
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
<meta property="og:image" content="{SITE_URL}/og-image.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{esc(title)}">
<meta name="twitter:description" content="{esc(desc)}">
<meta name="twitter:image" content="{SITE_URL}/og-image.png">
<link rel="icon" href="{root}favicon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="{root}apple-touch-icon.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>{CSS}</style>{extra_head}
</head>
<body{body_attr}>
{loader}
{loader_js}<div class="wrap">
<nav>
  <a class="mark" href="{root}index.html">footballers<i>.ie</i></a>
  <button class="burger" id="burger" aria-label="Menu" aria-expanded="false" aria-controls="navlinks">
    <span></span><span></span><span></span>
  </button>
  <div class="navlinks" id="navlinks">{links}</div>
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
{body}
<footer>
  <div class="foothead">
    {'footballers.ie · prototype · sample data' if SAMPLE_DATA else 'footballers.ie'}
    <span class="updated" data-stamp="{DATA_STAMP}">checking for updates…</span>
  </div>
  Every Irish player at a professional club — abroad, senior international and League of Ireland
  <div class="footlinks">
    <a href="{root}faq.html">Where does this data come from?</a>
    <a href="{root}about.html">About &amp; methodology</a>
    <a href="{root}where-are-the-irish.html">Where are the Irish?</a>
    <a href="{root}alerts.html">Alerts</a>
  </div>
</footer>
</div>
<script>window.FB_SUBSCRIBE_URL={json.dumps(NEWSLETTER_ACTION)};window.FB_ACCOUNTS={json.dumps([{k:a.get(k,"") for k in ("email","name","role","hash")} for a in ACCOUNTS])};</script>
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
ACADEMY_RE = re.compile(r"\s+(u\d{2}|academy|reserves?|ii|b)$", re.I)
def _register_club_ids():
    """club name -> FotMob team id, from players.csv and clubs.csv.
       Academy sides (U21/U18/Academy) borrow the senior club's badge when we have one."""
    for r in _rows("api/players.csv"):
        if (r.get("club") or "").strip() and (r.get("club_id") or "").strip():
            CLUB_IDS.setdefault(r["club"].strip(), r["club_id"].strip())
    for r in _rows("manual/clubs.csv"):
        if (r.get("club") or "").strip() and (r.get("club_id") or "").strip():
            CLUB_IDS.setdefault(r["club"].strip(), r["club_id"].strip())
_register_club_ids()

def club_id(name):
    n = (name or "").strip()
    if not n: return ""
    parent = ACADEMY_RE.sub("", n)
    if parent != n and parent in CLUB_IDS: return CLUB_IDS[parent]
    return CLUB_IDS.get(n, "")

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
    return f'<span class="rate {cls}{" sm" if small else ""}" title="Season average FotMob match rating">{f:.2f}</span>'

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
LOI_COMPS = ("premier division","first division","league of ireland","fai cup","president's cup")
def is_loi_match(m, involved):
    """A League of Ireland game: nearly every player is Irish, so list them compactly."""
    comp=(m.get("competition") or "").strip().lower()
    if any(c in comp for c in LOI_COMPS): return True
    pmap={p["slug"]:p for p in PLAYERS}
    tiers=[(pmap.get(x["slug"]) or {}).get("tier","") for x in involved]
    return bool(tiers) and sum(1 for t in tiers if t=="loi") > len(tiers)/2

def match_payload():
    """Every match with at least one tracked player, for the client-side renderer."""
    pmap = {p["slug"]: p for p in PLAYERS}
    mc = []
    for m in MATCHES:
        if not m.get("kickoff"): continue
        involved = []
        for s in [x.strip() for x in (m.get("players") or "").split(";") if x.strip()]:
            p = pmap.get(s)
            if p: involved.append(dict(slug=s, n=esc(p["n"]), club=esc(p["club"]),
                                       ini=initials(p["n"]), pos=p["pos"],
                                       img=(1 if (not p.get("photo") and s in HAVE_IMG) else 0),
                                       photo=(p.get("photo") or "")))
        if not involved: continue
        mc.append(dict(id=match_id(m), kickoff=m["kickoff"], comp=esc(m.get("competition","")),
                       home=esc(m.get("home","")), away=esc(m.get("away","")),
                       hb=club_id(m.get("home","")), ab=club_id(m.get("away","")),
                       hs=m.get("home_score",""), as_=m.get("away_score",""),
                       status=(m.get("status") or "scheduled"), minute=m.get("minute",""),
                       hp=(m.get("home_pens") or ""), ap=(m.get("away_pens") or ""),
                       loi=(1 if is_loi_match(m, involved) else 0),
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
    HEAD = [(a.get("tag",""), a.get("headline",""), a.get("standfirst",""), a["slug"], _is_now(a), partner_of(a))
            for a in pool[:5]] or [(t,h,s,sl,False,None) for (t,h,s,sl) in NEWS]
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

    # milestones
    ms = milestone_items()[:4]
    msh = "".join(f'<a class="mscard" href="{plink(m["p"])}"><div class="mstag">{esc(m["tag"])}</div>'
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
    t_players = sum(len(m["players"]) for m in todays)
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
    for p in PLAYERS: by_c.setdefault(country_of(p["league"]), []).append(p)
    ex = "".join(f'<a class="exc" href="country/{country_slug(c)}.html"><b>{esc(c)}</b><span>{len(by_c[c])}</span></a>'
                 for c in sorted(by_c, key=lambda c: (-len(by_c[c]), c))[:12])
    explore = (f'<div class="sec"><h2>Where are the Irish?</h2><a class="more" href="clubs.html">Every country →</a></div>'
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

    <div class="sec" style="margin-top:26px"><h2>Form guide</h2><span class="more" style="border:0">FotMob ratings averaged over this season · <a href="about.html#ratings">how it works</a></span></div>
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
                 "", "index.html", body, canonical="")


def art_link(a, root=""): return f'{root}news/{a["slug"]}.html'

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
        lead, top, rest = ARTICLES[0], ARTICLES[1:4], ARTICLES[4:]
        main = art_card(lead, kind="lead")
        if top: main += '<div class="artgrid ntop">' + "".join(art_card(a) for a in top) + '</div>'
        if rest: main += ('<div class="sec"><h2>Latest</h2></div><div class="nlist">' +
                          "".join(art_card(a, kind="row") for a in rest) + '</div>')
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
        document.getElementById('tagempty').style.display=n?'none':'';}});
    }})();
    </script>
    '''
    return shell("News — footballers.ie", "Latest news on Irish professional footballers.",
                 "", "news.html", body, canonical="news.html")

def build_author(w):
    cards = '<div class="artgrid">' + "".join(art_card(a) for a in w["arts"]) + '</div>'
    body = f'''
    <a class="crumb" data-back href="../news.html">← News</a>
    <div class="pagehead authhead">{writer_avatar(w, "../", "lg")}
      <div><h1>{esc(w["name"])}</h1><p>{len(w["arts"])} article{"s" if len(w["arts"])!=1 else ""} for footballers.ie</p></div></div>
    {f'<div class="abox wbio">' + "".join(f"<p>{esc(x.strip())}</p>" for x in w["bio"].replace(chr(92)+"n",chr(10)).split(chr(10)) if x.strip()) + '</div>' if w.get("bio") else ""}
    {f'<div class="wlinks">' + "".join(f'<a href="{esc(u.strip())}" target="_blank" rel="noopener">{esc(l.strip())} →</a>' for l,u in w["links"]) + '</div>' if w.get("links") else ""}
    {cards}
    <script>document.addEventListener('click',function(e){{var au=e.target.closest('.au');if(au){{e.preventDefault();location.href=au.getAttribute('data-href');}}}});</script>
    '''
    return shell(f'{w["name"]} — footballers.ie', f'Articles by {w["name"]}.', "../", "news.html", body, canonical=f'author/{w["slug"]}.html')

def build_article(a):
    paras = "".join(f'<p>{esc(x.strip())}</p>' for x in (a.get("body") or "").replace("\\n","\n").split("\n") if x.strip())
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
    return shell(f'{a.get("headline","")} — footballers.ie',
                 a.get("standfirst",""), "../", "news.html", body,
                 canonical=f'news/{a["slug"]}.html')


LEAGUE_COUNTRY = {
  "Major League Soccer":"United States", "MLS":"United States", "First Professional League":"Bulgaria", "LaLiga":"Spain", "La Liga":"Spain", "Leagues Cup":"United States",
    "premier division":"Ireland", "league of ireland":"Ireland", "first division":"Ireland",
    "premier league":"England", "championship":"England", "league one":"England",
    "league two":"England", "national league":"England", "premier league 2":"England",
    "national league cup group a":"England", "efl cup":"England", "fa cup":"England",
    "premiership":"Scotland", "scottish premiership":"Scotland",
    "nifl premiership":"Northern Ireland",
    "serie a":"Italy", "serie b":"Italy",
    "ligue 1":"France", "ligue 2":"France",
    "eredivisie":"Netherlands", "first division b":"Belgium", "jupiler pro league":"Belgium",
    "liga portugal":"Portugal", "super lig":"Turkey", "süper lig":"Turkey",
    "nb i":"Hungary", "bundesliga":"Germany", "la liga":"Spain",
}
def country_of(league):
    l=(league or "").strip().lower()
    if not l: return "Other"
    if l in LEAGUE_COUNTRY: return LEAGUE_COUNTRY[l]
    for k,v in LEAGUE_COUNTRY.items():
        if k in l or l in k: return v
    return "Other"

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
   "match data is scraped from certain live score football apps, more in depth stuff like nationalit(ies), height, weight, transfer value is from another site that starts with transfer and ends with market."),
  ("Who calculates the ratings?",
   "the ratings is from said football live score app."),
  ("What does a rating of 8.52 actually mean?",
   "idk ask fotmob."),
  ("Can I compare ratings between leagues?",
   "i mean not really obhiously premier league is harder than the third divison of israeli football."),
  ("Which matches are included?",
   "any match that an irish professional footballer is playing in should be included, if it\u2019s not then report an issue and we\u2019ll get it added."),
  ("How often is it updated?",
   "there are several different scrapers that run automatically at different times, but there\u2019s at least one every hour pulling in fresh data, and if there\u2019s a match live then the score is updated every minute."),
  ("How is a player classified as Irish?",
   "if you\u2019re irish eligible and not locked into another country or better.."),
  ("What about Northern Ireland?",
   "you mean the north of ireland?? mate idk ask fotmob ffs."),
]

def build_about():
    body = f'''
    <div class="pagehead"><h1>About footballers.ie</h1><p>Straight answers, in the founder's own words.</p></div>

    <div class="sec"><h2 id="who">Who counts as Irish?</h2></div>
    <div class="abox"><p>if you're irish eligible and not locked into another country or better..</p>
      <p>Northern Ireland? you mean the north of ireland?? mate idk ask fotmob ffs.</p>
      <p>Right now that's <b>{len(PLAYERS)}</b> players. Think someone's missing or shouldn't be here? Use the Report button on any page.</p></div>

    <div class="sec"><h2 id="ratings">How do the ratings work?</h2></div>
    <div class="abox"><p>the ratings is from said football live score app. idk ask fotmob.</p>
      <p>Can you compare them between leagues? i mean not really obhiously premier league is harder than the third divison of israeli football.</p>
      <p>Everywhere except a match row, the number is the average of a player's match ratings this season.</p></div>

    <div class="sec"><h2 id="data">Where does the data come from?</h2></div>
    <div class="abox"><p>match data is scraped from certain live score football apps, more in depth stuff like nationalit(ies), height, weight, transfer value is from another site that starts with transfer and ends with market.</p>
      <p>there are several different scrapers that run automatically at different times, but there's at least one every hour pulling in fresh data, and if there's a match live then the score is updated every minute.</p>
      <p>The footer of every page shows when the data last updated.</p></div>

    <div class="sec"><h2 id="team">Contact</h2></div>
    <div class="abox"><p>Contact: contact@footballers.ie should work but to be safe id submit your question or whatever in the submit a report box.</p></div>

    <div class="sec"><h2 id="privacy">Your data</h2></div>
    <div class="abox"><p>If you follow players or subscribe: it's saved securely and added to our email list. We don't sell it, and every email has an unsubscribe link.</p></div>
    '''
    return shell("About — footballers.ie", "What footballers.ie is, who counts as Irish, and how the ratings work.", "", "about.html", body, canonical="about.html")

def build_faq():
    items = "".join(
        f'<details class="faq"><summary>{esc(q)}</summary><p>{esc(a)}</p></details>'
        for q, a in FAQ)
    body = (f'<div class="pagehead"><h1>Where does this data come from?</h1>'
            f'<p>How the numbers on this site are collected, what they mean, and what they don\'t.</p></div>'
            f'{items}'
            f'<div class="faqfoot">Still not answered? Hit <b>Report</b> in the corner of any page.</div>')
    return shell("Where the data comes from — footballers.ie",
                 "How footballers.ie collects and calculates its data.",
                 "", "faq.html", body, canonical="faq.html")


# rough centre of each country we track, for the map
COUNTRY_POINT = {
 "Ireland":(53.35,-7.7,7), "England":(52.6,-1.3,6), "Scotland":(56.5,-4.2,7),
 "Northern Ireland":(54.6,-6.4,8), "Wales":(52.3,-3.7,8),
 "Italy":(42.8,12.6,6), "France":(46.6,2.4,6), "Germany":(51.2,10.4,6),
 "Netherlands":(52.2,5.3,7), "Belgium":(50.6,4.5,8), "Spain":(40.2,-3.7,6),
 "Portugal":(39.6,-8.0,7), "Turkey":(39.0,35.2,6), "Hungary":(47.2,19.5,7),
 "Denmark":(56.0,10.0,7), "Sweden":(60.0,15.0,5), "Norway":(61.0,9.0,5),
 "Poland":(52.1,19.4,6), "Switzerland":(46.8,8.2,7), "Austria":(47.6,14.1,7),
 "Greece":(39.0,22.0,6), "USA":(39.8,-98.5,4), "Other":(48.0,10.0,4),
}

def build_map():
    """Where are the Irish? — a map you can click into."""
    by_country = {}
    for p in PLAYERS:
        by_country.setdefault(country_of(p["league"]), []).append(p)

    points = []
    for c, ps in sorted(by_country.items(), key=lambda kv: -len(kv[1])):
        lat, lon, zoom = COUNTRY_POINT.get(c, COUNTRY_POINT["Other"])
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
    legend = "".join(
        f'<button class="cbtn" data-i="{i}"><span class="cn">{esc(p["name"])}</span>'
        f'<span class="cnum">{p["n"]}</span></button>' for i, p in enumerate(points))

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
                 "", "clubs.html", body, canonical="where-are-the-irish.html")

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
    return shell(f"{title} — footballers.ie", sub, "", fname, body, canonical=fname)

# ================= CLUBS =================
def build_clubs_index():
    """Top level: pick a country."""
    by_country = {}
    for p in PLAYERS:
        by_country.setdefault(country_of(p["league"]), []).append(p)
    order = sorted(by_country, key=lambda c: (-len(by_country[c]), c))
    cards = ""
    for c in order:
        ps = by_country[c]
        leagues = len({p["league"] for p in ps if p["league"] not in ("","—")})
        cards += (f'<a class="clubcard" href="country/{country_slug(c)}.html">'
                  f'<div class="cn">{esc(c)}</div>'
                  f'<div class="cl2">{leagues} league{"s" if leagues!=1 else ""}</div>'
                  f'<div class="cc">{len(ps)} Irish player{"s" if len(ps)!=1 else ""}</div></a>')
    body = (f'<div class="pagehead"><h1>Clubs</h1>'
            f'<p>Pick a country, then a league, then a club.</p></div>'
            f'<a class="mapcta" href="where-are-the-irish.html">'
            f'<div><b>Where are the Irish?</b><span>See every country on a map</span></div>'
            f'<span class="go">Open map →</span></a>'
            f'<div class="clubgrid">{cards}</div>')
    return shell("Clubs — footballers.ie","Irish players by country, league and club.","", "clubs.html", body, canonical="clubs.html")

def build_country(cname, ps):
    """Second level: leagues within a country."""
    by_league = {}
    for p in ps:
        by_league.setdefault(p["league"] or "Other", []).append(p)
    order = sorted(by_league, key=lambda l: (-len(by_league[l]), l))
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
            f'<p>{len(ps)} Irish player{"s" if len(ps)!=1 else ""} across {len(order)} league{"s" if len(order)!=1 else ""}.</p></div>'
            f'<div class="clubgrid">{cards}</div>')
    return shell(f"{cname} — Irish players — footballers.ie",
                 f"Irish players in {cname}, by league.", "../", "clubs.html", body,
                 canonical=f"country/{country_slug(cname)}.html")

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
    body = (f'<a class="crumb" data-back href="../country/{country_slug(cname)}.html">← Back</a>'
            f'<div class="pagehead"><h1>{esc(lname)}</h1>'
            f'<p>{esc(cname)} · {len(ps)} Irish player{"s" if len(ps)!=1 else ""} at {len(order)} club{"s" if len(order)!=1 else ""}.</p></div>'
            f'<div class="clubgrid">{cards}</div>')
    return shell(f"{lname} — Irish players — footballers.ie",
                 f"Irish players in the {lname}.", "../", "clubs.html", body,
                 canonical=f"league/{club_slug(cname)}-{club_slug(lname)}.html")

def build_club(cname, ps):
    rows = "".join(player_row(p, "../") for p in ps)
    fx = ps[0]["fixtures"]
    fxr = "".join(f'<div class="fxrow"><div class="fxd">{esc(day_label(d))}</div><div class="fxo">{esc(o)} '
                  f'<span class="ha">{"H" if h=="H" else "A"}</span></div><div class="fxc">{esc(c)}</div></div>'
                  for d,o,h,c in fx)
    body = f'''
    <a class="crumb" data-back href="../clubs.html">← Back</a>
    <div class="pagehead"><h1>{club_badge(cname,"lg")}{esc(cname)}</h1><p>{esc(ps[0]["league"]) + " · " if ps[0]["league"] not in ("","—") else ""}{len(ps)} Irish player{"s" if len(ps)!=1 else ""} tracked</p></div>
    <div class="sec"><h2>Irish players</h2></div>
    <div class="tiergroup">{rows}</div>
    <div class="sec"><h2>Upcoming fixtures</h2></div>
    <div class="fxlist">{fxr or '<div class="emptystate" style="display:block">No fixtures listed.</div>'}</div>
    '''
    return shell(f"{cname} — Irish players — footballers.ie",
                 f"Irish professionals at {cname}, plus upcoming fixtures.", "../", "clubs.html", body,
                 canonical=f"club/{club_slug(cname)}.html")

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
        fxr = "".join(f'<div class="fxrow"><div class="fxd">{esc(day_label(d))}</div><div class="fxo">{esc(o)} '
                      f'<span class="ha">{h}</span></div><div class="fxc">{esc(cp)}</div></div>'
                      for d,o,h,cp in info["fixtures"])
        rsr = "".join(f'<div class="fxrow"><div class="fxd">{esc(day_label(d))}</div><div class="fxo">{esc(o)}</div>'
                      f'<div class="fxs">{esc(sc)}</div><div class="fxc">{esc(cp)}</div></div>'
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
                 "Ireland fixtures, results and capped players at every level.","", "ireland.html", body, canonical="ireland.html")

def build_fixtures():
    mc = match_payload()
    body = (f'<div class="pagehead"><h1>Fixtures</h1><p>Every game a tracked Irish player is involved in.</p></div>'
            f'<div class="fxbar"><div class="fxdays" id="fxdays"></div>'
            f'<div class="fxfilt" id="fxfilt"><button data-f="abroad" class="on">Abroad</button>'
            f'<button data-f="loi">League of Ireland</button><button data-f="all">All</button></div></div>'
            f'<div id="fxall"></div>'
            f'<script>window.FB_MATCHES={json.dumps(mc)};</script>')
    return shell("Fixtures — footballers.ie","Every game a tracked Irish player is involved in.","", "fixtures.html", body, canonical="fixtures.html")

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
    return shell("Milestones — footballers.ie","Irish players approaching career and international milestones.","", "milestones.html", body, canonical="milestones.html")

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
    return shell("Compare players — footballers.ie","Compare any two Irish professionals side by side.","", "compare.html", body, canonical="compare.html")


def match_id(m):
    return (m.get("kickoff","")[:10] + "-" + club_slug(m.get("home","")) + "-v-" + club_slug(m.get("away",""))).strip("-")

EV_ICON = {"goal":"⚽","own_goal":"⚽","yellow":"","red":"","second_yellow":"","missed_penalty":"✕"}
EV_LABEL = {"own_goal":"own goal","missed_penalty":"penalty missed","second_yellow":"second yellow","red":"red card","yellow":"yellow card"}
def events_block(m, involved):
    evs = EVENTS.get(match_id(m), [])
    if not evs: return ""
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
    if not rows: return ""
    scorers = [e for e in evs if (e.get("type") in ("goal","own_goal"))]
    summ = ""
    if scorers:
        hs = ", ".join(f'{esc(e.get("player",""))} {esc(e.get("minute",""))}\'' + (" (og)" if e.get("type")=="own_goal" else "") for e in sorted(scorers,key=_min) if (e.get("team","") or "").strip()==home.strip())
        as_ = ", ".join(f'{esc(e.get("player",""))} {esc(e.get("minute",""))}\'' + (" (og)" if e.get("type")=="own_goal" else "") for e in sorted(scorers,key=_min) if (e.get("team","") or "").strip()!=home.strip())
        summ = f'<div class="mscorers"><div>{hs or "—"}</div><div class="r">{as_ or "—"}</div></div>'
    return (f'<div class="sec"><h2>Timeline</h2>{f"<span class=\"more\" style=\"border:0\">{esc(venue)}</span>" if venue else ""}</div>'
            f'<div class="timeline">{rows}</div>'
            f'<div class="rmnote">Irish players in <b class="ir">green</b>. Goals, cards and missed penalties only.</div>')

def build_match(m, involved):
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
    rows = "".join(
        f'<a class="plrow" href="../player/{p["slug"]}.html">{avatar(p,"../","sm")}'
        f'<div class="nm">{esc(p["n"])} <span class="cl">{esc(p["club"])}</span></div>'
        f'<div class="ev">{p["pos"]}</div><div class="mn"></div>{star(p)}</a>' for p in involved)
    body = f'''
    <a class="crumb" data-back href="../fixtures.html">← Back</a>
    <div class="matchhead" id="mhead" data-mid="{match_id(m)}">
      <div class="mcrow"><span class="mccomp">{esc(m.get("competition",""))}</span><span id="mchip">{chip}</span></div>
      <div class="mteams">
        <div class="mteam">{club_badge(m.get("home",""),"md")}<span>{esc(m.get("home",""))}</span></div>
        <div id="mscorewrap">{scoreline}</div>
        <div class="mteam right"><span>{esc(m.get("away",""))}</span>{club_badge(m.get("away",""),"md")}</div>
      </div>
    </div>
    {events_block(m, involved)}
    <script>window.FB_MATCHES=[{json.dumps(dict(id=match_id(m), kickoff=m.get("kickoff",""), comp=esc(m.get("competition","")), home=esc(m.get("home","")), away=esc(m.get("away","")), hs=hs, as_=as_, status=status, minute=m.get("minute",""), players=[], loi=0))}];</script>
    <div class="mactions"><button class="starbtn" data-favm="{match_id(m)}" aria-pressed="false">★ <span>Follow match</span></button>
      <span class="mhint">Email updates when the score changes</span></div>
    <div class="sec"><h2>Irish players in this match</h2>
      <span class="more" style="border:0">{len(involved)}</span></div>
    <div class="tiergroup">{rows}</div>
    '''
    title = f'{m.get("home","")} v {m.get("away","")} — Irish players'
    return shell(f"{title} — footballers.ie",
                 f"Irish players involved in {m.get('home','')} v {m.get('away','')}.",
                 "../", "fixtures.html", body, canonical=f"match/{match_id(m)}.html")

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
def match_page_for(p, date, opponent):
    """Find the match page for a player's result row, if the match centre has that game."""
    global _MATCH_LOOKUP
    if _MATCH_LOOKUP is None:
        _MATCH_LOOKUP = {}
        for m in MATCHES:
            k = m.get("kickoff","")[:10]
            for side in ("home","away"):
                _MATCH_LOOKUP.setdefault((k, club_slug(m.get(side,""))), match_id(m))
    return _MATCH_LOOKUP.get(((date or "")[:10], club_slug(opponent)))

def build_player(p):
    s, c = p["season"], p["career"]
    badge = "League of Ireland" if p["tier"]=="loi" else ("Abroad · top flight" if p["tier"]=="abroad-top" else "Abroad")

    if has_data(p):
        statsblock = f'''
    <div class="sec"><h2>Season {esc(p.get("season_label") or SEASON)}</h2>
      {'<span class="more stale" style="border:0">Last season · no appearances yet this season</span>'
       if not season_is_current(p.get("season_label")) else ''}</div>
    <div class="pdstats">
      <div class="pds"><div class="n">{stat(p,"s_apps",s["ap"])}</div><div class="l">Apps</div></div>
      <div class="pds"><div class="n">{stat(p,"s_starts",s["starts"])}</div><div class="l">Starts</div></div>
      <div class="pds"><div class="n">{stat(p,"s_goals",s["g"])}</div><div class="l">Goals</div></div>
      <div class="pds"><div class="n">{stat(p,"s_assists",s["a"])}</div><div class="l">Assists</div></div>
      <div class="pds"><div class="n">{stat(p,"s_mins",s["mins"])}</div><div class="l">Minutes</div></div>
      <div class="pds"><div class="n"><span class="card yel">{stat(p,"s_yellow",s["yellow"])}</span></div><div class="l">Yellow cards</div></div>
      <div class="pds"><div class="n"><span class="card red">{stat(p,"s_red",s["red"])}</span></div><div class="l">Red cards</div></div>
      <div class="pds"><div class="n">{rating_chip(p)}</div><div class="l">Avg rating · FotMob</div></div>
      <div class="pds"><div class="n">{stat(p,"c_apps",c["ap"])}</div><div class="l">Career apps</div></div>
      <div class="pds"><div class="n">{stat(p,"c_goals",c["g"])}</div><div class="l">Career goals</div></div>
    </div>'''
    else:
        statsblock = (
            '<div class="sec"><h2>Season data</h2></div>'
            '<div class="nodata">We track this player, but our data source doesn\'t cover their '
            'club yet — so there are no appearances, ratings or match records to show. '
            'Their profile will fill in as soon as it does. '
            'Think that\'s wrong? <a href="#" onclick="window.FB_REPORT&&FB_REPORT();return false">Report it</a>.</div>')

    upcoming = p["fixtures"]
    n_fx = len(p["fixtures"])
    fx_more = f'<span class="more" style="border:0">{n_fx} upcoming</span>' if n_fx else ""
    def _fxrow(d,o,h,cp):
        mid = match_page_for(p, d, o)
        tag, href = ("a", f' href="../match/{mid}.html"') if mid else ("div", "")
        return (f'<{tag} class="fxrow when{" lnk" if mid else ""}"{href} data-when="{esc(d)}" data-opp="{esc(o)}" data-ha="{h}">'
                f'<div class="fxwhen">{esc(day_label(d))}</div>'
                f'<div class="fxc">{esc(cp)}</div></{tag}>')
    fxr = "".join(_fxrow(d,o,h,cp) for d,o,h,cp in upcoming)
    rsr = ""
    recent_results = list(reversed(p["results"]))[:10]   # feed is oldest-first
    for row in recent_results:
        d,o,sc,cp,mins,g,a = row[:7]
        rt = row[7] if len(row) > 7 else ""
        ev = "".join(f'<span class="evi g" title="Goal">⚽</span>' for _ in range(g or 0)) + \
             "".join(f'<span class="evi a" title="Assist">A</span>' for _ in range(a or 0))
        res = result_class(sc)
        mid = match_page_for(p, d, o)
        tag = "a" if mid else "div"
        href = f' href="../match/{mid}.html"' if mid else ""
        rsr += (f'<{tag} class="rmrow{" lnk" if mid else ""}"{href}><div class="rmd">{esc(day_label(d))}</div>'
                f'<div class="rmo">{esc(o)}<span class="cl">{esc(cp)}</span></div>'
                f'<div class="rms {res}">{esc(sc)}</div>'
                f'<div class="rmm">{mins}\'</div>'
                f'<div class="rme">{ev}</div>'
                f'<div class="rmr">{rating_pill(rt)}</div></{tag}>')
    if rsr:
        rsr = ('<div class="rmhead"><div>Date</div><div>Opponent</div><div>Score</div><div>Mins</div><div>G/A</div><div>Rating</div></div>' + rsr)

    # international
    intl = ""
    if p["intl_senior"] or p["intl_youth"]:
        blocks = ""
        if p["intl_senior"]:
            i = p["intl_senior"]
            lv = IRELAND["Senior"]
            fx = "".join(f'<div class="fxrow"><div class="fxd">{esc(day_label(d))}</div><div class="fxo">{esc(o)} '
                         f'<span class="ha">{h}</span></div><div class="fxc">{esc(cp)}</div></div>'
                         for d,o,h,cp in lv["fixtures"])
            rs = "".join(f'<div class="fxrow"><div class="fxd">{esc(day_label(d))}</div><div class="fxo">{esc(o)}</div>'
                         f'<div class="fxs">{esc(sc)}</div><div class="fxc">{esc(cp)}</div></div>'
                         for d,o,sc,cp in lv["results"])
            blocks += (f'<a class="intlblock lnk" href="../ireland.html?level=Senior">'
                       f'<div class="ilvl">Senior <span>{i["caps"]} caps · {i["goals"]} goals · debut {i["debut"]}</span>'
                       f'<span class="go">View squad →</span></div>'
                       f'<div class="isub">Upcoming</div><div class="fxlist">{fx}</div>'
                       f'<div class="isub">Recent results</div><div class="fxlist">{rs}</div></a>')
        for lvl, caps, goals, since in p["intl_youth"]:
            txt = f'{caps} caps · {goals} goals · from {since}' if caps is not None else "Called up at this level"
            blocks += (f'<a class="intlblock lnk" href="../ireland.html?level={esc(lvl)}">'
                       f'<div class="ilvl">{esc(lvl)} <span>{txt}</span>'
                       f'<span class="go">View squad →</span></div></a>')
        intl = f'<div class="sec"><h2>International</h2><a class="more" href="../ireland.html">Ireland hub →</a></div>{blocks}'

    # eligibility: what the roster says, plus every nationality Transfermarkt lists
    elig = ""
    shown = set()
    tied_to = next((c for c, st in p["eligible"] if st == "tied"), None)
    for country, status in p["eligible"]:
        if status == "blocked":
            cls, note = "elig blocked", "No longer available"
        elif status == "tied":
            cls, note = "elig tied", "Cap-tied · committed"
        elif p["intl_youth"] and country == "Republic of Ireland":
            cls, note = "elig youth", "Played underage · can still switch"
        else:
            cls, note = "elig open", "Eligible · never played"
        shown.add(country.lower()); shown.add(country.lower().replace("republic of ",""))
        elig += f'<div class="{cls}"><span class="ec">{esc(country)}</span><span class="en">{note}</span></div>'
    for nat in (p.get("tm") or {}).get("nations", []) or []:
        n = nat.strip()
        if not n or n.lower() in shown or n.lower() in ("ireland","republic of ireland") and "republic of ireland" in shown: continue
        shown.add(n.lower())
        if tied_to:
            elig += f'<div class="elig off"><span class="ec">{esc(n)}</span><span class="en">Closed · cap-tied elsewhere</span></div>'
        else:
            elig += f'<div class="elig open"><span class="ec">{esc(n)}</span><span class="en">Also eligible</span></div>'
    tie_note = ("Cap-tied to Ireland — a competitive senior appearance means the other associations below are closed off."
                if p["cap_status"]=="senior_comp" else
                "Youth caps and senior friendlies don't cap-tie a player, so a switch is still possible under FIFA rules."
                if p["cap_status"] in ("youth","senior_friendly") or p["intl_youth"] else
                "Uncapped at any level — free to commit to any association they qualify for.")

    trans = "".join(f'<div class="trow"><div class="ty">{esc(y)}</div><div class="tf">{esc(f)} → <b>{esc(t)}</b></div>'
                    f'<div class="tfee">{esc(fee)}</div></div>' for y,f,t,fee in p["transfers"])

    body = f'''
    <a class="crumb" data-back href="../players.html">← Back</a>
    <div class="pdhead">
      <div class="pdid">
        {avatar(p, "../")}
        <div>
        <div class="pdname">{esc(p["n"])}</div>
        <div class="pdmeta">{" · ".join(filter(None,[
            f'<a href="{clink(p["club"],"../")}">{club_badge(p["club"])}{esc(p["club"])}</a>',
            esc(p["league"]) if p["league"] not in ("","—") else "",
            p["pos"] if p["pos"] not in ("","—") else "",
            str(p["age"]) if p["age"] else "",
            (f'<span class="loanfrom">on loan from {esc(p["parent_club"])}</span>'
             if p.get("loan") and p.get("parent_club") and p["parent_club"] != p["club"] else ""),
            (f'<b>{p["intl_senior"]["caps"]} caps</b>' if p["intl_senior"]["caps"] is not None
             else '<b>Senior international</b>') if p["intl_senior"] else ""]))}</div>
        {f'<div class="pdfull">{esc(p["tm"]["full_name"])}</div>'
          if p.get("tm") and p["tm"]["full_name"] and p["tm"]["full_name"] != p["n"] else ""}
        {f'<div class="pdborn">Born {esc(p["born"])}' + (" · " + esc(p["foot"]) + " footed" if p["foot"] else "") + "</div>" if p["born"] else ""}
        {f'<div class="pcredit">Photo: {esc(p["photo_credit"])}</div>' if p.get("photo_credit") else ""}
        </div>
      </div>
      <div class="pdactions">
        <button class="starbtn" data-fav="{p["slug"]}" aria-pressed="false">★ <span>Follow</span></button>
        <div class="pdbadge">{badge}</div>
      </div>
    </div>

    {statsblock}
    {bio_block(p)}

    <div class="sec"><h2>Upcoming fixtures</h2>{fx_more}</div>
    <div class="fxlist">{fxr or '<div class="emptystate" style="display:block">No fixtures listed.</div>'}</div>

    <div class="sec"><h2>Recent matches</h2>
      <span class="more" style="border:0">last {len(recent_results)} of {len(p["results"])}</span></div>
    <div class="fxlist rmlist">{rsr or '<div class="emptystate" style="display:block">No appearances yet.</div>'}</div>
    <div class="rmnote">Only games they were on the pitch for. Unused subs and squad omissions aren\'t listed.
      {'Match ratings aren\'t available for this player yet.' if not any(len(r)>7 and r[7] for r in p["results"]) else ''}
      Something wrong? <a href="#" onclick="window.FB_REPORT&&FB_REPORT();return false">Report it</a>.</div>

    {intl}

    <div class="sec"><h2>Eligibility</h2></div>
    <div class="eliglist">{elig}</div>
    <p class="eligNote">{tie_note}</p>

    {f'<div class="sec"><h2>Transfers</h2></div><div class="tlist">{trans}</div>' if trans else ''}
    '''
    return shell(f"{p['n']} — footballers.ie",
                 f"{p['n']} ({p['club']}, {p['league']}) — season stats, fixtures, results and international record.",
                 "../", "", body, canonical=f"player/{p['slug']}.html",
                 body_attr=f' data-player="{p["slug"]}"')



def build_newsletter():
    body = f'''
    <div class="pagehead"><h1>The <i>newsletter</i></h1>
      <p>Two emails a week on every Irish professional — written by hand, not scraped together.</p></div>
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
                 "", "newsletter.html", body, canonical="newsletter.html")


def build_alerts():
    form_open = f'<form class="alertform" action="{NEWSLETTER_ACTION}" method="post" target="_blank">' if NEWSLETTER_ACTION \
                else '<form class="nlform" onsubmit="event.preventDefault();document.getElementById(\'alertnote\').style.display=\'block\';">'
    body = f'''
    <div class="pagehead"><h1>Player <i>alerts</i></h1>
      <p>Follow players with the ★ anywhere on the site, then get an email when they're involved.</p></div>

    <div class="nlbox">
      <div class="nltag">Alerts</div>
      <h3 class="nlh">Your players, straight to your inbox</h3>
      <div class="alertgrid">
        <label class="alertopt"><input type="checkbox" name="alert_lineup" checked> <span><b>About to play</b>
          An hour before kick-off when one of your players is named in the squad.</span></label>
        <label class="alertopt"><input type="checkbox" name="alert_goal" checked> <span><b>Goal or assist</b>
          The moment they're involved.</span></label>
        <label class="alertopt"><input type="checkbox" name="alert_rating"> <span><b>Full-time rating</b>
          Their match rating and minutes once the game's done.</span></label>
        <label class="alertopt"><input type="checkbox" name="alert_news"> <span><b>Transfers &amp; injuries</b>
          Moves, call-ups and fitness news.</span></label>
      </div>
      <div class="alertwho">Following <b data-fav-count>0</b> player<span data-fav-plural></span>.
        <span id="alertnames"></span></div>
      {form_open}
        <input type="email" name="{NEWSLETTER_FIELD}" placeholder="your@email.ie" required aria-label="Email address">
        <input type="hidden" name="players" id="alertplayers">
        <button type="submit">Turn on alerts</button>
      </form>
      <div class="nlnote" id="alertnote" style="display:none">Alerts aren't switched on yet — the list opens shortly.</div>
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
    }})();
    </script>'''
    return shell("Player alerts — footballers.ie",
                 "Follow Irish players and get an email when they play, score or assist.",
                 "", "alerts.html", body, canonical="alerts.html")

# ================= 404 / SITEMAP / ROBOTS =================
def build_404():
    body = """
    <div class="pagehead" style="padding-top:60px">
      <h1>That page doesn't exist</h1>
      <p>The link may be out of date, or the player may not be tracked here. Try a search instead.</p>
    </div>
    <div class="filterbar" style="max-width:520px">
      <a class="tab" href="/players.html">All players</a>
      <a class="tab" href="/abroad.html">Abroad</a>
      <a class="tab" href="/league-of-ireland.html">League of Ireland</a>
      <a class="tab" href="/index.html">Home</a>
    </div>"""
    return shell("Page not found — footballers.ie", "That page doesn't exist on Footballers.", "/", "", body)

def build_sitemap():
    urls = ["", "news.html", "faq.html", "about.html", "where-are-the-irish.html", "players.html", "abroad.html", "league-of-ireland.html", "clubs.html",
            "ireland.html", "fixtures.html", "milestones.html", "compare.html", "newsletter.html", "alerts.html"]
    urls += [f"club/{club_slug(c)}.html" for c in sorted(set(p["club"] for p in PLAYERS))]
    urls += [f"player/{p['slug']}.html" for p in PLAYERS]
    urls += [f"news/{a['slug']}.html" for a in ARTICLES]
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
open(f"{OUT}/about.html","w").write(build_about())
open(f"{OUT}/where-are-the-irish.html","w").write(build_map())
os.makedirs(f"{OUT}/country", exist_ok=True)
os.makedirs(f"{OUT}/league", exist_ok=True)
_by_country = {}
for _p in PLAYERS: _by_country.setdefault(country_of(_p["league"]), []).append(_p)
for _c, _ps in _by_country.items():
    open(f"{OUT}/country/{country_slug(_c)}.html","w").write(build_country(_c, _ps))
    _by_league = {}
    for _p in _ps: _by_league.setdefault(_p["league"] or "Other", []).append(_p)
    for _l, _lp in _by_league.items():
        open(f"{OUT}/league/{club_slug(_c)}-{club_slug(_l)}.html","w").write(build_league(_c, _l, _lp))
open(f"{OUT}/news.html","w").write(build_news())
os.makedirs(f"{OUT}/news", exist_ok=True)
os.makedirs(f"{OUT}/author", exist_ok=True)
for _w in authors():
    open(f"{OUT}/author/{_w['slug']}.html","w").write(build_author(_w))
for _a in ARTICLES:
    open(f"{OUT}/news/{_a['slug']}.html","w").write(build_article(_a))
open(f"{OUT}/ireland.html","w").write(build_ireland())
open(f"{OUT}/fixtures.html","w").write(build_fixtures())
open(f"{OUT}/milestones.html","w").write(build_milestones())
open(f"{OUT}/compare.html","w").write(build_compare())
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

os.makedirs(f"{OUT}/match", exist_ok=True)
_pmap = {p["slug"]: p for p in PLAYERS}
_nmatch = 0
for m in MATCHES:
    inv = [_pmap[s] for s in [x.strip() for x in (m.get("players") or "").split(";") if x.strip()] if s in _pmap]
    if not inv: continue
    open(f"{OUT}/match/{match_id(m)}.html","w").write(build_match(m, inv))
    _nmatch += 1
print(f"  + {_nmatch} match pages")

print(f"Built {9 + len(clubs) + len(PLAYERS)} pages ({len(clubs)} clubs, {len(PLAYERS)} players)")

# ---- assets: make build/site a complete, servable site ----
import shutil
for _d in ("img", "images", "photos", "data"):
    _src = os.path.join(HERE, "..", _d)
    if os.path.isdir(_src):
        _dst = os.path.join(OUT, _d)
        if os.path.isdir(_dst): shutil.rmtree(_dst)
        shutil.copytree(_src, _dst)

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
