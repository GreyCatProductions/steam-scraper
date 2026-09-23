import argparse
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import List

import requests
from tqdm import tqdm

from playercount_fetching.schema.appPlayerCount import AppPlayerCount
from playercount_fetching.src.steam_db_scraper import get_player_numbers, REQUEST_COOLDOWN_SECONDS

DB_TIMEOUT_SECONDS = 30

CREATE_PLAYERCOUNTS_TABLE = """
    CREATE TABLE IF NOT EXISTS playercounts (
        appid       INTEGER NOT NULL,
        timestamp   INTEGER NOT NULL,
        playercount INTEGER NOT NULL,
        PRIMARY KEY (appid, timestamp)
    )
"""

UPSERT_PLAYERCOUNT = """
    INSERT INTO playercounts (appid, timestamp, playercount) VALUES (?, ?, ?)
    ON CONFLICT (appid, timestamp) DO UPDATE SET playercount = excluded.playercount
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="Steam Historical Player count fetcher",
        description="Scrape all available steam player numbers and save into sqlite table",
    )
    parser.add_argument("db", type=Path, help="SQLite database file containing the apps table")
    args = parser.parse_args()

    if not args.db.is_file():
        parser.error(f"database file not found: {args.db}")
    return args


def load_appids(db: Path) -> list[int]:
    """Reads all valid appids from the apps table."""
    with closing(sqlite3.connect(db, timeout=DB_TIMEOUT_SECONDS)) as conn:
        return [row[0] for row in conn.execute("SELECT appid FROM apps WHERE appid != 0 ORDER BY appid")]


def fetch_playercounts(appids: list[int]) -> list[AppPlayerCount]:
    """Fetches the player count history for every app. Apps whose request fails are kept with no entries."""
    playercounts = [AppPlayerCount(appid) for appid in appids]

    for app in tqdm(playercounts, desc="Fetching player counts", unit="app"):
        try:
            app.entries = get_player_numbers(app_id=app.appid)
        except requests.RequestException as e:
            tqdm.write(f"appid {app.appid} failed: {e}")
        time.sleep(REQUEST_COOLDOWN_SECONDS)

    return playercounts


def save_playercounts(db: Path, playercounts: list[AppPlayerCount]) -> int:
    """Upserts all entries into the playercounts table in one transaction. Returns the number of rows written."""
    rows = [(app.appid, e.timestamp, e.playercount) for app in playercounts for e in app.entries]

    with closing(sqlite3.connect(db, timeout=DB_TIMEOUT_SECONDS)) as conn:
        with conn:
            conn.execute(CREATE_PLAYERCOUNTS_TABLE)
            conn.executemany(UPSERT_PLAYERCOUNT, rows)
    return len(rows)


def main():
    args = parse_args()

    try:
        print(f"Reading appids from {args.db}")
        appids: List[int] = load_appids(args.db)

        playercounts: List[AppPlayerCount] = fetch_playercounts(appids)

        saved: int = save_playercounts(args.db, playercounts)
        print(f"Saved {saved} player count entries")

    except sqlite3.Error as e:
        raise SystemExit(f"database error ({args.db}): {e}")


if __name__ == "__main__":
    main()
