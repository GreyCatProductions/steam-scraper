import argparse
import logging
import os
import sqlite3
import threading
import time
from collections.abc import Callable
from contextlib import closing
from functools import partial
from logging.handlers import RotatingFileHandler
from pathlib import Path

import requests
import uvicorn
from dotenv import load_dotenv
from tqdm import tqdm
from tqdm.contrib.logging import logging_redirect_tqdm

from playercount_fetching.schema.appPlayerCount import AppPlayerCount
from playercount_fetching.src.api import BackupController, backup_file, backup_tables, create_app
from playercount_fetching.src.playercount_file import PlayercountFile
from playercount_fetching.src.proxy_pool import ProxyPool
from playercount_fetching.src.steam_db_scraper import get_player_numbers, REQUEST_COOLDOWN_SECONDS_MIN, REQUEST_COOLDOWN_SECONDS_MAX
from processing_server.src.appListBuilder import fetch_all_apps

log = logging.getLogger(__name__)

DB_TIMEOUT_SECONDS = 30
BATCH_SIZE = 100
DEFAULT_OUTPUT = Path("playercounts.sqlite")
APP_LIST_RETRY_SECONDS = 300
DEFAULT_PORT = 8002

CREATE_PLAYERCOUNTS_TABLE = """
    CREATE TABLE IF NOT EXISTS playercounts (
        appid       INTEGER NOT NULL,
        timestamp   INTEGER NOT NULL,
        playercount INTEGER NOT NULL,
        PRIMARY KEY (appid, timestamp)
    )
"""

CREATE_FETCHED_TABLE = """
    CREATE TABLE IF NOT EXISTS playercounts_fetched (
        appid       INTEGER PRIMARY KEY,
        fetched_at  INTEGER NOT NULL
    )
"""

UPSERT_PLAYERCOUNT = """
    INSERT INTO playercounts (appid, timestamp, playercount) VALUES (?, ?, ?)
    ON CONFLICT (appid, timestamp) DO UPDATE SET playercount = excluded.playercount
"""

UPSERT_FETCHED = """
    INSERT INTO playercounts_fetched (appid, fetched_at) VALUES (?, ?)
    ON CONFLICT (appid) DO UPDATE SET fetched_at = excluded.fetched_at
"""


def setup_logging(log_file: str = "logs/playercount_fetching.log") -> None:
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(), file_handler],
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="Steam Historical Player count fetcher",
        description="Scrape all available steam player numbers and save into a sqlite table, or without a database, "
                    "fetch the appids from the Steam API and save into a standalone single-table sqlite file. "
                    "Saves every --batch-size apps and resumes where a previous run stopped.",
    )
    parser.add_argument("db", type=Path, nargs="?",
                        help="SQLite database file containing the apps table. If omitted, the appids are fetched "
                             "from the Steam API and the results saved to --output")
    parser.add_argument("--output", type=Path,
                        help=f"sqlite file for the results when no db is given (default {DEFAULT_OUTPUT}), "
                             "created if missing, with the single table playercounts(appid, timestamp, playercount)")
    parser.add_argument("--key", help="Steam Web API key for fetching the appids (default: API_KEY from .env)")
    parser.add_argument("--loop", action="store_true",
                        help="run indefinitely: once all apps are fetched, start over and refetch all of them")
    parser.add_argument("--loop-pause", type=float, default=0, metavar="SECONDS",
                        help="seconds to wait between loop rounds (default 0)")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE, help=f"apps per save (default {BATCH_SIZE})")
    parser.add_argument("--restart", action="store_true", help="refetch all apps, ignoring previous progress")
    parser.add_argument("--proxies", type=Path,
                        help="file with one proxy per line (e.g. USER:PASSWORD@IP:HTTP_PORT), rotated on every request "
                             "together with our own IP")
    parser.add_argument("--no-own-ip", action="store_true",
                        help="with --proxies, only use the proxies and leave our own IP out of the rotation")
    parser.add_argument("--cooldown", type=float, nargs=2, metavar=("MIN", "MAX"),
                        default=(REQUEST_COOLDOWN_SECONDS_MIN, REQUEST_COOLDOWN_SECONDS_MAX),
                        help="seconds each proxy (or our own IP) rests after a request, picked randomly between MIN and MAX "
                             f"(default {REQUEST_COOLDOWN_SECONDS_MIN} {REQUEST_COOLDOWN_SECONDS_MAX})")
    parser.add_argument("--host", default="127.0.0.1",
                        help="address the control API listens on (default 127.0.0.1, use 0.0.0.0 for remote access)")
    parser.add_argument("-p", "--port", type=int, default=DEFAULT_PORT,
                        help=f"port of the control API, POST /backup pauses, backs up and continues (default {DEFAULT_PORT})")
    parser.add_argument("--backup-dir", type=Path, default=Path("backups"), help="where /backup writes (default backups)")
    parser.add_argument("--log-file", default="logs/playercount_fetching.log",
                        help="Log file path (rotates at 10MB, keeps 5 backups)")
    args = parser.parse_args()

    if args.db:
        if not args.db.is_file():
            parser.error(f"database file not found: {args.db}")
        if args.output:
            parser.error("--output is only used without a db")
    else:
        args.output = args.output or DEFAULT_OUTPUT
        load_dotenv()
        args.key = args.key or os.getenv("API_KEY")
        if not args.key:
            parser.error("without a db, a Steam Web API key is needed to fetch all appIds: pass --key or set API_KEY in .env")
    if args.loop_pause < 0:
        parser.error("--loop-pause can't be negative")
    if args.proxies and not args.proxies.is_file():
        parser.error(f"proxy file not found: {args.proxies}")
    if args.no_own_ip and not args.proxies:
        parser.error("--no-own-ip needs --proxies")
    if not 0 <= args.cooldown[0] <= args.cooldown[1]:
        parser.error("--cooldown needs 0 <= MIN <= MAX")
    return args


