import dataclasses
from typing import Generator, Optional
from enum import Enum
import sqlite_utils
from schema.data_objects import SteamApp
from schema.steamPage import GamePage

db: Optional[sqlite_utils.Database] = None

def init_db(path: str) -> None:
    global db
    db = sqlite_utils.Database(path)

def addApps(apps: list[SteamApp]):
    db["apps"].upsert_all(  # type: ignore[union-attr]
        [dataclasses.asdict(a) for a in apps],
        pk="appid",  # type: ignore[arg-type]
    )
    
def getApps(unscraped_only: bool = False) -> Generator[SteamApp, None, None]:
    table = db["apps"]  # type: ignore[union-attr]
    rows = table.rows_where("scraped_ok IS NOT 1") if unscraped_only else table.rows
    for row in rows:
        yield SteamApp.from_dict(row)

def countApps(unscraped_only: bool = False) -> int:
    table = db["apps"]  # type: ignore[union-attr]
    return table.count_where("scraped_ok IS NOT 1") if unscraped_only else table.count
        
def saveGamePageInfo(gamePage: GamePage):
    db["apps"].upsert(  # type: ignore[union-attr]
        dataclasses.asdict(gamePage),
        pk="appid",  # type: ignore[arg-type]
        alter=True,  # type: ignore[arg-type]
    )