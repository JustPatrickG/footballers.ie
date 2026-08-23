# footballers.ie

Static site tracking every Irish professional footballer — abroad, senior
international and League of Ireland.

## How the data works

Two layers. **Your edits always win.**

```
data/
  api/       ← written by the fetchers. Never hand-edit — it gets overwritten.
  manual/    ← yours. Nothing automated ever touches this.
```

At build time, for every field: **if the manual cell has something in it, it wins.
If it's blank, the API value is used.** So you can correct one wrong position
without freezing everything else about that player.

Set `locked` to `yes` on a player to ignore the API for them entirely.

## Adding players

Open `build/admin.html` in your browser (double-click it — it runs locally,
nothing is uploaded anywhere).

1. **Bulk add** — paste your list, one per line: `Name, Club`
   or `Name, Club, POS, League`. Slugs are generated, duplicates skipped.
2. **Edit** — fix anything in the table.
3. **Download players.csv** → drop it into `data/manual/` → commit → push.

To carry on from what's already there, use *Load existing CSV* and pick
`data/manual/players.csv` first.

## Fetching data

```bash
export API_FOOTBALL_KEY=xxxxxxxx        # free key from api-football.com
python3 build/fetch_api_football.py     # season stats for tracked players
python3 build/fetch_roster.py           # roster/club refresh from Wikidata
```

Both write only to `data/api/`. The free API tier is 100 requests/day; the
fetcher caps itself at 60 per run and prints usage as it goes.

In CI, add `API_FOOTBALL_KEY` as a repository secret
(Settings → Secrets and variables → Actions).

## Column formats

- `youth` — `U21:12:6:2019; U19:5:3:2018` (level:caps:goals:from-year)
- `eligible` — `Republic of Ireland:tied; England:blocked`
  - `tied` = committed · `eligible` = can still switch · `blocked` = unavailable
- `cap_status` — `senior_comp` (cap-tied), `senior_friendly`, `youth`, `uncapped`
- `tier` — `abroad-top`, `abroad-lower`, `loi`

Only a competitive senior appearance cap-ties a player under FIFA rules —
youth caps and friendlies don't.

## Before going fully live

In `build/gen.py`:
- `SAMPLE_DATA = False` once every figure is real (removes the footer notice)
- `NEWSLETTER_ACTION` — paste your provider's form URL
- `MATCHWEEK` — update weekly

## Local build

```bash
cd build && python3 gen.py     # outputs to build/site/
```

Pushing to `main` rebuilds and deploys automatically.
