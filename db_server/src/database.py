import dataclasses
import logging
import random
import sqlite3
import threading
import time
from datetime import date
from pathlib import Path
from typing import Generator, Optional
import sqlite_utils
from shared.schema.data_objects import SteamApp
from shared.schema.review import UserReview
from shared.schema.steamPage import GamePage

log = logging.getLogger(__name__)

FAILURE_THRESHOLD = 10


def next_backup_path(backup_dir: Path, stem: str) -> Path:
    today = date.today().isoformat()
    counter = 1
    while True:
        path = backup_dir / f"{stem}_{today}_{counter:03d}.db"
        if not path.exists():
            return path
        counter += 1


class Database:
    def __init__(self, path: str):
        self._path = path
        conn = sqlite3.connect(path, check_same_thread=False)
        self._db = sqlite_utils.Database(conn)
        self._lock = threading.Lock()
        self._db.execute("PRAGMA journal_mode=WAL")  # type: ignore
        self._db.execute("PRAGMA synchronous=NORMAL")  # type: ignore
        self._db.execute("PRAGMA cache_size=-64000")  # type: ignore
        self._db.execute("PRAGMA temp_store=MEMORY")  # type: ignore
        self._db.execute("PRAGMA mmap_size=1024000000")  # type: ignore
        self._db["apps"].create({"appid": int}, pk="appid", if_not_exists=True)  # type: ignore
        self._db["reviews"].create(  # type: ignore
            {"recommendation_id": int, "appid": int, "timestamp_created": int},
            pk="recommendation_id",  # type: ignore
            if_not_exists=True,
        )
        log.info("Ensuring indexes exist (may take a while on large DBs)...")

        cols = {col.name for col in self._db["apps"].columns}  # type: ignore
        if "reviews_scraped" not in cols:
            self._db["apps"].add_column("reviews_scraped", int)  # type: ignore
        if "scraped_ok" not in cols:
            self._db["apps"].add_column("scraped_ok", int)  # type: ignore
        if "fail_count" not in cols:
            self._db["apps"].add_column("fail_count", int)  # type: ignore
        if "claimed_at" not in cols:
            self._db["apps"].add_column("claimed_at", int)  # type: ignore
        review_cols = {col.name for col in self._db["reviews"].columns}  # type: ignore
        if "last_seen" not in review_cols:
            self._db["reviews"].add_column("last_seen", int)  # type: ignore

        self._db.execute("CREATE INDEX IF NOT EXISTS idx_reviews_appid ON reviews (appid)")  # type: ignore
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_reviews_appid_ts ON reviews (appid, timestamp_created)")  # type: ignore
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_apps_scraped_ok ON apps (scraped_ok)")  # type: ignore
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_apps_claim ON apps (scraped_ok, claimed_at, appid)")  # type: ignore
        log.info("Indexes ready")

        log.info("Loading db info")
        apps_count = self._db["apps"].count  # type: ignore
        reviews_count = self._db["reviews"].count  # type: ignore
        if apps_count > 0 or reviews_count > 0:
            scraped_count = self._db["apps"].count_where("scraped_ok IS 1") if self._scraped_col_exists() else 0  # type: ignore
            log.info(
                "Opened existing database at %s: %d apps (%d scraped), %d reviews",
                path, apps_count, scraped_count, reviews_count,
            )

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
                return table.count_where("scraped_ok IS NOT 1 AND scraped_ok IS NOT -1")
            return table.count

    def claim_apps(self, amount: int, timeout_seconds: int = 300) -> list[SteamApp]:
        '''
            Claims a batch of unscraped apps, or apps whose claim has expired,
            and marks them as claimed for timeout_seconds.
            Selection starts from a randomly chosen appid and wraps around the
            table, which spreads claims out without paying for an ORDER BY
            RANDOM() sort of the whole filtered result set on every call.
        '''
        with self._lock:
            table = self._db["apps"]  # type: ignore[union-attr]
            now = int(time.time())
            cutoff = now - timeout_seconds
            where = "scraped_ok IS NOT 1 AND scraped_ok IS NOT -1 AND (claimed_at IS NULL OR claimed_at < ?)"

            bounds = self._db.execute("SELECT MIN(appid), MAX(appid) FROM apps").fetchone()  # type: ignore
            rows: list[dict] = []
            if bounds and bounds[0] is not None:
                start = random.randint(bounds[0], bounds[1])
                rows = list(table.rows_where(
                    f"appid >= ? AND {where}",
                    [start, cutoff],
                    order_by="appid",
                    limit=amount,
                ))
                if len(rows) < amount:
                    rows += list(table.rows_where(
                        f"appid < ? AND {where}",
                        [start, cutoff],
                        order_by="appid",
                        limit=amount - len(rows),
                    ))

            ids = [r["appid"] for r in rows]
            if ids:
                self._db.execute(  # type: ignore[union-attr]
                    f"UPDATE apps SET claimed_at = ? WHERE appid IN ({','.join('?' * len(ids))})",
                    [now, *ids],
                )
            return [SteamApp.from_dict(r) for r in rows]

    def report_failure(self, appid: int) -> None:
        '''
            Records a client-side processing failure for an app. Once fail_count reaches
            FAILURE_THRESHOLD, the app is marked scraped_ok=-1 so it's no longer claimable
            or counted as remaining work.
        '''
        with self._lock:
            self._db.execute(  # type: ignore[union-attr]
                """
                UPDATE apps
                SET fail_count = COALESCE(fail_count, 0) + 1,
                    scraped_ok = CASE
                        WHEN COALESCE(fail_count, 0) + 1 >= ? THEN -1
                        ELSE scraped_ok
                    END
                WHERE appid = ?
                """,
                [FAILURE_THRESHOLD, appid],
            )

    def save_game_page_info(self, page: GamePage) -> None:
        with self._lock:
            self._db["apps"].upsert(  # type: ignore[union-attr]
                dataclasses.asdict(page),
                pk="appid",  # type: ignore[arg-type]
                alter=True,  # type: ignore[arg-type]
            )

    def save_reviews(self, reviews: list[UserReview]) -> None:
        '''
            Reviews are reused across weeks, never wiped by the weekly reset.
            Re-scraping an already-known review (same recommendation_id) just overwrites
            its fields and stamps last_seen, rather than creating a duplicate row.
        '''
        now = int(time.time())
        with self._lock:
            rows = [dict(dataclasses.asdict(r), last_seen=now) for r in reviews]
            self._db["reviews"].upsert_all(  # type: ignore[union-attr]
                rows,
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

    def mark_reviews_done(self, appid: int) -> None:
        '''
            A row has reviews_scraped = 1 only if the client reported to have worked trough all chunks.
            0 means the last chunk was not reached, hence the reviews to be incomplete"
        '''

        with self._lock:
            self._db.execute("UPDATE apps SET reviews_scraped = 1 WHERE appid = ?", [appid])  # type: ignore

    def _snapshot_table_locked(self, table: str, stem: str, backup_dir: str) -> Path:
        '''
            Copies `table` into a new attached db file over the server's own connection,
            so it competes for the write lock with nothing but itself.
        '''
        bdir = Path(backup_dir)
        bdir.mkdir(exist_ok=True)
        dest = next_backup_path(bdir, stem)
        conn = self._db.conn  # type: ignore
        conn.execute("ATTACH DATABASE ? AS backup_dst", (str(dest),))  # type: ignore
        try:
            conn.execute(f"CREATE TABLE backup_dst.{table} AS SELECT * FROM {table}")  # type: ignore
            conn.commit()  # type: ignore
        finally:
            conn.execute("DETACH DATABASE backup_dst")  # type: ignore
        return dest

    def backup_apps_table(self, backup_dir: str = "backups") -> Path:
        with self._lock:
            return self._snapshot_table_locked("apps", Path(self._path).stem, backup_dir)

    def backup_reviews_table(self, backup_dir: str = "backups") -> Path:
        with self._lock:
            return self._snapshot_table_locked("reviews", f"{Path(self._path).stem}_reviews", backup_dir)

    def reset_apps(self, backup_dir: str = "backups") -> Path:
        '''
            Weekly reset: backs up and clears the apps table. Reviews are reused across
            weeks and untouched here. Backup and delete share the server's own connection
            and a single lock acquisition, so no in-flight request can interleave with it.
        '''
        with self._lock:
            dest = self._snapshot_table_locked("apps", Path(self._path).stem, backup_dir)
            self._db.execute("DELETE FROM apps")  # type: ignore
            self._db.conn.commit()  # type: ignore
            return dest


_db: Optional[Database] = None
_path: str = ""


def init(path: str) -> None:
    global _db, _path
    _path = path
    _db = Database(path)


def get_db() -> Database:
    return _db  # type: ignore[return-value]


def get_path() -> str:
    return _path
