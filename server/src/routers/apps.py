import dataclasses
import itertools
from fastapi import APIRouter
from server.src.database import countApps, getApps, saveGamePageInfo
from shared.schema.data_objects import SteamApp
from shared.schema.steamPage import GamePage

router = APIRouter(prefix="/apps", tags=["apps"])


@router.get("/stats")
def stats():
    total = countApps()
    remaining = countApps(unscraped_only=True)
    return {"total": total, "scraped": total - remaining, "remaining": remaining}


@router.get("/next")
def get_next_batch(batch: int = 50):
    apps: list[SteamApp] = list(itertools.islice(getApps(unscraped_only=True), batch))
    return [dataclasses.asdict(a) for a in apps]


@router.post("/results")
def submit_results(results: list[GamePage]):
    for result in results:
        saveGamePageInfo(result)
    return {"saved": len(results)}
