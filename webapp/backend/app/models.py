from sqlalchemy import Column, Float, ForeignKey, Integer, String, UniqueConstraint
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
