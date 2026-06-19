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
    
def getApps() -> Generator[SteamApp, None, None]:
    for row in db["apps"].rows:  # type: ignore[union-attr]
        yield SteamApp.from_dict(row)

def countApps() -> int:
    return db["apps"].count  # type: ignore[union-attr]
        
def saveGamePageInfo(gamePage: GamePage):
    db["apps"].upsert(  # type: ignore[union-attr]
        dataclasses.asdict(gamePage),
        pk="appid",  # type: ignore[arg-type]
    )