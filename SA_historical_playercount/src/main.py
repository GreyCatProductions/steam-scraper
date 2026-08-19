import argparse
import sqlite3
import sys
import tempfile
import csv
from pathlib import Path
from typing import Callable
from playwright.sync_api import sync_playwright, Page
from tqdm import tqdm

CDP_URL = "http://localhost:9222"

CLICK_TIMEOUT_MS = 5000

def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS player_counts (
            appid INTEGER NOT NULL,
            datetime TEXT NOT NULL,
            players INTEGER,
            average_players REAL,
            PRIMARY KEY (appid, datetime)
        )
        """
    )


def _parse_player_count_csv(path: Path) -> list[tuple[str, int | None, float | None]]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [
            (
                row["DateTime"],
                int(row["Players"]) if row["Players"] else None,
                float(row["Average Players"]) if row["Average Players"] else None,
            )
            for row in csv.DictReader(f)
        ]


def _download_player_counts(
    page: Page, appid: int, status: Callable[[str], None]
) -> list[tuple[str, int | None, float | None]]:
    status(f"appid {appid}: opening chart page")
    page.goto(f"https://steamdb.info/app/{appid}/charts/#max")

    status(f"appid {appid}: opening export menu")
    icon = page.locator("image.highcharts-button-symbol")
    icon.hover(timeout=CLICK_TIMEOUT_MS)
    icon.click(timeout=CLICK_TIMEOUT_MS)

    csv_item = page.locator("li.highcharts-menu-item", has_text="Download CSV")
    csv_item.hover(timeout=CLICK_TIMEOUT_MS)

    status(f"appid {appid}: downloading CSV")
    with page.expect_download() as download_info:
        csv_item.click(timeout=CLICK_TIMEOUT_MS)
    download = download_info.value

    status(f"appid {appid}: parsing CSV")
    with tempfile.TemporaryDirectory() as tmp_dir:
        csv_path = Path(tmp_dir) / "download.csv"
        download.save_as(csv_path)
        return _parse_player_count_csv(csv_path)


def run(db_path: str) -> None:
    print(f"Opening database: {db_path}")
    conn = sqlite3.connect(db_path)
    _ensure_table(conn)

    print("Checking which appids still need fetching...")
    appids = [
        row[0] for row in conn.execute(
            "SELECT appid FROM apps WHERE appid NOT IN (SELECT DISTINCT appid FROM player_counts)"
        ).fetchall()
    ]
    print(f"{len(appids)} appids to fetch")

    print(f"Connecting to browser at {CDP_URL}...")
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP_URL)
        except Exception as e:
            sys.exit(
                f"Could not connect to a browser at {CDP_URL}: {e}\n\n"
                "Close every other Chrome window first (they lock the profile), then start it "
                "with remote debugging enabled:\n"
                "  google-chrome --remote-debugging-port=9222\n"
                "Log in to steamdb.info as you normally would, then re-run this script."
            )
        print("Connected.")

        context = browser.contexts[0]
        page = context.new_page()
        try:
            pbar = tqdm(appids, desc="Fetching player counts")
            for appid in pbar:
                try:
                    rows = _download_player_counts(page, appid, pbar.set_description)
                except Exception as e:
                    tqdm.write(f"  Skipping appid {appid}: {e}")
                    continue
                pbar.set_description(f"appid {appid}: saving {len(rows)} rows")
                conn.executemany(
                    "INSERT OR REPLACE INTO player_counts (appid, datetime, players, average_players) "
                    "VALUES (?, ?, ?, ?)",
                    [(appid, dt, players, avg) for dt, players, avg in rows],
                )
                conn.commit()
        finally:
            page.close()

    conn.close()
    print("Done.")


def main():
    parser = argparse.ArgumentParser(description="Extend a steam-scraper database with historical player counts")
    parser.add_argument("db", help="Path to the steam-scraper .db file to extend")
    args = parser.parse_args()

    run(args.db)


if __name__ == "__main__":
    main()
