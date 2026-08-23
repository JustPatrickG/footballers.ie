# -*- coding: utf-8 -*-
import os, sys, html as H, json, csv
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data")

# ---- config ----
SITE_URL   = "https://footballers.ie"
SEASON     = "2026/27"
MATCHWEEK  = "MW04 · 22-23 AUG"
SAMPLE_DATA = True   # set False once every figure on the site is real

# Newsletter: paste your provider's form-action URL here (Buttondown, Beehiiv,
# Mailchimp, Kit — they all give you one). Until then the form shows a notice.
NEWSLETTER_ACTION = ""      # e.g. "https://buttondown.email/api/emails/embed-subscribe/footballers"
NEWSLETTER_FIELD  = "email" # Buttondown/Beehiiv use "email"; Mailchimp uses "EMAIL"

def _rows(path):
    full = os.path.join(DATA, path)
    if not os.path.exists(full): return []
    with open(full, newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if any((v or "").strip() for v in r.values())]

def _int(v, d=0):
    try: return int(str(v).strip())
    except: return d

def _merge_players():
    """API layer provides the facts; manual layer overrides any non-empty cell.
    A manual row with locked=yes ignores the API entirely."""
    api = {r["slug"]: r for r in _rows("api/players.csv") if r.get("slug")}
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
                if (v or "").strip():          # manual wins when filled in
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

def load():
    fixtures, results = {}, {}
    for r in _merge_rows("fixtures.csv", ("slug","date","opponent")):
        if r.get("slug"):
            fixtures.setdefault(r["slug"], []).append(
                (r["date"], r["opponent"], r["home_away"], r["competition"]))
    for r in _merge_rows("results.csv", ("slug","date","opponent")):
        if r.get("slug"):
            results.setdefault(r["slug"], []).append(
                (r["date"], r["opponent"], r["score"], r["competition"],
                 _int(r["minutes"]), _int(r["goals"]), _int(r["assists"])))

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
            photo=(r.get("photo") or "").strip(),
            photo_credit=(r.get("photo_credit") or "").strip(),
            fixtures=fixtures.get(slug, []), results=results.get(slug, [])))
    players.sort(key=lambda p: p["n"])

    ireland = {}
    for r in _rows("manual/ireland.csv"):
        lv = ireland.setdefault(r["level"], dict(label=r["level"], fixtures=[], results=[]))
        if r["type"] == "fixture": lv["fixtures"].append((r["date"], r["opponent"], r["home_away"], r["competition"]))
        else: lv["results"].append((r["date"], r["opponent"], r["score"], r["competition"]))
    news = [(r["tag"], r["headline"], r["standfirst"], r["player_slug"]) for r in _rows("manual/news.csv")]
    return players, ireland, news

PLAYERS, IRELAND, NEWS = load()
TIERS = {"abroad-top":"Abroad — top divisions",
         "abroad-lower":"Abroad — second tier & smaller leagues",
         "loi":"League of Ireland"}

def esc(s): return H.escape(str(s), quote=False)
OUT = "site"

CSS = open(os.path.join(HERE, "style.css")).read()
APPJS = open(os.path.join(HERE, "app.js")).read()

NAV = [("Players","players.html"),("Abroad","abroad.html"),("League of Ireland","league-of-ireland.html"),
       ("Clubs","clubs.html"),("Ireland","ireland.html"),("Fixtures","fixtures.html"),("Alerts","alerts.html")]

def shell(title, desc, root, active, body, extra_head="", canonical=""):
    links = "".join(f'<a class="{"on" if active==href else ""}" href="{root}{href}">{l}</a>' for l,href in NAV)
    can = f"{SITE_URL}/{canonical}" if canonical else SITE_URL
    return f"""<!DOCTYPE html>
<html lang="en">
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
<body>
<div class="wrap">
<nav>
  <a class="mark" href="{root}index.html">footballers<i>.ie</i></a>
  <button class="burger" id="burger" aria-label="Menu" aria-expanded="false" aria-controls="navlinks">
    <span></span><span></span><span></span>
  </button>
  <div class="navlinks" id="navlinks">{links}</div>
  <div class="navmeta">{esc(MATCHWEEK)} · <b>{len(PLAYERS)}</b> TRACKED</div>
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
  {'Footballers · prototype · sample data<br>' if SAMPLE_DATA else 'Footballers<br>'}
  Every Irish player at a professional club — abroad, senior international and League of Ireland
</footer>
</div>
<script>{APPJS}</script>
</body>
</html>"""

