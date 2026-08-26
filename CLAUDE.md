# irishfball / footballers.ie — CLAUDE.md

Read fully before touching anything. Supersedes HANDOFF.md (which is from fb72; this is current as of fb80, 26 Aug 2026).

## What this is
Site tracking every Irish professional footballer, home and abroad. 487 players. Owned by Matchweek (the owner's parent company).
- Live: irishfball.vercel.app → moving to **footballers.ie** (bought 26 Aug, DNS pointed at Vercel, may still be propagating)
- Repo: JustPatrickG/footballers.ie (public). Brand is still "Irish Fball" in copy/wordmark — rename to footballers.ie is a pending task.

## Who you're working with
Site owner. Direct, informal. Wants the answer first, then the minimum words needed.
**Bullets over paragraphs. Simple commands. No preamble, no lectures.** He'll push back hard when something's wrong and is usually right.
He can't see your reasoning — if something's broken, say what's broken and what you're doing, in one line.

## Workflow (Claude Code)
You're in the project folder. Make the edit, run the build, show him the diff summary, commit, push.
- Build check: `python3 build/gen.py` — must finish with "assets copied". Run it before every commit.
- JS check: `node --check build/app.js`
- Commit message: short, plain ("Fix live scores", "News layout").
- Always `git pull --rebase` before push — GitHub Actions and the admin both commit to main constantly.
- **NEVER edit or commit `data/api/*`, `scraper/*`, `img/`, `live.json`** — the scrapers own those. Editing them by hand gets overwritten and can clobber real data.
- `data/manual/*.csv` is the human layer — fine to edit, survives scrapes.
- Zips (`fbNN.zip`) are the OLD workflow from chat. Dead now. Don't make them.

## Architecture
Static site. No framework. Python (stdlib only — keep it that way, Vercel's build image has no pip) generates HTML.
```
build/gen.py        generator. reads CSVs → writes site/ (OUT is absolute, HERE/../site — don't make it relative)
build/style.css     inlined into every page
build/app.js        inlined into every page
build/admin.html    the CMS, served at /build/admin.html (double-click logo → login)
build/fetch_roster.py, fetch_api_football.py   used by workflows
api/report.js       Vercel fn: report button → GitHub Issues
api/subscribe.js    Vercel fn: signups → subscribers.csv in PRIVATE repo (SUBSCRIBERS_REPO). Never weaken that.
vercel.json         build: python3 build/gen.py, output: site/. Rewrites /live.json → raw.githubusercontent (see Live scores)
.github/workflows/  live (*/5), matchday (*/10), refresh (hourly), sync-roster (weekly), build (on push — build check only, no commit)
data/api/           SCRAPER WRITES. players.csv, matches.csv, tm.csv (Transfermarkt bio, monthly)
data/manual/        players.csv, articles.csv, ireland.csv, clubs.csv, accounts.csv, news.csv
scraper/            separate agent maintains this. players_list.csv is the roster.
site/               build output, gitignored
```

### Data rules
- Merge priority: scraper/players_list.csv < data/api/players.csv < data/manual/players.csv. Scraper is authoritative for club/league/age/foot/avg_rating unless manual row has `locked=yes`. `on_loan_at` overrides displayed club. `tm_club` (Transfermarkt) beats the stats feed's club but never beats a manual club.
- Blank numeric = unknown, render `—`, never 0. `source=none` = no stats page exists; `has_data()` hides the grid.
- `season` is a string ("2026/27", "2026"); never hardcode a year.
- `club_slug()` must strip everything non-alphanumeric ("Highland / Lowland" once broke the build with a slash).
- Adding a player the scraper doesn't know: append a row to data/manual/players.csv with slug,name,club,tier,pos. Manual-only rows show.
- articles.csv: `slug,date,tag,headline,standfirst,body,author,image,player_slug,expires`. Tag NOW/LIVE/BREAKING pins to the top of the homepage carousel. `expires` (YYYY-MM-DDTHH:MM) drops it from the carousel only — stays on news.html.

## Live scores (fixed in fb80 — verify it works tonight)
- `live.yml` runs `scraper/irish_scraper.py live` every 5 min → writes `live.json` at repo root → commits.
- **Bug that existed until fb80:** live.json was never in site/, so /live.json 404'd and scores never moved. Now: vercel.json rewrites /live.json to raw.githubusercontent.com/.../main/live.json (no rebuild needed), and gen.py also copies it into site/ as fallback.
- app.js fetches /live.json every 60s and patches FB_MATCHES by `id`. The id must exactly equal gen.py's `match_id()` (`YYYY-MM-DD-home-slug-v-away-slug`) or updates are silently ignored.
- LIVE_WINDOW_H = 3 in the scraper — only matches within ±3h of now are in live.json.

## Fixtures page (fb78/79)
FotMob-style, rendered client-side by `renderAll()` in app.js from `window.FB_MATCHES` (built by `match_payload()` in gen.py).
- Sticky date strip (14 days back, 21 forward), one day at a time, grouped by competition. Opens on today.
- Filter pills Abroad (default) / League of Ireland / All.
- `m.loi` flag: competition matches LOI_COMPS, or >50% of involved players have tier `loi`. LOI matches render a 6-face pile + "N Irish players" instead of chips (they'd have 20 chips otherwise). Same on homepage match centre.
- Empty day shows a "next match" card that jumps to that day.

## News (fb79)
- `build_news()`: lead story (image left / text right), next 3 as cards, rest as a list, sidebar with Writers + newsletter signup.
- `art_visual()`: article image → else player photo cutout on gradient with tag badge → else branded placeholder. Never an empty box.
- Bylines are `<span class="au" data-href>` NOT nested `<a>` (nested anchors broke the whole page once). A click handler routes them.
- Writer pages at /author/<slug>.html. Tag filter pills. "Want to write for us" → business@matchweek.ie.
- Admin has a `journalist` role (own articles only) — the owner has journalists lined up.

## Theme
`[data-theme="pitch"]` on <html> is a second skin (FotMob-ish green). Toggle `#themetog` in nav, saved in localStorage `fb_theme`, no-flash script in <head>. Original theme is the default; don't break either.

## Automation notes
- All 5 workflows run on schedule (85 runs on 26 Aug, mostly green). Matchday refresh fails ~1 in 5 overnight — not yet diagnosed.
- Scrapers have `concurrency: data-write` so they queue.
- Public repo → Actions minutes are free.

## Outstanding
1. Rename copy/wordmark/SITE_URL from "Irish Fball" → footballers.ie once DNS is green
2. Two articles have placeholder bylines ("me myself and i", "journalist name") → they generate author pages. One article's slug is literally `a`.
3. Admin password on business@matchweek.ie still the default
4. evan-ferguson `on_loan_at=Roma` is a leftover test value
5. ireland.csv holds last season's results
6. Five players have no club: alex-gilbert, darragh-lenihan, jay-oshea, scott-hogan, will-keane
7. Pike Rovers has no map coords
8. Reddit feedback: Osagie, Wade (Chelsea U21), Omochere, Quigley, Sanyaolu (Bristol Rovers) were missing — rows to add to data/manual/players.csv; check they're in.
9. Diagnose the red matchday refresh runs

## Things that bite
- Backslashes inside f-string expressions break Python < 3.12. Vercel and Actions now both use 3.12, but keep them out anyway.
- `.gitignore` once contained `.github/` and silently hid every workflow from GitHub. Never add it back.
- Mobile CSS once hid the rating column for months — check computed styles before assuming a data bug.
- Leaflet map on where-are-the-irish.html degrades to country pills if the CDN fails; that's intentional.
