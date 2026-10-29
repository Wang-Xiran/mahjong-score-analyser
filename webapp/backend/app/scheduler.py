import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .scraper import ScrapeInProgress, scrape_venue_exclusive
from .venues import VENUES

logger = logging.getLogger(__name__)
scheduler = BackgroundScheduler()


def _run_scrape(venue: str):
    try:
        count = scrape_venue_exclusive(venue)
        logger.info("Scheduled scrape for %s stored %d games", venue, count)
    except ScrapeInProgress:
        logger.info("Skipped scheduled scrape for %s: already running", venue)
    except Exception:
        logger.exception("Scheduled scrape failed for %s", venue)


def start_scheduler():
    """Add one cron job per interval slot inside each venue's active window,
    so the scraper only runs while that venue is actually playing."""
    for slug, cfg in VENUES.items():
        start_h, start_m = (int(x) for x in cfg["start"].split(":"))
        end_h, end_m = (int(x) for x in cfg["end"].split(":"))
        start_minutes = start_h * 60 + start_m
        end_minutes = end_h * 60 + end_m
        interval = cfg.get("interval_minutes", 30)

        for offset in range(0, end_minutes - start_minutes + 1, interval):
            total_minutes = start_minutes + offset
            hour, minute = divmod(total_minutes, 60)
            scheduler.add_job(
                _run_scrape,
                CronTrigger(day_of_week=cfg["day"], hour=hour, minute=minute),
                args=[slug],
                id=f"{slug}-{hour:02d}{minute:02d}",
                replace_existing=True,
            )

    scheduler.start()
