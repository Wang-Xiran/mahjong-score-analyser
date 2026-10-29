# Each venue only runs on its own day/time window, so the scheduler should
# not spin up a browser outside those hours. Add new venues here as they're
# supported; "day" uses APScheduler's cron day_of_week names (mon..sun).
VENUES = {
    "boxhill": {
        "name": "Box Hill",
        "url": "https://scores.vicriichi.org/events/boxhill",
        "day": "sat",
        "start": "12:30",
        "end": "17:30",
        "interval_minutes": 30,
    },
}
