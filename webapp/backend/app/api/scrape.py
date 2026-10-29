import logging

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from ..scraper import ScrapeInProgress, scrape_venue_exclusive
from ..venues import VENUES

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)


@router.post("/scrape/{venue}")
async def manual_scrape(venue: str):
    if venue not in VENUES:
        raise HTTPException(404, "Unknown venue")

    try:
        count = await run_in_threadpool(scrape_venue_exclusive, venue)
    except ScrapeInProgress:
        raise HTTPException(409, "Scrape already in progress for this venue")
    except Exception as exc:
        logger.exception("Manual scrape failed for %s", venue)
        raise HTTPException(500, f"Scrape failed: {exc}")

    return {"venue": venue, "games_scraped": count}
