import argparse
import os
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from functools import partial
from pathlib import Path

import requests
from dotenv import load_dotenv
from tqdm import tqdm

from playercount_fetching.schema.appPlayerCount import AppPlayerCount
from playercount_fetching.src.playercount_file import PlayercountFile
from playercount_fetching.src.proxy_pool import ProxyPool
from playercount_fetching.src.steam_db_scraper import get_player_numbers, REQUEST_COOLDOWN_SECONDS_MIN, REQUEST_COOLDOWN_SECONDS_MAX
from processing_server.src.appListBuilder import fetch_all_apps

DB_TIMEOUT_SECONDS = 30
BATCH_SIZE = 100
DEFAULT_OUTPUT = Path("playercounts.sqlite")
APP_LIST_RETRY_SECONDS = 300

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
                   save: Callable[[list[AppPlayerCount]], int]) -> None:
    """Fetches every app and saves in batches. Failed apps are not saved, so the next run retries them.
    Whatever is fetched is saved in batch size"""
    batch: list[AppPlayerCount] = []
    failed: list[int] = []
    with_data = no_data = saved_rows = 0

    progress = tqdm(appids, desc="Fetching player counts", unit="app")
    try:
        for appid in progress:
            try:
                app = AppPlayerCount(appid, get_player_numbers(app_id=appid, pool=pool))
            except Exception as e:
                failed.append(appid)
                progress.set_postfix(failed=len(failed))
                tqdm.write(f"appid {appid} failed: {e!r}")
            else:
                batch.append(app)
                if app.entries:
                    with_data += 1
                else:
                    no_data += 1

            if len(batch) >= batch_size:
                saved_rows += save(batch)
                batch.clear()

    finally:
        if batch:
            saved_rows += save(batch)
        progress.close()

        print(f"Processed {with_data + no_data + len(failed)} of {len(appids)} apps:")
        print(f"  succeeded with data: {with_data}")
        print(f"  not tracked by steamcharts: {no_data}")
        print(f"  failed (retried on next run or round): {len(failed)}")
        if failed:
            print(f"  failed appids: {', '.join(map(str, failed))}")
        print(f"Saved {saved_rows} player count entries")


def main():
    args = parse_args()

    try:
        pool = (ProxyPool.from_file(args.proxies, args.cooldown, own_ip=not args.no_own_ip) if args.proxies
                else ProxyPool.direct(args.cooldown))
    except ValueError as e:
        raise SystemExit(f"proxy file error ({args.proxies}): {e}")
    cooldown = f"{args.cooldown[0]}-{args.cooldown[1]}s cooldown each"
    if not args.proxies:
        print(f"Using own IP. No proxy, {cooldown}")
    elif args.no_own_ip:
        print(f"Rotating through {len(pool)} proxies, {cooldown}")
    else:
        print(f"Rotating through {len(pool) - 1} proxies plus own IP, {cooldown}")

    restart = args.restart
    round_number = 1
    try:
        while True:
            if args.loop:
                print(f"=== Round {round_number} ===")
            try:
                if args.db:
                    run_db_round(args, pool, restart)
                else:
                    run_file_round(args, pool, restart)
            except requests.RequestException as e:
                message = f"Steam app list error: {e}"
                if not args.loop:
                    raise SystemExit(message)
                print(f"{message}; retrying in {APP_LIST_RETRY_SECONDS}s")
                time.sleep(APP_LIST_RETRY_SECONDS)
                continue

            if not args.loop:
                break

            restart = True
            round_number += 1
            if args.loop_pause:
                print(f"Waiting {args.loop_pause}s before the next round")
                time.sleep(args.loop_pause)

    except sqlite3.Error as e:
        raise SystemExit(f"database error ({args.db or args.output}): {e}")
    except OSError as e:
        raise SystemExit(f"output file error ({args.output}): {e}")
    except KeyboardInterrupt:
        raise SystemExit("interrupted, progress saved; rerun the same command to continue")


def run_db_round(args: argparse.Namespace, pool: ProxyPool, restart: bool) -> None:
    init_tables(args.db)
    appids = load_pending_appids(args.db, restart)
    print(f"{len(appids)} apps to fetch from {args.db}")
    fetch_and_save(appids, args.batch_size, pool, partial(save_batch, args.db))


def run_file_round(args: argparse.Namespace, pool: ProxyPool, restart: bool) -> None:
    # fetch the app list before touching the output, so a failed request can't discard progress
    all_appids = fetch_steam_appids(args.key)
    store = PlayercountFile(args.output)
    last_saved = store.start_round(restart)
    # apps that failed before the resume point are retried in the next round
    appids = [appid for appid in all_appids if appid > last_saved]
    resumed = f", resuming after appid {last_saved}" if last_saved else ""
    print(f"{len(appids)} of {len(all_appids)} Steam apps to fetch{resumed}")
    fetch_and_save(appids, args.batch_size, pool, store.save_batch)
    store.finish_round()
    print(f"Round complete, saved into {args.output}")


if __name__ == "__main__":
    main()
