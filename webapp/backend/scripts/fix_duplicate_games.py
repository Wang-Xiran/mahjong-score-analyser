"""One-off cleanup for games that got stored twice under two different date
strings for the same game_number — caused by the site rendering a freshly-
played game's date with a timestamp, then as just a date once it's no longer
brand-new (see scraper.normalize_game_date, which stops this happening for
any *new* scrape, but doesn't retroactively merge rows already duplicated by
scrapes from before that fix).

For each game_number with more than one row: keeps the row with a confirmed
season tag if one of the duplicates has it (merging that info onto the kept
row if needed), normalizes its date, and deletes the other row(s) along with
their player results.

Usage: python scripts/fix_duplicate_games.py [--apply]
Without --apply, only prints what it would change (dry run).
"""

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal, init_db
from app.models import Game, PlayerResult
from app.scraper import normalize_game_date

VENUE = "boxhill"


def _keep_priority(game: Game) -> tuple[int, int]:
    # Prefer a confirmed tag over a provisional one over no tag at all;
    # among equals, prefer the earliest-inserted row.
    if game.season is not None and not game.season_provisional:
        tag_rank = 0
    elif game.season is not None:
        tag_rank = 1
    else:
        tag_rank = 2
    return (tag_rank, game.order_index)


def main(apply: bool) -> None:
    init_db()
    db = SessionLocal()
    try:
        games = db.query(Game).filter(Game.venue == VENUE).order_by(Game.order_index).all()

        groups: dict[str, list[Game]] = defaultdict(list)
        for g in games:
            groups[g.game_number].append(g)

        dup_groups = {k: v for k, v in groups.items() if len(v) > 1}
        if not dup_groups:
            print("No duplicate games found.")
            return

        for game_number, dups in dup_groups.items():
            details = ", ".join(
                f"[id={g.id} date={g.date!r} season={g.season!r} provisional={g.season_provisional}]"
                for g in dups
            )
            print(f"{game_number}: {len(dups)} rows -> {details}")

            keep = min(dups, key=_keep_priority)
            losers = [g for g in dups if g.id != keep.id]

            for loser in losers:
                if loser.season is not None and not loser.season_provisional:
                    if keep.season is None or keep.season_provisional:
                        keep.season = loser.season
                        keep.season_provisional = False

            new_date = normalize_game_date(keep.date)
            print(f"  -> keeping id={keep.id}, date {keep.date!r} -> {new_date!r}, season={keep.season!r}")

            if apply:
                # Delete the losers (and flush) before renaming keep's date —
                # otherwise the update and the still-present loser row would
                # momentarily collide on the (venue, date, game_number)
                # unique index within the same flush.
                for loser in losers:
                    db.query(PlayerResult).filter(PlayerResult.game_id == loser.id).delete(
                        synchronize_session=False
                    )
                    db.delete(loser)
                db.flush()
                keep.date = new_date

        if apply:
            db.commit()
            print(f"\nApplied: merged {len(dup_groups)} duplicate game group(s).")
        else:
            print(f"\nDry run: {len(dup_groups)} duplicate group(s) would be merged. Re-run with --apply to commit.")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Actually write the changes (default is dry-run).")
    args = parser.parse_args()
    main(apply=args.apply)
