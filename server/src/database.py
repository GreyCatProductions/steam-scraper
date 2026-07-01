import dataclasses
import sqlite3
import threading
import time
from typing import Generator, Optional
import sqlite_utils
from shared.schema.data_objects import SteamApp
from shared.schema.review import UserReview
from shared.schema.steamPage import GamePage


class Database:
    def __init__(self, path: str):
        conn = sqlite3.connect(path, check_same_thread=False)
        self._db = sqlite_utils.Database(conn)
        self._lock = threading.Lock()
        self._db["apps"].create({"appid": int}, pk="appid", if_not_exists=True)  # type: ignore
        self._db["reviews"].create({"recommendation_id": int}, pk="recommendation_id", if_not_exists=True)  # type: ignore

    def add_apps(self, apps: list[SteamApp]) -> None:
        with self._lock:
            self._db["apps"].upsert_all(  # type: ignore[union-attr]
                [dataclasses.asdict(a) for a in apps],
                pk="appid",  # type: ignore[arg-type]
                alter=True,  # type: ignore[arg-type]
            )

    def _scraped_col_exists(self) -> bool:
        return "scraped_ok" in {col.name for col in self._db["apps"].columns}  # type: ignore

    def get_apps(self, unscraped_only: bool = False) -> Generator[SteamApp, None, None]:
        with self._lock:
            table = self._db["apps"]  # type: ignore[union-attr]
            if unscraped_only and self._scraped_col_exists():
                rows = list(table.rows_where("scraped_ok IS NOT 1"))
            else:
                rows = list(table.rows)
        for row in rows:
            yield SteamApp.from_dict(row)

    def count_apps(self, unscraped_only: bool = False) -> int:
        with self._lock:
            table = self._db["apps"]  # type: ignore[union-attr]
            if unscraped_only and self._scraped_col_exists():
                return table.count_where("scraped_ok IS NOT 1")
            return table.count

    def claim_apps(self, amount: int, timeout_seconds: int = 300) -> list[SteamApp]:
        with self._lock:
            table = self._db["apps"]  # type: ignore[union-attr]
            if "claimed_at" not in {col.name for col in table.columns}:
                table.add_column("claimed_at", int) # type: ignore
            now = int(time.time())
            cutoff = now - timeout_seconds
            cols = {col.name for col in table.columns}
            conditions = []
            params: list = [cutoff]
            if "scraped_ok" in cols:
                conditions.append("scraped_ok IS NOT 1")
            conditions.append("(claimed_at IS NULL OR claimed_at < ?)")
            rows = list(table.rows_where(
                " AND ".join(conditions),
                params,
                limit=amount,
            ))
            ids = [r["appid"] for r in rows]
            if ids:
                self._db.execute(  # type: ignore[union-attr]
                    f"UPDATE apps SET claimed_at = ? WHERE appid IN ({','.join('?' * len(ids))})",
                    [now, *ids],
                )
            return [SteamApp.from_dict(r) for r in rows]

    def save_game_page_info(self, page: GamePage) -> None:
        with self._lock:
            self._db["apps"].upsert(  # type: ignore[union-attr]
                dataclasses.asdict(page),
                pk="appid",  # type: ignore[arg-type]
                alter=True,  # type: ignore[arg-type]
            )

    def save_reviews(self, reviews: list[UserReview]) -> None:
        with self._lock:
            self._db["reviews"].upsert_all(  # type: ignore[union-attr]
                [dataclasses.asdict(r) for r in reviews],
                pk="recommendation_id",  # type: ignore[arg-type]
                alter=True,  # type: ignore[arg-type]
            )

    def get_latest_review_timestamp(self, appid: int) -> int:
        '''
            Gets the timestamp of the newest review entry fo given appid
        '''
        with self._lock:
            row = self._db.execute(
                "SELECT MAX(timestamp_created) FROM reviews WHERE appid = ?", [appid]
            ).fetchone()  # type: ignore
            return row[0] if row and row[0] is not None else 0

    def delete_orphaned_reviews(self) -> int:
        with self._lock:
            result = self._db.execute(
                "DELETE FROM reviews WHERE appid NOT IN (SELECT appid FROM apps)"
            )  # type: ignore
            return result.rowcount

    def mark_reviews_done(self, appid: int) -> None:
        '''
            A row has reviews_scraped = 1 only if the client reported to have worked trough all chunks.
            0 means the last chunk was not reached, hence the reviews to be incomplete"
        '''
        
        with self._lock:
            self._db.execute("UPDATE apps SET reviews_scraped = 1 WHERE appid = ?", [appid])  # type: ignore


_db: Optional[Database] = None


def init(path: str) -> None:
    global _db
    _db = Database(path)


def get_db() -> Database:
    return _db  # type: ignore[return-value]
