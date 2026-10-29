import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .api import players, scrape, ui
from .db import init_db
from .scheduler import start_scheduler
from .venues import VENUES

logging.basicConfig(level=logging.INFO)

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Mahjong Score Analyser")
app.include_router(players.router)
app.include_router(scrape.router)
app.include_router(ui.router)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.on_event("startup")
def on_startup():
    init_db()
    start_scheduler()


@app.get("/")
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"venues": VENUES})
