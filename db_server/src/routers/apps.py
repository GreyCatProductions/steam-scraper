import dataclasses
from fastapi import APIRouter
from db_server.src.database import get_db
from shared.schema.data_objects import SteamApp
from shared.schema.steamPage import GamePage

router = APIRouter(prefix="/apps", tags=["apps"])


@router.post("")
def add_apps(apps: list[SteamApp]):
    get_db().add_apps(apps)
    return {"added": len(apps)}


@router.get("/claim")
def claim_apps(amount: int = 50):
    return [dataclasses.asdict(a) for a in get_db().claim_apps(amount)]


@router.get("/count")
def count_apps(unscraped_only: bool = False):
    return {"count": get_db().count_apps(unscraped_only=unscraped_only)}


@router.post("/results")
def save_results(results: list[GamePage]):
    for result in results:
        get_db().save_game_page_info(result)
    return {"saved": len(results)}
