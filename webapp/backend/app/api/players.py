from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import stats
from ..db import get_db
from ..venues import VENUES

router = APIRouter(prefix="/api")


@router.get("/venues")
def list_venues():
    return [{"slug": slug, "name": cfg["name"]} for slug, cfg in VENUES.items()]


@router.get("/venues/{venue}/players")
def list_players(venue: str, db: Session = Depends(get_db)):
    if venue not in VENUES:
        raise HTTPException(404, "Unknown venue")
    return stats.get_player_names(venue, db)


@router.get("/venues/{venue}/seasons")
def list_seasons(venue: str, db: Session = Depends(get_db)):
    if venue not in VENUES:
        raise HTTPException(404, "Unknown venue")
    return stats.get_seasons(venue, db)


@router.get("/venues/{venue}/players/{player}/stats")
def player_stats(venue: str, player: str, season: str | None = None, db: Session = Depends(get_db)):
    if venue not in VENUES:
        raise HTTPException(404, "Unknown venue")
    return stats.get_player_summary(venue, player, db, season=season)
