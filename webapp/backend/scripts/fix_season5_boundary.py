"""One-off correction for games that got stuck tagged "Season 4" during the
window when Season 5's leaderboard was still empty (see scraper.save_games's
leaderboard_is_empty fallback). Those tags predate the season_provisional
column, so the migration default (confirmed) left them locked even after
Season 5 got real GP data.

This re-derives "whose games are actually this season" the same way
compute_current_season_game_keys does during a live scrape — each player's
last `gp` games, per the CURRENT (already-scraped, non-empty) scoreboard —
but against the full stored game history instead of a fresh HTML parse, and
corrects any game that evidence now says belongs to the latest season but
isn't tagged that way yet.

Usage: python scripts/fix_season5_boundary.py [--apply]
Without --apply, only prints what it would change (dry run).
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal, init_db
from app.models import Game, PlayerGamesPlayed

VENUE = "boxhill"
LATEST_SEASON = "Season 5"


def main(apply: bool) -> None:
    init_db()
    db = SessionLocal()
    try:
        games = (
            db.query(Game)
            .filter(Game.venue == VENUE)
            .order_by(Game.order_index)
            .all()
        )
        games_played = {
            row.name: row.games_played
            for row in db.query(PlayerGamesPlayed).filter(PlayerGamesPlayed.venue == VENUE).all()
        }

        games_by_player: dict[str, list[Game]] = {}
        for game in games:
            for result in game.results:
                games_by_player.setdefault(result.name, []).append(game)

        confirmed_ids: set[int] = set()
        for name, gp in games_played.items():
            if gp > 0:
                confirmed_ids.update(g.id for g in games_by_player.get(name, [])[-gp:])

        changes = [g for g in games if g.id in confirmed_ids and g.season != LATEST_SEASON]

        if not changes:
            print("No games need correcting.")
            return

        for g in changes:
            print(f"{g.game_number} ({g.date}): {g.season!r} -> {LATEST_SEASON!r}")

        if apply:
            for g in changes:
                g.season = LATEST_SEASON
                g.season_provisional = False
            db.commit()
            print(f"\nApplied: corrected {len(changes)} games.")
        else:
            print(f"\nDry run: {len(changes)} games would be corrected. Re-run with --apply to commit.")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Actually write the changes (default is dry-run).")
    args = parser.parse_args()
    main(apply=args.apply)
