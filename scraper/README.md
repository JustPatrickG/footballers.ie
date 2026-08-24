# footballers.ie scraper

Player-centric FotMob scraper. Outputs:
- data/api/matches.csv, data/api/players.csv
- data/manual/results.csv, data/manual/fixtures.csv
Never touches data/manual/players.csv.

## First run
    python3 scraper/irish_scraper.py resolve
Builds scraper/fotmob_ids.csv (slug -> FotMob ID). Check any rows
flagged "CHECK" or "not found" — fix by pasting the ID from the
player's fotmob.com URL. This only needs doing once.

## Every run after
    python3 scraper/irish_scraper.py scrape

## Discover LOI players
    python3 scraper/irish_scraper.py discover-loi
Scans every LOI Premier + First Division squad on FotMob, adds all Irish
players not already in your list (with their IDs, so no resolve
needed). ireland_level is left blank for new players.

## Notes
- players_list.csv in this folder is the source of truth. Add/remove
  players there, then run resolve again to pick up new ones.
- --only slug1,slug2 to test on a few players
- --debug dumps raw FotMob JSON to scraper/debug/ (send these if
  something extracts wrong)
- born + c_assists are left blank (FotMob doesn't have them) — blanks
  are respected per spec, your manual values win.
