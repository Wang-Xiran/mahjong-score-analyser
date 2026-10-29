import logging
import re
import threading
from typing import Callable

import httpx
from bs4 import BeautifulSoup
from fastapi import BackgroundTasks
from sqlalchemy.orm import Session

from . import progress
from .db import SessionLocal
from .models import Game, PlayerGamesPlayed, PlayerResult
from .venues import VENUES

logger = logging.getLogger(__name__)

# Guards against the scheduler and someone mashing the manual-scrape button
# both scraping the same venue at once.
_scrape_locks_guard = threading.Lock()
_scraping_venues: set[str] = set()

_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# (percent, message) -> None, called as the scrape moves through real phases.
ProgressFn = Callable[[int, str], None]


def _noop_progress(percent: int, message: str) -> None:
    pass


class ScrapeInProgress(Exception):
    pass


def fetch_html(url: str) -> str:
    """The site fully server-renders the game list, so a plain request gets
    everything — no headless browser needed. (Confirmed by diffing a plain
    HTTP fetch against a Selenium/real-browser fetch: identical game data,
    the only difference being the date string's locale formatting, which a
    client-side hydration pass reformats but doesn't change the content of.)
    The response is chunked with no Content-Length, so there's no byte count
    to report real download progress against — this phase can only be
    reported as a single before/after step."""
    response = httpx.get(url, headers=_HEADERS, timeout=20.0, follow_redirects=True)
    response.raise_for_status()
    return response.text


