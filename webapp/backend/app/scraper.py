import logging
import re
import threading
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup
from fastapi import BackgroundTasks
from sqlalchemy.orm import Session

from . import progress
from .db import SessionLocal
from .models import Game, PlayerGamesPlayed, PlayerResult, VenueState
from .venues import VENUES

logger = logging.getLogger(__name__)

# Guards against the scheduler and someone mashing the manual-scrape button
# both scraping the same venue at once.
_scrape_locks_guard = threading.Lock()
_scraping_venues: set[str] = set()

_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# All game times on the site are for this single venue's own timezone -
# used to turn the embedded payload's UTC timestamps (see
# parse_games_from_payload) into the same "M/D/YYYY" local-date strings the
# rendered page (and so the rest of this module, and everything already
# stored) uses.
_VENUE_TZ = ZoneInfo("Australia/Melbourne")

# The site's "Game Logs" section embeds the FULL game history (every game
# since season 1) as part of its React hydration payload - escaped JSON
# sitting inside a <script> tag - regardless of how many are actually
# rendered as visible "game-display" cards (see parse_games_from_payload).
# These match that escaped-JSON shape directly rather than parsing it as
# real JSON, since it's nested one level inside a larger non-JSON flight
# payload string - not worth a full parser for two fields.
_PAYLOAD_PLAYER_NAME_RE = re.compile(r'\[(\d+),\{\\"id\\":\d+,\\"name\\":\\"([^\\]+)\\"')
_PAYLOAD_GAME_RE = re.compile(
    r'\\"id\\":(\d+),\\"date\\":\\"\$D([^\\]+)\\",'
    r'\\"player1\\":\{\\"id\\":(\d+),\\"points\\":(-?\d+),\\"roundScore\\":(-?[\d.]+),\\"rank\\":(\d+)\},'
    r'\\"player2\\":\{\\"id\\":(\d+),\\"points\\":(-?\d+),\\"roundScore\\":(-?[\d.]+),\\"rank\\":(\d+)\},'
    r'\\"player3\\":\{\\"id\\":(\d+),\\"points\\":(-?\d+),\\"roundScore\\":(-?[\d.]+),\\"rank\\":(\d+)\},'
    r'\\"player4\\":\{\\"id\\":(\d+),\\"points\\":(-?\d+),\\"roundScore\\":(-?[\d.]+),\\"rank\\":(\d+)\}'
)

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


def normalize_game_date(date: str) -> str:
    """The site renders a freshly-played game's date with a timestamp (e.g.
    "11/7/2026, 6:00:02 AM") but renders the same game as just a date (e.g.
    "11/7/2026") once it's no longer brand-new. Since games are matched by
    (game_number, date), that format change alone created a second row for
    the same game once it "aged out" of the timestamped view. Keeping only
    the date portion makes both forms match the same stored row."""
    return date.split(",")[0].strip()