def club_slug(c): return c.lower().replace("'","").replace(".","").replace("ı","i").replace("İ","i").replace(" ","-")
def plink(p, root=""): return f'{root}player/{p["slug"]}.html'
def clink(c, root=""): return f'{root}club/{club_slug(c)}.html'

def ev_str(p):
    r = p["results"][0] if p["results"] else None
    if not r: return "—", 0
    _,_,_,_,mins,g,a = r
    bits = []
    if g: bits.append("⚽"*g)
    if a: bits.append("🅰"*a)
    return (" ".join(bits) if bits else "—"), mins

def player_row(p, root=""):
    ev, mins = ev_str(p)
    return (f'<a class="plrow" href="{plink(p,root)}">{avatar(p, root, "sm")}'
            f'<div class="nm">{esc(p["n"])} '
            f'<span class="cl">{esc(p["club"])}</span></div><div class="ev">{ev}</div>'
            f'<div class="mn">{mins}\'</div>{star(p)}</a>')




def star(p):
    return f'<button class="star" data-fav="{p["slug"]}" aria-pressed="false" aria-label="Follow {esc(p["n"])}">★</button>'

def next_fixture(p):
    return f'{p["fixtures"][0][0]} v {p["fixtures"][0][1]}' if p["fixtures"] else ""

def initials(name):
    parts = [p for p in name.split() if p]
    return (parts[0][0] + parts[-1][0]).upper() if len(parts) > 1 else (parts[0][:2].upper() if parts else "?")

def avatar(p, root="", size="lg"):
    cls = "pavatar" + (" sm" if size == "sm" else "")
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
        form = '''<form class="nlform" onsubmit="event.preventDefault();this.nextElementSibling.style.display='block';">
          <input type="email" placeholder="your@email.ie" required aria-label="Email address">
          <button type="submit">Subscribe</button>
        </form>
        <div class="nlnote" style="display:none">Sign-ups open shortly — the list isn't live yet.</div>'''
    if compact:
        return f'''<div class="nlbar">
          <div class="nltext"><b>Two emails a week.</b> Monday round-up, Friday preview.</div>
          {form}
        </div>'''
    return f'''<div class="nlbox">
      <div class="nltag">Newsletter</div>
      <h3 class="nlh">Every Irish pro, in your inbox twice a week</h3>
      <div class="nlcols">
        <div class="nlcol"><div class="nlday">Monday</div>
          <p>How every Irish player got on at the weekend, plus what's coming up — with a proper look at the midweek games that usually go unnoticed.</p></div>
        <div class="nlcol"><div class="nlday">Friday</div>
          <p>Who's playing this weekend, a recap of the weekday matches, and the latest injury news.</p></div>
      </div>
      {form}
      <div class="nlfine">Written by hand. No spam, unsubscribe any time.</div>
    </div>'''