def init_tables(db: Path) -> None:
    with closing(sqlite3.connect(db, timeout=DB_TIMEOUT_SECONDS)) as conn:
        with conn:
            conn.execute(CREATE_PLAYERCOUNTS_TABLE)
            conn.execute(CREATE_FETCHED_TABLE)


def load_pending_appids(db: Path, restart: bool) -> list[int]:
    """Reads all valid appids from the apps table, minus those already fetched unless restart is set."""
    query = "SELECT appid FROM apps WHERE appid != 0"
    if not restart:
        query += " AND appid NOT IN (SELECT appid FROM playercounts_fetched)"
    with closing(sqlite3.connect(db, timeout=DB_TIMEOUT_SECONDS)) as conn:
        return [row[0] for row in conn.execute(query + " ORDER BY appid")]


def save_batch(db: Path, batch: list[AppPlayerCount]) -> int:
    """Upserts the entries of a batch and marks its apps as fetched in one transaction. Returns rows written."""
    rows = [(app.appid, e.timestamp, e.playercount) for app in batch for e in app.entries]
    now = int(time.time())

    with closing(sqlite3.connect(db, timeout=DB_TIMEOUT_SECONDS)) as conn:
        with conn:
            conn.executemany(UPSERT_PLAYERCOUNT, rows)
            conn.executemany(UPSERT_FETCHED, [(app.appid, now) for app in batch])
    return len(rows)


def fetch_steam_appids(key: str) -> list[int]:
    """All appids from the Steam API, sorted."""
    return sorted({app["appid"] for page in fetch_all_apps(key) for app in page})


def fetch_and_save(appids: list[int], batch_size: int, pool: ProxyPool,
                   save: Callable[[list[AppPlayerCount]], int], controller: BackupController) -> None:
    """Fetches every app and saves in batches. Failed apps are not saved, so the next run retries them.
    Whatever is fetched is saved in batch size, or earlier when a backup flushes it"""
    batch: list[AppPlayerCount] = []
    failed: list[int] = []
    with_data = no_data = saved_rows = 0

    def flush() -> None:
        nonlocal saved_rows
        if batch:
            saved_rows += save(batch)
            batch.clear()

    with controller.lock:
        controller.set_flush(flush)

    progress = tqdm(appids, desc="Fetching player counts", unit="app")
    try:
        for appid in progress:
            try:
                app = AppPlayerCount(appid, get_player_numbers(app_id=appid, pool=pool))
            except Exception as e:
                failed.append(appid)
                progress.set_postfix(failed=len(failed))
                log.warning("appid %s failed: %r", appid, e)
                continue

            if app.entries:
                with_data += 1
            else:
                no_data += 1

            # blocks here while a backup runs
            with controller.lock:
                batch.append(app)
                if len(batch) >= batch_size:
                    flush()

    finally:
        with controller.lock:
            controller.set_flush(None)
            flush()
        progress.close()

        log.info("Processed %d of %d apps:", with_data + no_data + len(failed), len(appids))
        log.info("  succeeded with data: %d", with_data)
        log.info("  not tracked by steamcharts: %d", no_data)
        log.info("  failed (retried on next run or round): %d", len(failed))
        if failed:
            log.info("  failed appids: %s", ", ".join(map(str, failed)))
        log.info("Saved %d player count entries", saved_rows)