def parse_games(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    game_divs = soup.find_all("div", class_="game-display")
    game_divs = game_divs[::-1]  # site lists newest first; we want oldest first

    parsed = []
    for game in game_divs:
        header_spans = game.find("div", class_="game-display-header").find_all("span")
        game_number = header_spans[0].text
        game_date = header_spans[1].text

        players = []
        for player in game.find_all("div", class_="game-player-display"):
            cells = player.find_all("div")
            players.append(
                {
                    "name": cells[0].text,
                    "points": int(cells[1].text),
                    "score": float(cells[2].text),
                }
            )

        parsed.append({"game_number": game_number, "date": game_date, "players": players})

    return parsed


def parse_season_label(html: str) -> str | None:
    """The "Showing: Season N (Current)" control above the scoreboard. We
    strip the "(Current)" suffix since that's just this page's own framing,
    not part of the season's identity."""
    soup = BeautifulSoup(html, "lxml")
    el = soup.find(attrs={"aria-labelledby": "period"})
    if el is None:
        return None
    text = el.get_text().strip()
    return re.sub(r"\s*\(current\)\s*$", "", text, flags=re.IGNORECASE).strip() or None


def compute_current_season_game_keys(
    parsed_games: list[dict], games_played: dict[str, int]
) -> set[tuple[str, str]]:
    """Which games belong to the season the scoreboard's GP numbers describe.

    GP is per player (how many games *that player* has played this season),
    not a single season-wide count, so there's no one number to slice the
    game list by. But since the current season is always the most recent
    one, a player's last GP games are *exactly* their season games — so the
    union of "each scoreboard player's last GP games" is exactly the set of
    games that happened this season (every game has 4 participants, and the
    game is within each of their own last-GP window if and only if it's a
    current-season game).
    """
    games_by_player: dict[str, list[tuple[str, str]]] = {}
    for g in parsed_games:  # oldest-first, so appending keeps chronological order
        key = (g["game_number"], g["date"])
        for player in g["players"]:
            games_by_player.setdefault(player["name"], []).append(key)

    season_keys: set[tuple[str, str]] = set()
    for name, gp in games_played.items():
        season_keys.update(games_by_player.get(name, [])[-gp:] if gp > 0 else [])

    return season_keys


def parse_scoreboard(html: str) -> dict[str, int]:
    """The site's "Scoreboard" table (Rank/Name/Score/GP) for the current
    season — GP is how many games count toward a player's best-10 window on
    the site itself, distinct from their all-time game count."""
    soup = BeautifulSoup(html, "lxml")

    scoreboard_table = None
    for table in soup.find_all("table"):
        head = table.find("thead")
        if head and "GP" in head.get_text():
            scoreboard_table = table
            break
    if scoreboard_table is None or scoreboard_table.find("tbody") is None:
        return {}

    games_played = {}
    for row in scoreboard_table.find("tbody").find_all("tr"):
        cells = row.find_all(["th", "td"])
        if len(cells) < 4:
            continue
        name = cells[1].text
        games_played[name] = int(cells[3].text)

    return games_played


def save_games(
    venue: str,
    parsed_games: list[dict],
    db: Session,
    season_label: str | None = None,
    current_season_keys: set[tuple[str, str]] = frozenset(),
    on_progress: ProgressFn = _noop_progress,
) -> int:
    # Upsert rather than wiping and reinserting: the site's Game Logs
    # currently shows full cross-season history, but if it ever starts
    # scoping that down to just the current season, a destructive replace
    # would permanently delete every older game from our own record.
    # Existing games are only updated in place (never removed), so old
    # seasons stay intact locally even if the site stops showing them.
    #
    # Matched by (game_number, date), not game_number alone: numbering has
    # been continuous across every season seen so far, but if a future
    # season ever restarts it at #1, matching on game_number alone would
    # collide with an old season's "Game #1" and overwrite its history.
    existing_games = {
        (g.game_number, g.date): g for g in db.query(Game).filter(Game.venue == venue).all()
    }
    next_order_index = (
        db.query(Game).filter(Game.venue == venue).count()
    )  # new games are appended after whatever's already stored

    total = len(parsed_games)
    # Saving is the one phase with a known total to report real fractional
    # progress against, spread across the 50-90% band of the overall scrape.
    for i, g in enumerate(parsed_games):
        key = (g["game_number"], g["date"])
        game = existing_games.get(key)
        this_games_season = season_label if key in current_season_keys else None

        if game is None:
            game = Game(
                venue=venue,
                game_number=g["game_number"],
                date=g["date"],
                order_index=next_order_index,
                season=this_games_season,
            )
            next_order_index += 1
            db.add(game)
            db.flush()  # need game.id before attaching results
            existing_games[key] = game
        else:
            if this_games_season is not None:
                game.season = this_games_season
            # Replace this one game's results (e.g. a site-side correction)
            # without touching any other game's rows.
            db.query(PlayerResult).filter(PlayerResult.game_id == game.id).delete(
                synchronize_session=False
            )

        for placement, player in enumerate(g["players"], start=1):
            db.add(
                PlayerResult(
                    game_id=game.id,
                    placement=placement,
                    name=player["name"],
                    points=player["points"],
                    score=player["score"],
                )
            )

        if total:
            percent = 50 + int(40 * (i + 1) / total)
            on_progress(percent, f"Saving game {i + 1}/{total}…")

    db.commit()
    return len(parsed_games)


def save_scoreboard(venue: str, games_played: dict[str, int], db: Session) -> None:
    db.query(PlayerGamesPlayed).filter(PlayerGamesPlayed.venue == venue).delete(synchronize_session=False)
    for name, gp in games_played.items():
        db.add(PlayerGamesPlayed(venue=venue, name=name, games_played=gp))
    db.commit()


def backfill_season(venue: str, season_label: str, games_played: dict[str, int], db: Session) -> int:
    """One-off backfill for a past season, using the same reasoning as
    compute_current_season_game_keys but against already-stored games
    instead of a fresh scrape: a player's last `gp` *still-untagged* games
    are exactly their games from this season.

    Must be called for seasons in reverse-chronological order (most recent
    untagged season first) so each call's "untagged" pool is the right one —
    otherwise an older season's games would already have been claimed by a
    newer season's GP window. Returns how many games got tagged.
    """
    games = (
        db.query(Game)
        .filter(Game.venue == venue, Game.season.is_(None))
        .order_by(Game.order_index)
        .all()
    )

    games_by_player: dict[str, list[Game]] = {}
    for game in games:
        for result in game.results:
            games_by_player.setdefault(result.name, []).append(game)

    to_tag: set[int] = set()
    for name, gp in games_played.items():
        if gp > 0:
            to_tag.update(g.id for g in games_by_player.get(name, [])[-gp:])

    for game in games:
        if game.id in to_tag:
            game.season = season_label

    db.commit()
    return len(to_tag)


def scrape_venue(venue: str, on_progress: ProgressFn = _noop_progress) -> int:
    config = VENUES[venue]

    on_progress(5, "Connecting to site…")
    html = fetch_html(config["url"])

    on_progress(45, "Parsing games…")
    parsed_games = parse_games(html)
    games_played = parse_scoreboard(html)
    season_label = parse_season_label(html)
    current_season_keys = compute_current_season_game_keys(parsed_games, games_played)

    db = SessionLocal()
    try:
        count = save_games(
            venue,
            parsed_games,
            db,
            season_label=season_label,
            current_season_keys=current_season_keys,
            on_progress=on_progress,
        )
        on_progress(92, "Saving scoreboard…")
        save_scoreboard(venue, games_played, db)
        return count
    finally:
        db.close()


def scrape_venue_exclusive(venue: str, on_progress: ProgressFn = _noop_progress) -> int:
    """Like scrape_venue, but raises ScrapeInProgress instead of overlapping
    with another scrape (scheduled or manual) already running for the venue."""
    with _scrape_locks_guard:
        if venue in _scraping_venues:
            raise ScrapeInProgress(venue)
        _scraping_venues.add(venue)

    try:
        return scrape_venue(venue, on_progress=on_progress)
    finally:
        with _scrape_locks_guard:
            _scraping_venues.discard(venue)


def start_scrape_job(venue: str, background_tasks: BackgroundTasks) -> str:
    """Reserves the venue's scrape lock synchronously (so a second click
    while one is running fails immediately), then runs the actual scrape
    after the response is sent — the UI gets a job id to poll real progress
    against instead of blocking on the whole request."""
    with _scrape_locks_guard:
        if venue in _scraping_venues:
            raise ScrapeInProgress(venue)
        _scraping_venues.add(venue)

    job_id = progress.create_job(venue)
    background_tasks.add_task(_run_scrape_job, venue, job_id)
    return job_id


def _run_scrape_job(venue: str, job_id: str) -> None:
    def on_progress(percent: int, message: str) -> None:
        progress.update_job(job_id, percent, message)

    try:
        count = scrape_venue(venue, on_progress=on_progress)
        progress.finish_job(job_id, f"Scraped {count} games for {venue}.")
    except Exception:
        logger.exception("Scrape failed for %s", venue)
        progress.fail_job(job_id, "Scrape failed — check the server logs.")
    finally:
        with _scrape_locks_guard:
            _scraping_venues.discard(venue)