# ================= HOME =================
def build_index():
    GOALS = [p for p in PLAYERS if p["results"] and (p["results"][0][5] or p["results"][0][6])]
    HEAD = NEWS
    slides = "".join(
      f'<a class="slide {"on" if i==0 else ""}" data-i="{i}" href="player/{s[3]}.html">'
      f'<div class="tag">{esc(s[0])}</div><h3>{esc(s[1])}</h3><p>{esc(s[2])}</p></a>'
      for i,s in enumerate(HEAD))
    dots = "".join(f'<button aria-current="{"true" if i==0 else "false"}" data-i="{i}"></button>' for i in range(len(HEAD)))

    gi = ""
    for p in GOALS:
        _,_,_,_,mins,g,a = p["results"][0]
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

    # ---- who's playing, grouped by date ----
    byday = {}
    for p in PLAYERS:
        for d, o, h, comp in p["fixtures"]:
            byday.setdefault(d, []).append((p, o, h, comp))
    def fxrow(p, o, h, comp):
        return (f'<a class="plrow" href="{plink(p)}">{avatar(p,"","sm")}'
                f'<div class="nm">{esc(p["n"])} <span class="cl">{esc(p["club"])}</span></div>'
                f'<div class="ev">v {esc(o)} <span class="ha">{h}</span></div>'
                f'<div class="mn">{esc(comp)}</div>{star(p)}</a>')
    upcoming = ""
    for d in list(byday.keys())[:4]:
        items = "".join(fxrow(*x) for x in byday[d])
        upcoming += (f'<div class="tiergroup"><h4><span>{esc(d)}</span>'
                     f'<span>{len(byday[d])} Irish player{"s" if len(byday[d])!=1 else ""}</span></h4>{items}</div>')
    fixtures_block = (f'<div class="sec"><h2>Playing next</h2><a class="more" href="fixtures.html">All fixtures →</a></div>'
                      f'{upcoming}') if byday else (
                      '<div class="sec"><h2>Playing next</h2></div>'
                      '<div class="emptybox">No fixtures loaded yet. They appear here as soon as the '
                      'fixture feed is connected — every Irish player, every game, newest first.</div>')

    # ---- abroad / LOI split ----
    abroad = [p for p in PLAYERS if p["tier"].startswith("abroad")]
    loi    = [p for p in PLAYERS if p["tier"] == "loi"]
    def block(title, group, href, limit=8):
        rows = "".join(player_row(p) for p in group[:limit])
        return (f'<div class="sec"><h2>{title}</h2><a class="more" href="{href}">All {len(group)} →</a></div>'
                f'<div class="tiergroup">{rows}</div>') if group else ""

    news_block = (f'<div class="sec"><h2>News</h2></div>'
                  f'<div class="carousel">{slides}<div class="dots">{dots}</div></div>') if HEAD else ""
    goals_block = (f'<div class="sec"><h2>Goal involvements</h2><a class="more" href="abroad.html">All →</a></div>'
                   f'<div class="giband">{gi}</div>') if GOALS else ""

    # ---- data for the client-side favourites rail ----
    fbp = {p["slug"]: dict(n=esc(p["n"]), club=esc(p["club"]), ini=initials(p["n"]),
                           next=next_fixture(p)) for p in PLAYERS}

    body = f'''
    <div id="myplayers-sec" style="display:none">
      <div class="sec" style="margin-top:26px"><h2>Your players <span class="cnt" data-fav-count>0</span></h2>
        <a class="more" href="alerts.html">Get alerts →</a></div>
      <div class="tiergroup" id="myplayers"></div>
    </div>
    <div class="emptybox" id="myplayers-empty" style="display:none">
      <b>Follow your players.</b> Tap the ★ beside any name and they'll appear up here —
      who they're playing next, how they got on. <a href="alerts.html">Get an email when they play →</a>
    </div>

    {fixtures_block}

    {block("Abroad", abroad, "abroad.html")}
    {block("League of Ireland", loi, "league-of-ireland.html")}

    {news_block}
    {goals_block}

    {signup()}
    <script>window.FB_PLAYERS={json.dumps(fbp)};</script>
'''
    return shell("FOOTBALLERS — every Irish professional, tracked",
                 "News, goal involvements and the full weekend round-up for every Irish professional footballer.",
                 "", "index.html", body, canonical="")

# ================= LIST PAGES =================
def build_list(fname, title, sub, data):
    rows = ""
    for p in data:
        ev, mins = ev_str(p)
        rows += (f'<a class="plrow pl-item" data-name="{esc(p["n"]).lower()} {esc(p["club"]).lower()}" '
                 f'data-pos="{p["pos"]}" data-league="{esc(p["league"])}" href="{plink(p)}">'
                 f'{avatar(p)}'
                 f'<div class="nm">{esc(p["n"])} <span class="cl">{esc(p["club"])}</span></div>'
                 f'<div class="ev">{ev}</div><div class="mn">{mins}\'</div>{star(p)}</a>')
    leagues = sorted(set(p["league"] for p in data))
    lopts = "".join(f'<option>{esc(l)}</option>' for l in leagues)
    body = f'''
    <div class="pagehead"><h1>{esc(title)}</h1><p>{esc(sub)}</p></div>
    <div class="filterbar">
      <input type="search" id="q" placeholder="Search player or club">
      <select id="posf"><option value="">All positions</option><option>GK</option><option>DEF</option><option>MID</option><option>FWD</option></select>
      <select id="lgf"><option value="">All leagues</option>{lopts}</select>
    </div>
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
    q.oninput=draw;posf.onchange=draw;lgf.onchange=draw;
    </script>'''
    return shell(f"{title} — FOOTBALLERS", sub, "", fname, body, canonical=fname)

