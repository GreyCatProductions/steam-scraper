import dataclasses
from fastapi import APIRouter
from processing_server.src.db_client import get_client
from shared.schema.steamPage import GamePage

router = APIRouter(prefix="/apps", tags=["apps"])


@router.get("/stats")
def stats():
    client = get_client()
    total = client.count_apps()
    remaining = client.count_apps(unscraped_only=True)
    return {"total": total, "scraped": total - remaining, "remaining": remaining}


@router.get("/next")
def get_next_batch(batch: int = 50):
    return [dataclasses.asdict(a) for a in get_client().claim_apps(batch)]


@router.post("/results")
def submit_results(results: list[GamePage]):
    client = get_client()
    for result in results:
        if not result.is_valid():
            result.scraped_ok = False
        client.save_game_page_info(result)
    return {"saved": len(results)}
