import dataclasses
import itertools
from fastapi import APIRouter
from server.src.database import get_db
from shared.schema.data_objects import SteamApp
from shared.schema.steamPage import GamePage

router = APIRouter(prefix="/apps", tags=["apps"])


@router.get("/stats")
def stats():
    db = get_db()
    total = db.count_apps()
    remaining = db.count_apps(unscraped_only=True)
    return {"total": total, "scraped": total - remaining, "remaining": remaining}


@router.get("/next")
def get_next_batch(batch: int = 50):
    db = get_db()
    apps: list[SteamApp] = list(itertools.islice(db.get_apps(unscraped_only=True), batch))
    return [dataclasses.asdict(a) for a in apps]


@router.post("/results")
def submit_results(results: list[GamePage]):
    db = get_db()
    for result in results:
        db.save_game_page_info(result)
    return {"saved": len(results)}