# ================= CLUBS =================
def build_clubs_index():
    clubs = {}
    for p in PLAYERS: clubs.setdefault(p["club"], []).append(p)
    cards = ""
    for c in sorted(clubs):
        ps = clubs[c]
        cards += (f'<a class="clubcard" href="{clink(c)}"><div class="cn">{esc(c)}</div>'
                  f'<div class="cl2">{esc(ps[0]["league"]) if ps[0]["league"] not in ("","—") else "&nbsp;"}</div>'
                  f'<div class="cc">{len(ps)} Irish player{"s" if len(ps)!=1 else ""}</div></a>')
    body = (f'<div class="pagehead"><h1>Clubs</h1><p>Every club with an Irish professional on the books, at home and abroad.</p></div>'
            f'<div class="clubgrid">{cards}</div>')
    return shell("Clubs — FOOTBALLERS","Every club with an Irish professional on the books.","", "clubs.html", body, canonical="clubs.html")

def build_club(cname, ps):
    rows = "".join(player_row(p, "../") for p in ps)
    fx = ps[0]["fixtures"]
    fxr = "".join(f'<div class="fxrow"><div class="fxd">{esc(d)}</div><div class="fxo">{esc(o)} '
                  f'<span class="ha">{"H" if h=="H" else "A"}</span></div><div class="fxc">{esc(c)}</div></div>'
                  for d,o,h,c in fx)
    body = f'''
    <a class="crumb" href="../clubs.html">← All clubs</a>
    <div class="pagehead"><h1>{esc(cname)}</h1><p>{esc(ps[0]["league"]) + " · " if ps[0]["league"] not in ("","—") else ""}{len(ps)} Irish player{"s" if len(ps)!=1 else ""} tracked</p></div>
    <div class="sec"><h2>Irish players</h2></div>
    <div class="tiergroup">{rows}</div>
    <div class="sec"><h2>Upcoming fixtures</h2></div>
    <div class="fxlist">{fxr or '<div class="emptystate" style="display:block">No fixtures listed.</div>'}</div>
    '''
    return shell(f"{cname} — Irish players — FOOTBALLERS",
                 f"Irish professionals at {cname}, plus upcoming fixtures.", "../", "clubs.html", body,
                 canonical=f"club/{club_slug(cname)}.html")

# ================= IRELAND =================
def build_ireland():
    tabs, panels = "", ""
    for i,(lvl,info) in enumerate(IRELAND.items()):
        squad = [p for p in PLAYERS if (lvl=="Senior" and p["intl_senior"]) or
                 (lvl!="Senior" and any(y[0]==lvl for y in p["intl_youth"]))]
        cards = ""
        for p in squad:
            if lvl=="Senior":
                c = p["intl_senior"]["caps"]
                meta = f'{c} caps · {p["intl_senior"]["goals"]} goals' if c is not None else "Capped"
            else:
                y = next(y for y in p["intl_youth"] if y[0]==lvl)
                meta = f'{y[1]} caps · {y[2]} goals' if y[1] is not None else "In the squad"
            cards += (f'<a class="squadcard" href="{plink(p)}"><div class="pos">{p["pos"]}</div>'
                      f'<div class="who">{esc(p["n"])}</div><div class="cl">{esc(p["club"])}</div>'
                      f'<div class="caps">{meta}</div></a>')
        fxr = "".join(f'<div class="fxrow"><div class="fxd">{esc(d)}</div>'
                      f'<div class="fxo">{esc(o)} <span class="ha">{h}</span></div><div class="fxc">{esc(c)}</div></div>'
                      for d,o,h,c in info["fixtures"])
        rsr = "".join(f'<div class="fxrow"><div class="fxd">{esc(d)}</div><div class="fxo">{esc(o)}</div>'
                      f'<div class="fxs">{esc(s)}</div><div class="fxc">{esc(c)}</div></div>'
                      for d,o,s,c in info["results"])
        tabs += f'<button class="tab {"on" if i==0 else ""}" data-t="{lvl}">{esc(lvl)}</button>'
        panels += f'''<div class="tabpanel {"on" if i==0 else ""}" data-t="{lvl}">
          <div class="sec"><h2>Fixtures</h2></div><div class="fxlist">{fxr or '<div class="emptystate" style="display:block">None listed.</div>'}</div>
          <div class="sec"><h2>Recent results</h2></div><div class="fxlist">{rsr or '<div class="emptystate" style="display:block">None listed.</div>'}</div>
          <div class="sec"><h2>Tracked players at this level</h2></div>
          <div class="squadgrid">{cards or '<div class="emptystate" style="display:block">No tracked players.</div>'}</div>
        </div>'''
    body = f'''
    <div class="pagehead"><h1>Republic of <i>Ireland</i></h1><p>Fixtures, results and tracked players from senior down through the underage sides.</p></div>
    <div class="tabbar">{tabs}</div>
    {panels}
    <script>
    var tabs=[].slice.call(document.querySelectorAll('.tab')),panels=[].slice.call(document.querySelectorAll('.tabpanel'));
    tabs.forEach(function(t){{t.onclick=function(){{
      tabs.forEach(function(x){{x.classList.toggle('on',x===t)}});
      panels.forEach(function(p){{p.classList.toggle('on',p.dataset.t===t.dataset.t)}});
    }}}});
    </script>'''
    return shell("Republic of Ireland — FOOTBALLERS",
                 "Ireland fixtures, results and tracked players from senior to underage level.","", "ireland.html", body, canonical="ireland.html")

