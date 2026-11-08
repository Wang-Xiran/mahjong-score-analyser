from sqlalchemy import Boolean, Column, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import relationship

from .db import Base


class Game(Base):
    __tablename__ = "games"

    id = Column(Integer, primary_key=True)
    venue = Column(String, index=True, nullable=False)
    game_number = Column(String, nullable=False)  # text as scraped, e.g. "Game #495"
    date = Column(String, nullable=False)  # text as scraped, venue's own date format
    order_index = Column(Integer, nullable=False)  # position in scrape order, oldest = 0
    # Which season this game belongs to (e.g. "Season 4"), derived from the
    # site's scoreboard GP numbers at scrape time. Null for games older than
    # what GP currently covers — we have no way to attribute those yet.
    season = Column(String, nullable=True, index=True)
    # True when `season` is only a guess (the leaderboard was empty when it
    # was tagged, see scraper.save_games's leaderboard_is_empty fallback),
    # as opposed to confirmed by an actual GP-window match. A provisional
    # tag can still be corrected once real evidence arrives; a confirmed one
    # (season_provisional=False) never is — that's what stops a transient
    # bad scrape from reassigning genuinely old games.
    season_provisional = Column(Boolean, nullable=False, default=False)

    results = relationship("PlayerResult", back_populates="game", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("venue", "order_index", name="uq_venue_order"),
        # NOT (venue, game_number) alone: the site's game numbering has been
        # continuous across every season observed so far, but if a future
        # season ever restarts numbering at #1, matching on game_number alone
        # would collide with an old season's "Game #1" and overwrite its
        # history. Date always differs across seasons, so the pair is safe.
        UniqueConstraint("venue", "date", "game_number", name="uq_venue_date_game_number"),
    )


class PlayerResult(Base):
    __tablename__ = "player_results"

    id = Column(Integer, primary_key=True)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)
    placement = Column(Integer, nullable=False)  # 1-4
    name = Column(String, nullable=False, index=True)
    points = Column(Integer, nullable=False)
    score = Column(Float, nullable=False)

    game = relationship("Game", back_populates="results")


class PlayerGamesPlayed(Base):
    """The site's own current-season "GP" (games played) count per player,
    from its scoreboard table — distinct from total games in `games`/
    `player_results`, which span the venue's full history."""

    __tablename__ = "player_games_played"

    id = Column(Integer, primary_key=True)
    venue = Column(String, index=True, nullable=False)
    name = Column(String, nullable=False, index=True)
    games_played = Column(Integer, nullable=False)

    __table_args__ = (UniqueConstraint("venue", "name", name="uq_venue_player_gp"),)


class VenueState(Base):
    """One row per venue, updated on every scrape regardless of whether the
    scoreboard has any data. Lets the UI still list the newest season (e.g.
    for the dropdown) even while its leaderboard is completely empty — that
    info would otherwise be lost, since `Game.season` only reflects seasons
    that actually have at least one tagged game."""

    __tablename__ = "venue_state"

    venue = Column(String, primary_key=True)
    latest_season_label = Column(String, nullable=True)