def parse_games(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    game_divs = soup.find_all("div", class_="game-display")
    game_divs = game_divs[::-1]  # site lists newest first; we want oldest first

    parsed = []
    for game in game_divs:
        header_spans = game.find("div", class_="game-display-header").find_all("span")
        game_number = header_spans[0].text
        game_date = normalize_game_date(header_spans[1].text)

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


def parse_games_from_payload(html: str) -> list[dict]:
    """The site now paginates the rendered Game Logs (10 per page by
    default), so parse_games alone only ever sees the newest page - any
    older game simply isn't in the HTML it looks at, and gets silently
    skipped forever once it "ages out" of that page. But the full history is
    still embedded in the same response as part of React's hydration data
    (see the regexes above), unpaginated, all the way back to game #1.

    The tradeoff: this payload's "roundScore" is each game's RAW score,
    before any chombo/penalty correction - corrections live in a separate
    list matched by player + a timestamp that doesn't reliably correspond to
    the game itself (penalties are sometimes added well after the fact,
    e.g. "Missed in-game chombo"), so there's no sound way to re-attach them
    here. Callers should prefer parse_games's rendered (and so
    penalty-correct) version of any game that's still visible there, and
    use this only to recover games that have already scrolled out of view -
    an approximate score beats a completely missing game, but isn't a full
    substitute for the rendered page where one's available.

    Returns games oldest-first, matching parse_games's ordering. Returns an
    empty list if the payload's shape doesn't match (e.g. a future site
    change) - callers should treat that as "recovery unavailable", not fail
    the whole scrape over it.
    """
    names = {int(m.group(1)): m.group(2) for m in _PAYLOAD_PLAYER_NAME_RE.finditer(html)}

    parsed = []
    for m in _PAYLOAD_GAME_RE.finditer(html):
        game_id, iso_date = m.group(1), m.group(2)
        player_groups = m.groups()[2:]  # 4 players x (id, points, roundScore, rank)

        ranked_players = []
        ok = True
        for i in range(4):
            pid, points, score, rank = player_groups[i * 4 : i * 4 + 4]
            name = names.get(int(pid))
            if name is None:
                ok = False  # unknown player id - skip rather than guess
                break
            ranked_players.append((int(rank), {"name": name, "points": int(points), "score": float(score)}))
        if not ok:
            continue

        local_date = datetime.fromisoformat(iso_date.replace("Z", "+00:00")).astimezone(_VENUE_TZ)
        parsed.append(
            {
                "game_number": f"Game #{game_id}",
                "date": f"{local_date.month}/{local_date.day}/{local_date.year}",
                "players": [p for _, p in sorted(ranked_players, key=lambda rp: rp[0])],  # rank order, not seat order
            }
        )

    parsed.sort(key=lambda g: int(g["game_number"].removeprefix("Game #")))
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
    leaderboard_is_empty: bool = False,
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
    _next_fallback_order_index = [
        db.query(Game).filter(Game.venue == venue).count()
    ]  # fallback counter, see _resolve_order_indices below

    def _numeric_order_index(game_number: str) -> int | None:
        try:
            return int(game_number.removeprefix("Game #"))
        except ValueError:
            return None

    # order_index needs to reflect each game's true chronological position,
    # not just "when we happened to insert it" — that assumption broke once
    # parse_games_from_payload could discover a historical game (e.g. #514)
    # that the site's pagination had hidden from every earlier scrape: being
    # "new" to our DB no longer means "the newest game", so simply appending
    # it put it last instead of between #510 and #518. The game's own number
    # is a stable, permanent chronological sequence, so order_index is just
    # that number — self-correcting every scrape for every game it touches,
    # not just ones inserted under the old scheme.
    #
    # Resolved up front (rather than inline per-row) because game_number
    # isn't actually guaranteed unique by itself — two DB rows can still
    # share one while a date-format-change duplicate (see
    # normalize_game_date) is waiting to be merged by
    # scripts/fix_duplicate_games.py. Without this, both would be assigned
    # the identical id-derived order_index and collide on
    # UniqueConstraint("venue", "order_index"); the second one here instead
    # falls back to a plain incrementing value until that cleanup runs.
    #
    # `claimed` starts seeded with every OTHER existing game's current
    # order_index (anything this call isn't about to touch/renumber) —
    # otherwise a duplicate-game_number row from a past, separate scrape
    # could go undetected simply because it isn't in this call's own batch.
    def _resolve_order_indices(games: list[dict]) -> dict[tuple[str, str], int]:
        touched_keys = {(g["game_number"], g["date"]) for g in games}
        claimed: set[int] = {
            existing.order_index
            for key, existing in existing_games.items()
            if key not in touched_keys
        }
        resolved: dict[tuple[str, str], int] = {}
        for g in games:
            key = (g["game_number"], g["date"])
            candidate = _numeric_order_index(g["game_number"])
            if candidate is None or candidate in claimed:
                candidate = _next_fallback_order_index[0]
                _next_fallback_order_index[0] += 1
            claimed.add(candidate)
            resolved[key] = candidate
        return resolved

    final_order_index = _resolve_order_indices(parsed_games)

    # GP is written to the site's leaderboard the moment a game is
    # submitted, so a completely empty leaderboard for the newest season is
    # a reliable signal — not just "GP hasn't caught up yet" — that zero
    # games have actually been played in it so far. In that case, anything
    # still untagged must really belong to whichever season was accruing
    # before it, so fall back to that instead of leaving new games
    # unseasoned (where they'd otherwise sit invisible under every
    # season-filtered view until the new season's board gets real data).
    #
    # This must read order_index before the renumbering below touches it.
    fallback_season = None
    if leaderboard_is_empty and season_label is not None:
        most_recently_tagged = max(
            (g for g in existing_games.values() if g.season is not None),
            key=lambda g: g.order_index,
            default=None,
        )
        if most_recently_tagged is not None:
            fallback_season = most_recently_tagged.season

    # Two-phase update: first move every touched existing game's order_index
    # to a negative placeholder, then assign final values in the main loop
    # below. Without this, reassigning one game's order_index to a value
    # another not-yet-updated game still holds would trip the
    # UniqueConstraint("venue", "order_index") mid-transaction.
    for key, existing in existing_games.items():
        if key in final_order_index:
            existing.order_index = -final_order_index[key] - 1
    db.flush()

    total = len(parsed_games)
    # Saving is the one phase with a known total to report real fractional
    # progress against, spread across the 50-90% band of the overall scrape.
    for i, g in enumerate(parsed_games):
        key = (g["game_number"], g["date"])
        game = existing_games.get(key)
        is_confirmed = key in current_season_keys
        this_games_season = season_label if is_confirmed else fallback_season

        if game is None:
            game = Game(
                venue=venue,
                game_number=g["game_number"],
                date=g["date"],
                order_index=final_order_index[key],
                season=this_games_season,
                season_provisional=this_games_season is not None and not is_confirmed,
            )
            db.add(game)
            db.flush()  # need game.id before attaching results
            existing_games[key] = game
        else:
            game.order_index = final_order_index[key]
            # A *confirmed* season (set from an actual GP-window match) is
            # locked forever — never reassigned by a later scrape, even to a
            # "current" season computed from this scrape's own (possibly
            # stale/transient) GP numbers. This is what stops the
            # corruption seen at the Season 4→5 boundary: a brief window
            # where the site reported "Season 5" as current with GP numbers
            # that, combined with the complete game history, pointed back
            # at a handful of genuine Season 4 games and reassigned them.
            #
            # A *provisional* season (only ever guessed via the
            # leaderboard_is_empty fallback below, because there was no GP
            # data yet to confirm anything) is NOT locked the same way —
            # once real GP evidence arrives, it should correct the guess
            # rather than leave it stuck. That's the gap that left genuine
            # Season 5 games permanently mislabeled "Season 4" once Season
            # 5's leaderboard filled in: the fallback guess got treated as
            # equally permanent as a confirmed tag.
            if game.season is None:
                if this_games_season is not None:
                    game.season = this_games_season
                    game.season_provisional = not is_confirmed
            elif game.season_provisional and is_confirmed:
                game.season = season_label
                game.season_provisional = False
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


def save_venue_state(venue: str, season_label: str | None, db: Session) -> None:
    """Record the newest season label seen, even when its leaderboard is
    empty — unlike Game.season, this isn't lost just because nothing's
    tagged with it yet, so the UI can still list it."""
    if season_label is None:
        return
    state = db.get(VenueState, venue)
    if state is None:
        state = VenueState(venue=venue, latest_season_label=season_label)
        db.add(state)
    else:
        state.latest_season_label = season_label
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
    visible_games = parse_games(html)
    recovered_games = parse_games_from_payload(html)

    # Merge: the full history from the embedded payload is the base (it's
    # the only one that reaches past whatever page the site happens to be
    # showing), but swap in the rendered version of any game that's also
    # currently visible there, since only that one reflects a chombo/penalty
    # correction if one applies. See parse_games_from_payload for why the
    # two can't simply be reconciled the other way around.
    if recovered_games:
        visible_by_key = {g["game_number"]: g for g in visible_games}
        parsed_games = [visible_by_key.get(g["game_number"], g) for g in recovered_games]
        extra_keys = set(visible_by_key) - {g["game_number"] for g in recovered_games}
        parsed_games += [visible_by_key[k] for k in extra_keys]
    else:
        logger.warning(
            "Couldn't recover full game history for %s from the embedded payload "
            "(site layout may have changed) — falling back to just the visible page",
            venue,
        )
        parsed_games = visible_games

    games_played = parse_scoreboard(html)
    season_label = parse_season_label(html)

    # An empty/missing scoreboard (e.g. right after a new season opens with
    # no games recorded yet, or a site layout change breaks our scoreboard
    # selector) means we have no trustworthy GP numbers to work from — we
    # can't compute *which* games are this season's. save_games still
    # handles this case (falling back untagged games to the previous
    # season, see leaderboard_is_empty below); this just means we can't
    # positively confirm anything as belonging to the new season yet.
    leaderboard_is_empty = not games_played
    if games_played and season_label is not None:
        current_season_keys = compute_current_season_game_keys(parsed_games, games_played)
    else:
        logger.info(
            "No scoreboard/season data for %s this scrape (season_label=%r, %d GP entries) — "
            "falling back untagged games to the previous season",
            venue,
            season_label,
            len(games_played),
        )
        current_season_keys = set()

    db = SessionLocal()
    try:
        count = save_games(
            venue,
            parsed_games,
            db,
            season_label=season_label,
            current_season_keys=current_season_keys,
            leaderboard_is_empty=leaderboard_is_empty,
            on_progress=on_progress,
        )
        save_venue_state(venue, season_label, db)
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