# ================= FIXTURES =================
def build_fixtures():
    rows = []
    for p in PLAYERS:
        for d,o,h,c in p["fixtures"]:
            rows.append((d, p, o, h, c))
    order = {}
    for d,p,o,h,c in rows: order.setdefault(d, []).append((p,o,h,c))
    out = ""
    for d in sorted(order, key=lambda x:(x.split()[1], int(x.split()[0]))):
        items = "".join(f'<a class="fxrow lnk" href="{plink(p)}"><div class="fxo">{esc(p["n"])} '
                        f'<span class="cl">{esc(p["club"])}</span></div>'
                        f'<div class="fxv">v {esc(o)} <span class="ha">{h}</span></div>'
                        f'<div class="fxc">{esc(c)}</div></a>' for p,o,h,c in order[d])
        out += f'<div class="tiergroup"><h4><span>{esc(d)}</span><span>{len(order[d])} match{"es" if len(order[d])!=1 else ""}</span></h4>{items}</div>'
    body = (f'<div class="pagehead"><h1>Fixtures</h1><p>Every upcoming game a tracked Irish player could feature in.</p></div>{out}')
    return shell("Fixtures — FOOTBALLERS","Every upcoming game a tracked Irish player could feature in.","", "fixtures.html", body, canonical="fixtures.html")

# ================= MILESTONES =================
def milestone_items():
    out = []
    for p in PLAYERS:
        c = p["career"]["ap"]
        for mark in (50,100,150,200,250,300,400):
            if 0 < mark - c <= 8:
                out.append(dict(p=p, tag="Club appearances", text=f'{mark-c} away from {mark} career appearances'))
                break
        if p["intl_senior"] and p["intl_senior"]["caps"] is not None:
            caps = p["intl_senior"]["caps"]
            for mark in (10,25,50,75,100):
                if 0 < mark - caps <= 4:
                    out.append(dict(p=p, tag="Caps", text=f'{mark-caps} cap{"s" if mark-caps!=1 else ""} from {mark} for Ireland'))
                    break
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
            f'<div class="msgrid">{cards or "<div class=\'emptystate\' style=\'display:block\'>Nothing close right now.</div>"}</div>')
    return shell("Milestones — FOOTBALLERS","Irish players approaching career and international milestones.","", "milestones.html", body, canonical="milestones.html")

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
    return shell("Compare players — FOOTBALLERS","Compare any two Irish professionals side by side.","", "compare.html", body, canonical="compare.html")

