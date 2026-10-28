import logging
import sqlite3
import threading
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

from fastapi import FastAPI, HTTPException

from db_server.src.database import next_backup_path

log = logging.getLogger(__name__)

DB_TIMEOUT_SECONDS = 30


class BackupController:
    """Lets the API take a backup while the fetch loop runs. The fetch loop holds `lock` whenever it touches the
    database, so a backup taken under the same lock pauses it at the next app and sees a consistent file.
    Apps fetched but not saved yet are flushed first, so the backup holds everything fetched so far."""

    def __init__(self, backup: Callable[[], Path]):
        self.lock = threading.RLock()
        self._backup = backup
        self._flush: Callable[[], None] | None = None

    def set_flush(self, flush: Callable[[], None] | None) -> None:
        """Registers the fetch loop's save for its unsaved batch. Call under `lock`."""
        self._flush = flush

    def backup(self) -> Path:
        with self.lock:
            log.info("Backup requested, pausing fetching")
            if self._flush:
                self._flush()
            dest = self._backup()
        log.info("Backed up to %s, resuming fetching", dest)
        return dest


def backup_file(src: Path, backup_dir: Path) -> Path:
    """Copies the whole sqlite file, schema and progress included, with sqlite's online backup."""
    if not src.is_file():
        raise FileNotFoundError(f"nothing to back up yet, {src} doesn't exist")
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = next_backup_path(backup_dir, src.stem)
    with closing(sqlite3.connect(src, timeout=DB_TIMEOUT_SECONDS)) as source, closing(sqlite3.connect(dest)) as target:
        source.backup(target)
    return dest


def backup_tables(src: Path, tables: list[str], backup_dir: Path) -> Path:
    """Copies only the given tables into a new sqlite file, leaving out the rest of a shared database."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = next_backup_path(backup_dir, f"{src.stem}_playercounts")
    with closing(sqlite3.connect(src, timeout=DB_TIMEOUT_SECONDS)) as conn:
        conn.execute("ATTACH DATABASE ? AS backup_dst", (str(dest),))
        with conn:
            for table in tables:
                conn.execute(f"CREATE TABLE backup_dst.{table} AS SELECT * FROM {table}")
        conn.execute("DETACH DATABASE backup_dst")
    return dest


def create_app(controller: BackupController) -> FastAPI:
    app = FastAPI()

    @app.get("/")
    def root():
        return {"status": "ok"}

    @app.post("/backup")
    def backup():
        """
        Pauses fetching, saves the apps fetched so far, copies the player counts into a new SQLite file
        in the backup directory, then continues fetching.
        """
        try:
            dest = controller.backup()
        except (sqlite3.Error, OSError) as e:
            log.exception("Backup failed")
            raise HTTPException(status_code=500, detail=f"backup failed: {e}")
        return {"backup": str(dest)}

    return app
