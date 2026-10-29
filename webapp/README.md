# Mahjong Score Analyser — webapp

FastAPI backend that scrapes https://scores.vicriichi.org (currently just the
Box Hill event), stores results in SQLite, and serves a small htmx frontend
to look up a player's best-10 window, ranking distribution, and match
history.

The scraper is a plain HTTP request (`httpx`) — no headless browser. The
original notebook used Selenium because the old site layout seemed to need
JS execution; testing against the current site showed a plain request
returns identical game data to a fully-rendered browser (only the date
string's locale formatting differs, which doesn't affect the parsed values).

## Running locally (without Docker)

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate        # or source .venv/bin/activate on Linux/macOS
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Then open http://localhost:8000.

## Running with Docker (e.g. on OMV)

```bash
docker compose up --build -d
```

Data persists in `./data/mahjong.db` on the host via the bind mount in
`docker-compose.yml`.

## Scraping

- **Scheduled**: each venue in `app/venues.py` has its own day-of-week and
  time window (Box Hill only plays Saturday 12:30–17:30), and the scheduler
  only scrapes during that window, every `interval_minutes`.
- **Manual**: the "Scrape now" button on the page (or `POST /api/scrape/{venue}`)
  triggers the same scrape function immediately. Scheduled and manual scrapes
  share a lock per venue so two scrapes never run at once.

## Season labels

Each scrape auto-tags the current season's games (via GP numbers from the
site's scoreboard). Past seasons (1-3 as of writing) were backfilled once by
hand — see `backend/scripts/backfill_seasons.py` for how, and to extend it
if more seasons close out later (the site has no URL/API for past seasons,
so their GP numbers have to be read off the site's dropdown manually). Run
it once per deployment if you want historical season labels:

```bash
docker compose exec mahjong-app python scripts/backfill_seasons.py
```

## Adding a new venue

Add an entry to `VENUES` in `app/venues.py` with its own `url`, `day`,
`start`/`end` window, and the rest (scraping, storage, API, UI) picks it up
automatically — the frontend's venue dropdown is driven by that same config.