# ================= PLAYER =================
def build_player(p):
    s, c = p["season"], p["career"]
    badge = "League of Ireland" if p["tier"]=="loi" else ("Abroad · top flight" if p["tier"]=="abroad-top" else "Abroad")

    fxr = "".join(f'<div class="fxrow"><div class="fxd">{esc(d)}</div><div class="fxo">{esc(o)} '
                  f'<span class="ha">{h}</span></div><div class="fxc">{esc(cp)}</div></div>'
                  for d,o,h,cp in p["fixtures"])
    rsr = ""
    for d,o,sc,cp,mins,g,a in p["results"]:
        ev = ("⚽"*g) + ("🅰"*a)
        rsr += (f'<div class="fxrow"><div class="fxd">{esc(d)}</div><div class="fxo">{esc(o)}</div>'
                f'<div class="fxs">{esc(sc)}</div><div class="fxev">{ev}</div>'
                f'<div class="fxm">{mins}\'</div></div>')

    # international
    intl = ""
    if p["intl_senior"] or p["intl_youth"]:
        blocks = ""
        if p["intl_senior"]:
            i = p["intl_senior"]
            lv = IRELAND["Senior"]
            fx = "".join(f'<div class="fxrow"><div class="fxd">{esc(d)}</div><div class="fxo">{esc(o)} '
                         f'<span class="ha">{h}</span></div><div class="fxc">{esc(cp)}</div></div>'
                         for d,o,h,cp in lv["fixtures"])
            rs = "".join(f'<div class="fxrow"><div class="fxd">{esc(d)}</div><div class="fxo">{esc(o)}</div>'
                         f'<div class="fxs">{esc(sc)}</div><div class="fxc">{esc(cp)}</div></div>'
                         for d,o,sc,cp in lv["results"])
            blocks += (f'<div class="intlblock"><div class="ilvl">Senior <span>{i["caps"]} caps · {i["goals"]} goals · debut {i["debut"]}</span></div>'
                       f'<div class="isub">Upcoming</div><div class="fxlist">{fx}</div>'
                       f'<div class="isub">Recent results</div><div class="fxlist">{rs}</div></div>')
        for lvl, caps, goals, since in p["intl_youth"]:
            txt = f'{caps} caps · {goals} goals · from {since}' if caps is not None else "Called up at this level"
            blocks += (f'<div class="intlblock"><div class="ilvl">{esc(lvl)} <span>{txt}</span></div></div>')
        intl = f'<div class="sec"><h2>International</h2><a class="more" href="../ireland.html">Ireland hub →</a></div>{blocks}'

    # eligibility
    elig = ""
    for country, status in p["eligible"]:
        cls = {"tied":"elig tied","blocked":"elig blocked","eligible":"elig open"}[status]
        note = {"tied":"Committed","blocked":"No longer available","eligible":"Still eligible"}[status]
        elig += f'<div class="{cls}"><span class="ec">{esc(country)}</span><span class="en">{note}</span></div>'
    tie_note = ("Cap-tied to Ireland — a competitive senior appearance means the other associations below are closed off."
                if p["cap_status"]=="senior_comp" else
                "Youth caps and senior friendlies don't cap-tie a player, so a switch is still possible under FIFA rules."
                if p["cap_status"] in ("youth","senior_friendly") else
                "Uncapped at any level — free to commit to any association they qualify for.")

    trans = "".join(f'<div class="trow"><div class="ty">{esc(y)}</div><div class="tf">{esc(f)} → <b>{esc(t)}</b></div>'
                    f'<div class="tfee">{esc(fee)}</div></div>' for y,f,t,fee in p["transfers"])

    body = f'''
    <a class="crumb" href="../players.html">← All players</a>
    <div class="pdhead">
      <div class="pdid">
        {avatar(p, "../")}
        <div>
        <div class="pdname">{esc(p["n"])}</div>
        <div class="pdmeta">{" · ".join(filter(None,[
            f'<a href="{clink(p["club"],"../")}">{esc(p["club"])}</a>',
            esc(p["league"]) if p["league"] not in ("","—") else "",
            p["pos"] if p["pos"] not in ("","—") else "",
            str(p["age"]) if p["age"] else "",
            (f'<b>{p["intl_senior"]["caps"]} caps</b>' if p["intl_senior"]["caps"] is not None
             else '<b>Senior international</b>') if p["intl_senior"] else ""]))}</div>
        {f'<div class="pdborn">Born {esc(p["born"])}' + (" · " + esc(p["foot"]) + " footed" if p["foot"] else "") + "</div>" if p["born"] else ""}
        {f'<div class="pcredit">Photo: {esc(p["photo_credit"])}</div>' if p.get("photo_credit") else ""}
        </div>
      </div>
      <div class="pdactions">
        <button class="starbtn" data-fav="{p["slug"]}" aria-pressed="false">★ <span>Follow</span></button>
        <div class="pdbadge">{badge}</div>
      </div>
    </div>

    <div class="sec"><h2>Season {esc(SEASON)}</h2></div>
    <div class="pdstats">
      <div class="pds"><div class="n">{s["ap"]}</div><div class="l">Apps</div></div>
      <div class="pds"><div class="n">{s["starts"]}</div><div class="l">Starts</div></div>
      <div class="pds"><div class="n">{s["g"]}</div><div class="l">Goals</div></div>
      <div class="pds"><div class="n">{s["a"]}</div><div class="l">Assists</div></div>
      <div class="pds"><div class="n">{s["mins"]}</div><div class="l">Minutes</div></div>
      <div class="pds"><div class="n">{s["yellow"]}/{s["red"]}</div><div class="l">Cards</div></div>
      <div class="pds"><div class="n">{c["ap"]}</div><div class="l">Career apps</div></div>
      <div class="pds"><div class="n">{c["g"]}</div><div class="l">Career goals</div></div>
    </div>

    <div class="sec"><h2>Upcoming fixtures</h2><span class="more" style="border:0">{esc(p["club"])}</span></div>
    <div class="fxlist">{fxr or '<div class="emptystate" style="display:block">No fixtures listed.</div>'}</div>

    <div class="sec"><h2>This season's results</h2></div>
    <div class="fxlist">{rsr or '<div class="emptystate" style="display:block">No appearances yet.</div>'}</div>

    {intl}

    <div class="sec"><h2>Eligibility</h2></div>
    <div class="eliglist">{elig}</div>
    <p class="eligNote">{tie_note}</p>

    {f'<div class="sec"><h2>Transfers</h2></div><div class="tlist">{trans}</div>' if trans else ''}
    '''
    return shell(f"{p['n']} — FOOTBALLERS",
                 f"{p['n']} ({p['club']}, {p['league']}) — season stats, fixtures, results and international record.",
                 "../", "", body, canonical=f"player/{p['slug']}.html")



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
    return shell("Newsletter — FOOTBALLERS",
                 "Two emails a week on every Irish professional footballer — Monday round-up and Friday preview.",
                 "", "newsletter.html", body, canonical="newsletter.html")


