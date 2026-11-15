from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .. import charts, progress, stats
from ..db import get_db
from ..scraper import ScrapeInProgress, start_scrape_job

router = APIRouter(prefix="/ui")
templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")

# The trend toggle button cycles through these window sizes in order.
TREND_WINDOW_CYCLE = {10: 20, 20: 40, 40: 10}


@router.get("/players")
def players_fragment(request: Request, venue: str, db: Session = Depends(get_db)):
    names = stats.get_player_names(venue, db)
    seasons = stats.get_seasons(venue, db)
    current_season = stats.get_current_season(venue, db)
    # current_season is None right after a new season opens but before any
    # of its games have a GP reading to tag from (see scraper.scrape_venue) —
    # in that gap, default the dropdown to the most recent season that does
    # have games tagged, rather than silently falling back to "Overall".
    # current_season itself stays None/unchanged: it's also used to decide
    # the "(Current)" label and the best-10 forward-looking messaging, and
    # a season with no tagged games yet genuinely isn't "current" for those.
    default_season = current_season or (seasons[0] if seasons else None)
    return templates.TemplateResponse(
        request,
        "_filters.html",
        {
            "venue": venue,
            "players": names,
            "seasons": seasons,
            "current_season": current_season,
            "default_season": default_season,
        },
    )


@router.get("/stats")
def stats_fragment(
    request: Request, venue: str, player: str, season: str | None = None, db: Session = Depends(get_db)
):
    summary = stats.get_player_summary(venue, player, db, season=season or None)
    return templates.TemplateResponse(request, "_stats.html", {"summary": summary})


@router.post("/season-window")
def set_season_window(
    request: Request,
    venue: str = Form(...),
    player: str = Form(...),
    season: str = Form(...),
    window_size: int = Form(...),
    db: Session = Depends(get_db),
):
    # Clamped rather than rejected: this only changes how "best run" is
    # computed, not any underlying game data, so there's no real harm in
    # being forgiving about a slightly out-of-range value typed by hand.
    window_size = max(1, min(window_size, 50))
    stats.set_best_window_size(venue, season, window_size, db)
    summary = stats.get_player_summary(venue, player, db, season=season)
    return templates.TemplateResponse(request, "_stats.html", {"summary": summary})


@router.get("/trend")
def trend_fragment(
    request: Request,
    venue: str,
    player: str,
    season: str | None = None,
    limit: int = 10,
    db: Session = Depends(get_db),
):
    if limit not in TREND_WINDOW_CYCLE:
        limit = 10

    results = stats.get_player_results(venue, player, db, season=season or None)
    chart = charts.build_placement_trend(results[-limit:])

    return templates.TemplateResponse(
        request,
        "_placement_trend.html",
        {"chart": chart, "limit": limit, "next_limit": TREND_WINDOW_CYCLE[limit]},
    )


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