def start_api(args: argparse.Namespace, controller: BackupController) -> None:
    threading.Thread(
        target=uvicorn.run,
        kwargs={"app": create_app(controller), "host": args.host, "port": args.port},
        daemon=True,
    ).start()
    log.info("Control API on http://%s:%d, POST /backup to back up", args.host, args.port)


def main():
    args = parse_args()
    setup_logging(args.log_file)

    try:
        pool = (ProxyPool.from_file(args.proxies, args.cooldown, own_ip=not args.no_own_ip) if args.proxies
                else ProxyPool.direct(args.cooldown))
    except ValueError as e:
        raise SystemExit(f"proxy file error ({args.proxies}): {e}")
    cooldown = f"{args.cooldown[0]}-{args.cooldown[1]}s cooldown each"
    if not args.proxies:
        log.info("Using own IP. No proxy, %s", cooldown)
    elif args.no_own_ip:
        log.info("Rotating through %d proxies, %s", len(pool), cooldown)
    else:
        log.info("Rotating through %d proxies plus own IP, %s", len(pool) - 1, cooldown)

    if args.db:
        backup = partial(backup_tables, args.db, ["playercounts", "playercounts_fetched"], args.backup_dir)
    else:
        backup = partial(backup_file, args.output, args.backup_dir)
    controller = BackupController(backup)
    start_api(args, controller)

    restart = args.restart
    round_number = 1
    try:
        with logging_redirect_tqdm():
            while True:
                if args.loop:
                    log.info("=== Round %d ===", round_number)
                try:
                    if args.db:
                        run_db_round(args, pool, restart, controller)
                    else:
                        run_file_round(args, pool, restart, controller)
                except requests.RequestException as e:
                    if not args.loop:
                        log.error("Steam app list error: %s", e)
                        raise SystemExit(1)
                    log.error("Steam app list error: %s; retrying in %ss", e, APP_LIST_RETRY_SECONDS)
                    time.sleep(APP_LIST_RETRY_SECONDS)
                    continue

                if not args.loop:
                    break

                restart = True
                round_number += 1
                if args.loop_pause:
                    log.info("Waiting %ss before the next round", args.loop_pause)
                    time.sleep(args.loop_pause)

    except sqlite3.Error as e:
        log.error("database error (%s): %s", args.db or args.output, e)
        raise SystemExit(1)
    except OSError as e:
        log.error("output file error (%s): %s", args.output, e)
        raise SystemExit(1)
    except KeyboardInterrupt:
        log.info("interrupted, progress saved; rerun the same command to continue")
        raise SystemExit(1)


def run_db_round(args: argparse.Namespace, pool: ProxyPool, restart: bool, controller: BackupController) -> None:
    with controller.lock:
        init_tables(args.db)
        appids = load_pending_appids(args.db, restart)
    log.info("%d apps to fetch from %s", len(appids), args.db)
    fetch_and_save(appids, args.batch_size, pool, partial(save_batch, args.db), controller)


def run_file_round(args: argparse.Namespace, pool: ProxyPool, restart: bool, controller: BackupController) -> None:
    # fetch the app list before touching the output, so a failed request can't discard progress
    all_appids = fetch_steam_appids(args.key)
    store = PlayercountFile(args.output)
    with controller.lock:
        last_saved = store.start_round(restart)
    # apps that failed before the resume point are retried in the next round
    appids = [appid for appid in all_appids if appid > last_saved]
    resumed = f", resuming after appid {last_saved}" if last_saved else ""
    log.info("%d of %d Steam apps to fetch%s", len(appids), len(all_appids), resumed)
    fetch_and_save(appids, args.batch_size, pool, store.save_batch, controller)
    with controller.lock:
        store.finish_round()
    log.info("Round complete, saved into %s", args.output)


if __name__ == "__main__":
    main()