def build_alerts():
    form_open = f'<form class="nlform" action="{NEWSLETTER_ACTION}" method="post" target="_blank">' if NEWSLETTER_ACTION \
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
    return shell("Player alerts — FOOTBALLERS",
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
    return shell("Page not found — FOOTBALLERS", "That page doesn't exist on Footballers.", "/", "", body)

def build_sitemap():
    urls = ["", "players.html", "abroad.html", "league-of-ireland.html", "clubs.html",
            "ireland.html", "fixtures.html", "milestones.html", "compare.html", "newsletter.html", "alerts.html"]
    urls += [f"club/{club_slug(c)}.html" for c in sorted(set(p["club"] for p in PLAYERS))]
    urls += [f"player/{p['slug']}.html" for p in PLAYERS]
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

open(f"{OUT}/index.html","w").write(build_index())
open(f"{OUT}/players.html","w").write(build_list("players.html","All players","Every professional Irish player on the books — abroad and at home.",PLAYERS))
open(f"{OUT}/abroad.html","w").write(build_list("abroad.html","Abroad","Irish players at clubs outside Ireland, top flight to smaller leagues.",[p for p in PLAYERS if p["tier"].startswith("abroad")]))
open(f"{OUT}/league-of-ireland.html","w").write(build_list("league-of-ireland.html","League of Ireland","Every Irish pro playing their football at home.",[p for p in PLAYERS if p["tier"]=="loi"]))
open(f"{OUT}/clubs.html","w").write(build_clubs_index())
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

print(f"Built {9 + len(clubs) + len(PLAYERS)} pages ({len(clubs)} clubs, {len(PLAYERS)} players)")
