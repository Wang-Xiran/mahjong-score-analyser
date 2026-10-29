from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .. import progress, stats
from ..db import get_db
from ..scraper import ScrapeInProgress, start_scrape_job

router = APIRouter(prefix="/ui")
templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")


@router.get("/players")
def players_fragment(request: Request, venue: str, db: Session = Depends(get_db)):
    names = stats.get_player_names(venue, db)
    seasons = stats.get_seasons(venue, db)
    current_season = stats.get_current_season(venue, db)
    return templates.TemplateResponse(
        request,
        "_filters.html",
        {"venue": venue, "players": names, "seasons": seasons, "current_season": current_season},
    )


@router.get("/stats")
def stats_fragment(
    request: Request, venue: str, player: str, season: str | None = None, db: Session = Depends(get_db)
):
    summary = stats.get_player_summary(venue, player, db, season=season or None)
    return templates.TemplateResponse(request, "_stats.html", {"summary": summary})


@router.get("/game/{game_id}")
def game_modal(request: Request, game_id: int, db: Session = Depends(get_db)):
    details = stats.get_game_details(game_id, db)
    if details is None:
        raise HTTPException(404, "Unknown game")
    return templates.TemplateResponse(request, "_game_modal.html", {"game": details})


@router.post("/scrape")
def scrape_action(request: Request, background_tasks: BackgroundTasks, venue: str = Form(...)):
    try:
        job_id = start_scrape_job(venue, background_tasks)
    except ScrapeInProgress:
        return templates.TemplateResponse(
            request,
            "_scrape_status.html",
            {"message": "A scrape for this venue is already running — try again shortly."},
        )

    return templates.TemplateResponse(
        request, "_scrape_progress.html", {"job_id": job_id, "percent": 0, "message": "Starting…"}
    )


@router.get("/scrape/progress/{job_id}")
def scrape_progress(request: Request, job_id: str):
    job = progress.get_job(job_id)
    if job is None:
        return templates.TemplateResponse(
            request, "_scrape_status.html", {"message": "Unknown scrape job."}
        )

    if job["status"] == "running":
        return templates.TemplateResponse(
            request,
            "_scrape_progress.html",
            {"job_id": job_id, "percent": job["percent"], "message": job["message"]},
        )

    return templates.TemplateResponse(request, "_scrape_status.html", {"message": job["message"]})
