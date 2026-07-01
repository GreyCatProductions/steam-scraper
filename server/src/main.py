import argparse
import json
import logging
import threading
import time
import uvicorn
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from server.src.appListBuilder import fetch_all_apps
from shared.schema.data_objects import SteamApp
import server.src.database as database
from server.src.api import app
from scripts.weekly_reset import reset as weekly_reset

log = logging.getLogger(__name__)


def setup_logging(log_file: str = "logs/server.log") -> None:
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


def fill_app_entries(args: argparse.Namespace):
    def iter_pages_from_json(path: str):
        with open(path, encoding="utf-8") as f:
            yield json.load(f)

    pages = iter_pages_from_json(args.app_list) if args.app_list else fetch_all_apps(args.key)
    total = 0
    for page in pages:
        apps = [SteamApp.from_dict(a) for a in page]
        database.get_db().add_apps(apps)
        total += len(apps)
    log.info("%d apps loaded into %s", total, args.output)


def seconds_until_next_monday() -> float:
    now = datetime.now()
    days_ahead = 7 - now.weekday()
    next_monday = (now + timedelta(days=days_ahead)).replace(hour=0, minute=0, second=0, microsecond=0)
    return (next_monday - now).total_seconds()


def weekly_cycle(args: argparse.Namespace) -> None:
    CHECK_INTERVAL = 300

    while True:
        cycle_start = time.time()
        initial_remaining = database.get_db().count_apps(unscraped_only=True)

        while True:
            total = database.get_db().count_apps()
            remaining = database.get_db().count_apps(unscraped_only=True)
            if remaining == 0:
                break
            elapsed = time.time() - cycle_start
            scraped = initial_remaining - remaining
            if scraped > 0:
                rate = scraped / elapsed
                eta_seconds = remaining / rate
                eta = timedelta(seconds=int(eta_seconds))
                log.info("Progress: %d/%d scraped | %d remaining | rate: %.0f apps/hr | ETA: %s",
                         total - remaining, total, remaining, rate * 3600, eta)
            else:
                log.info("Progress: %d/%d scraped | %d remaining | ETA: calculating...",
                         total - remaining, total, remaining)
            time.sleep(CHECK_INTERVAL)

        wait = seconds_until_next_monday()
        wake = datetime.now() + timedelta(seconds=wait)
        log.info("All apps scraped. Next cycle: %s (%.1fh from now)", wake.strftime("%Y-%m-%d %H:%M"), wait / 3600)
        time.sleep(wait)

        log.info("Starting weekly reset...")
        weekly_reset(db=args.output)
        database.init(args.output)
        fill_app_entries(args)
        removed = database.get_db().delete_orphaned_reviews()
        if removed:
            log.info("Removed %d reviews for apps no longer on Steam", removed)
        log.info("Weekly cycle started.")


def main():
    parser = argparse.ArgumentParser(prog="Steam scraper", description="Scrape Steam app list into SQLite")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("-k", "--key", help="Steam Web API key (fetches live data)")
    source.add_argument("-al", "--app-list", help="Path to existing app list JSON file. If file is given only apps from the list are scraped. No new ones are searched for using API!")
    parser.add_argument("-sapf", "--skip-app-list-fetch", action="store_true", help="Skip the initial app list fetch")
    parser.add_argument("-o", "--output", default="steam.db", help="SQLite database file path")
    parser.add_argument("-p", "--port", type=int, default=8000, help="Port to listen on")
    parser.add_argument("--reset", action="store_true", help="Back up and wipe the apps table before starting (use at the start of a new weekly cycle)")
    parser.add_argument("--log-file", default="logs/server.log", help="Log file path (rotates at 10MB, keeps 5 backups)")
    args = parser.parse_args()

    setup_logging(args.log_file)

    if args.reset:
        weekly_reset(db=args.output)

    database.init(args.output)

    if not args.skip_app_list_fetch:
        fill_app_entries(args)

    threading.Thread(
        target=uvicorn.run,
        kwargs={"app": app, "host": "0.0.0.0", "port": args.port},
        daemon=True,
    ).start()

    weekly_cycle(args)


if __name__ == "__main__":
    main()
