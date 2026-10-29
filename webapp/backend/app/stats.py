import re

from sqlalchemy.orm import Session

from .models import Game, PlayerGamesPlayed, PlayerResult


def get_player_names(venue: str, db: Session) -> list[str]:
    rows = (
        db.query(PlayerResult.name)
        .join(Game, PlayerResult.game_id == Game.id)
        .filter(Game.venue == venue)
        .distinct()
        .order_by(PlayerResult.name)
        .all()
    )
    return [r[0] for r in rows]


def _season_sort_key(season: str) -> int:
    m = re.search(r"\d+", season)
    return int(m.group()) if m else -1


def get_seasons(venue: str, db: Session) -> list[str]:
    """Every season tagged in the DB for this venue, newest first."""
    rows = (
        db.query(Game.season)
        .filter(Game.venue == venue, Game.season.isnot(None))
        .distinct()
        .all()
    )
    return sorted((r[0] for r in rows), key=_season_sort_key, reverse=True)


def get_current_season(venue: str, db: Session) -> str | None:
    """The season of the venue's most recent game — whichever season is
    still open, as opposed to one of the closed past seasons."""
    row = db.query(Game.season).filter(Game.venue == venue).order_by(Game.order_index.desc()).first()
    return row[0] if row else None


def get_player_results(venue: str, player: str, db: Session, season: str | None = None) -> list[dict]:
    query = (
        db.query(PlayerResult, Game)
        .join(Game, PlayerResult.game_id == Game.id)
        .filter(Game.venue == venue, PlayerResult.name == player)
    )
    if season is not None:
        query = query.filter(Game.season == season)
    rows = query.order_by(Game.order_index).all()

    return [
        {
            "game_id": game.id,
            "game_number": game.game_number,
            "date": game.date,
            "placement": result.placement,
            "points": result.points,
            "score": result.score,
        }
        for result, game in rows
    ]


def get_game_details(game_id: int, db: Session) -> dict | None:
    game = db.get(Game, game_id)
    if game is None:
        return None

    players = (
        db.query(PlayerResult)
        .filter(PlayerResult.game_id == game_id)
        .order_by(PlayerResult.placement)
        .all()
    )
    return {
        "game_number": game.game_number,
        "date": game.date,
        "players": [
            {"placement": p.placement, "name": p.name, "points": p.points, "score": p.score}
            for p in players
        ],
    }


def get_games_played(venue: str, player: str, db: Session) -> int | None:
    """The site's current-season GP for this player, or None if they're not
    on the scoreboard (e.g. scraped before the scoreboard table existed)."""
    row = (
        db.query(PlayerGamesPlayed.games_played)
        .filter(PlayerGamesPlayed.venue == venue, PlayerGamesPlayed.name == player)
        .first()
    )
    return row[0] if row else None


def calc_best_window(results: list[dict], window_size: int = 10) -> dict:
    """results must be ordered oldest-to-newest (as get_player_results returns)."""
    if len(results) < window_size:
        return {"available": False, "games_needed": window_size - len(results)}

    best_sum = None
    best_frame: list[dict] = []
    current_sum = 0.0

    last_i = len(results) - window_size
    for i in range(last_i + 1):
        frame = results[i : i + window_size]
        frame_sum = sum(r["score"] for r in frame)
        if best_sum is None or frame_sum > best_sum:
            best_sum = frame_sum
            best_frame = frame
        if i == last_i:
            current_sum = frame_sum

    diff = round(best_sum - current_sum, 1)
    return {
        "available": True,
        "best_frame": list(reversed(best_frame)),  # most recent game first, matching match_history
        "best_sum": round(best_sum, 1),
        "on_high_run": diff == 0,
        "points_to_beat_pb": None if diff == 0 else diff,
    }


def calc_ranking_distribution(results: list[dict]) -> dict:
    counts = {1: 0, 2: 0, 3: 0, 4: 0}
    for r in results:
        counts[r["placement"]] += 1
    total = sum(counts.values())
    percentages = {k: (v / total * 100 if total else 0.0) for k, v in counts.items()}
    return {"total_games": total, "counts": counts, "percentages": percentages}


def get_player_summary(venue: str, player: str, db: Session, season: str | None = None) -> dict:
    """season=None means "Overall": full history, ranking + match history
    only, no best-10 block. A season name scopes everything to just that
    season's games and adds the best-10 block — the window is exactly that
    season's games (no further limiting needed, since each game is already
    tagged with the one season it belongs to). The "needs N more points" /
    "on their high run" messaging only makes sense for the still-open
    current season, not a closed past one, so callers should check
    is_current_season before showing it."""
    results = get_player_results(venue, player, db, season=season)

    best_10 = None
    is_current_season = False
    if season is not None:
        best_10 = calc_best_window(results, window_size=10)
        is_current_season = season == get_current_season(venue, db)

    return {
        "player": player,
        "season": season,
        "is_current_season": is_current_season,
        "best_10": best_10,
        "ranking": calc_ranking_distribution(results),
        "match_history": list(reversed(results)),  # most recent game first
    }
