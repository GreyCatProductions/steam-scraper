import argparse
import random
import sqlite3
import sys
import tempfile
import time
import csv
from pathlib import Path
from typing import Callable
from playwright.sync_api import sync_playwright, Page
from tqdm import tqdm

CDP_URL = "http://127.0.0.1:9222"  # not "localhost" - that can resolve to ::1 (IPv6) on Windows,
CLICK_TIMEOUT_MS = 5000
PREFLIGHT_APPID = 730  # Counter-Strike 2 - always has chart history, a good canary for access issues

MIN_REQUEST_DELAY = 5.0
MAX_REQUEST_DELAY = 10.0

CLOUDFLARE_TITLE_MARKER = "Just a moment"
CLOUDFLARE_COOLDOWN_SECONDS = 60
CLOUDFLARE_MAX_RETRIES = 3

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
                int(row["Players"]) if row.get("Players") else None,
                float(row["Average Players"]) if row.get("Average Players") else None,
            )
            for row in csv.DictReader(f)
        ]


def _goto(page: Page, url: str, status: Callable[[str], None]) -> None:
    '''
        Navigates, retrying through Cloudflare's "Just a moment..." interstitial if it shows up.
        Even with a real, logged-in browser this can still appear occasionally - waiting it out
        beats failing the appid outright over something that usually clears on its own.
    '''
    for attempt in range(CLOUDFLARE_MAX_RETRIES + 1):
        page.goto(url)
        if CLOUDFLARE_TITLE_MARKER not in page.title():
            return
        if attempt == CLOUDFLARE_MAX_RETRIES:
            raise RuntimeError(f"Cloudflare check did not clear after {CLOUDFLARE_MAX_RETRIES} retries")
        status(f"Cloudflare check detected, waiting {CLOUDFLARE_COOLDOWN_SECONDS}s "
               f"before retry {attempt + 1}/{CLOUDFLARE_MAX_RETRIES}...")
        time.sleep(CLOUDFLARE_COOLDOWN_SECONDS)


def _check_access(page: Page) -> None:
    '''
        Fails fast, once, before the batch loop. Without this, a missing login (or any other
        precondition that blocks every appid identically, not just one) would otherwise time out
        silently on every single appid in the list - potentially thousands of 5s timeouts before
        anyone notices zero rows got saved.
    '''
    _goto(page, f"https://steamdb.info/app/{PREFLIGHT_APPID}/charts/#max", print)
    try:
        page.locator("image.highcharts-button-symbol").wait_for(timeout=CLICK_TIMEOUT_MS)
    except Exception as e:
        sys.exit(
            f"Could not find the chart export button on a known-good appid ({PREFLIGHT_APPID}).\n"
            "You're probably not logged in to steamdb.info in the Chrome window this script is "
            "attached to (or the page layout changed). Log in there, then re-run this script.\n"
            f"({e})"
        )


def _download_player_counts(
    page: Page, appid: int, status: Callable[[str], None]
) -> list[tuple[str, int | None, float | None]]:
    status(f"appid {appid}: opening chart page")
    _goto(page, f"https://steamdb.info/app/{appid}/charts/#max", status)

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
            print("Checking access (login, page layout)...")
            _check_access(page)
            print("Access confirmed.")

            pbar = tqdm(appids, desc="Fetching player counts")
            for appid in pbar:
                try:
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
                    # Space requests out so this doesn't look like a scraping burst - runs
                    # whether the appid succeeded, failed, or hit `continue` above.
                    time.sleep(random.uniform(MIN_REQUEST_DELAY, MAX_REQUEST_DELAY))
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
