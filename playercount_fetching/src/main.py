import argparse
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from tqdm import tqdm

from playercount_fetching.schema.appPlayerCount import AppPlayerCount
from playercount_fetching.src.proxy_pool import ProxyPool
from playercount_fetching.src.steam_db_scraper import get_player_numbers, REQUEST_COOLDOWN_SECONDS_MIN, REQUEST_COOLDOWN_SECONDS_MAX

DB_TIMEOUT_SECONDS = 30
BATCH_SIZE = 100

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
        description="Scrape all available steam player numbers and save into sqlite table. "
                    "Saves every --batch-size apps and resumes where a previous run stopped.",
    )
    parser.add_argument("db", type=Path, help="SQLite database file containing the apps table")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE, help=f"apps per save (default {BATCH_SIZE})")
    parser.add_argument("--restart", action="store_true", help="refetch all apps, ignoring previous progress")
    parser.add_argument("--proxies", type=Path,
                        help="file with one proxy per line (e.g. USER:PASSWORD@IP:HTTP_PORT), rotated on every request")
    parser.add_argument("--cooldown", type=float, nargs=2, metavar=("MIN", "MAX"),
                        default=(REQUEST_COOLDOWN_SECONDS_MIN, REQUEST_COOLDOWN_SECONDS_MAX),
                        help="seconds each proxy (or our own IP) rests after a request, picked randomly between MIN and MAX "
                             f"(default {REQUEST_COOLDOWN_SECONDS_MIN} {REQUEST_COOLDOWN_SECONDS_MAX})")
    args = parser.parse_args()

    if not args.db.is_file():
        parser.error(f"database file not found: {args.db}")
    if args.proxies and not args.proxies.is_file():
        parser.error(f"proxy file not found: {args.proxies}")
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


def fetch_and_save(db: Path, appids: list[int], batch_size: int, pool: ProxyPool) -> None:
    """Fetches every app and saves in batches. Failed apps are not marked as fetched, so the next run retries them.
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
                saved_rows += save_batch(db, batch)
                batch.clear()

    finally:
        if batch:
            saved_rows += save_batch(db, batch)
        progress.close()

        print(f"Processed {with_data + no_data + len(failed)} of {len(appids)} apps:")
        print(f"  succeeded with data: {with_data}")
        print(f"  not tracked by steamcharts: {no_data}")
        print(f"  failed (retried on next run): {len(failed)}")
        if failed:
            print(f"  failed appids: {', '.join(map(str, failed))}")
        print(f"Saved {saved_rows} player count entries")


def main():
    args = parse_args()

    try:
        pool = ProxyPool.from_file(args.proxies, args.cooldown) if args.proxies else ProxyPool.direct(args.cooldown)
    except ValueError as e:
        raise SystemExit(f"proxy file error ({args.proxies}): {e}")
    cooldown = f"{args.cooldown[0]}-{args.cooldown[1]}s cooldown each"
    print(f"Rotating through {len(pool)} proxies, {cooldown}" if args.proxies else f"Using own IP. No proxy, {cooldown}")

    try:
        init_tables(args.db)
        appids = load_pending_appids(args.db, args.restart)
        print(f"{len(appids)} apps to fetch from {args.db}")
        fetch_and_save(args.db, appids, args.batch_size, pool)

    except sqlite3.Error as e:
        raise SystemExit(f"database error ({args.db}): {e}")
    except KeyboardInterrupt:
        raise SystemExit("interrupted, progress saved; rerun the same command to continue")


if __name__ == "__main__":
    main()
