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
    client.save_results(results)
    for result in results:
        if not result.scraped_ok:
            # An invalid scrape never retries on its own otherwise: it would sit
            # at scraped_ok=0 forever, still claimable, without ever accruing
            # towards FAILURE_THRESHOLD like a client-side processing failure does.
            client.report_failure(result.appid)
    return {"saved": len(results)}


@router.post("/fail/{appid}")
def report_failure(appid: int):
    get_client().report_failure(appid)
    return {"ok": True}
