import dataclasses
import sqlite3
import threading
from typing import Generator, Optional
import sqlite_utils
from shared.schema.data_objects import SteamApp
from shared.schema.steamPage import GamePage


class Database:
    def __init__(self, path: str):
        conn = sqlite3.connect(path, check_same_thread=False)
        self._db = sqlite_utils.Database(conn)
        self._write_lock = threading.Lock()

    def add_apps(self, apps: list[SteamApp]) -> None:
        with self._write_lock:
            self._db["apps"].upsert_all(  # type: ignore[union-attr]
                [dataclasses.asdict(a) for a in apps],
                pk="appid",  # type: ignore[arg-type]
            )

    def get_apps(self, unscraped_only: bool = False) -> Generator[SteamApp, None, None]:
        table = self._db["apps"]  # type: ignore[union-attr]
        rows = table.rows_where("scraped_ok IS NOT 1") if unscraped_only else table.rows
        for row in rows:
            yield SteamApp.from_dict(row)

    def count_apps(self, unscraped_only: bool = False) -> int:
        table = self._db["apps"]  # type: ignore[union-attr]
        return table.count_where("scraped_ok IS NOT 1") if unscraped_only else table.count

    def save_game_page_info(self, page: GamePage) -> None:
        with self._write_lock:
            self._db["apps"].upsert(  # type: ignore[union-attr]
                dataclasses.asdict(page),
                pk="appid",  # type: ignore[arg-type]
                alter=True,  # type: ignore[arg-type]
            )


_db: Optional[Database] = None


def init(path: str) -> None:
    global _db
    _db = Database(path)


def get_db() -> Database:
    return _db  # type: ignore[return-value]
