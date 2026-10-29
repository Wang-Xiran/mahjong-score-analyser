import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# Default lives next to the backend package (not wherever the process happens
# to be launched from) so local/non-Docker runs don't scatter a "data" folder
# at an unrelated cwd; Docker overrides this via the DB_PATH env var.
_DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "mahjong.db"
DB_PATH = os.environ.get("DB_PATH", str(_DEFAULT_DB_PATH))
os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def init_db():
    from . import models  # noqa: F401 register tables before create_all
    Base.metadata.create_all(bind=engine)
    _migrate_schema()


def _migrate_schema() -> None:
    """create_all only creates missing tables, never alters existing ones —
    this applies the small schema changes an existing games.db needs to pick
    up on an upgrade. Each step is idempotent, so it's safe to run on every
    startup."""
    with engine.begin() as conn:
        columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(games)")}
        if "season" not in columns:
            conn.exec_driver_sql("ALTER TABLE games ADD COLUMN season VARCHAR")

        indexes = {row[1] for row in conn.exec_driver_sql("PRAGMA index_list(games)")}
        if "uq_venue_game_number" in indexes:
            # Superseded by uq_venue_date_game_number below — matching on
            # game_number alone would collide if a season ever restarts
            # numbering at #1.
            conn.exec_driver_sql("DROP INDEX uq_venue_game_number")
        if "uq_venue_date_game_number" not in indexes:
            conn.exec_driver_sql(
                "CREATE UNIQUE INDEX uq_venue_date_game_number ON games (venue, date, game_number)"
            )


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
