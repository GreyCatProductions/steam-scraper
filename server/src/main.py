import argparse
import json
import threading
import time
import uvicorn
from datetime import datetime, timedelta
from server.src.appListBuilder import fetch_all_apps
from shared.schema.data_objects import SteamApp
import server.src.database as database
from server.src.api import app
from scripts.weekly_reset import reset as weekly_reset


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
    print(f"{total} apps loaded into {args.output}")


def seconds_until_next_monday() -> float:
    now = datetime.now()
    days_ahead = 7 - now.weekday()
    next_monday = (now + timedelta(days=days_ahead)).replace(hour=0, minute=0, second=0, microsecond=0)
    return (next_monday - now).total_seconds()


def weekly_cycle(args: argparse.Namespace) -> None:
    CHECK_INTERVAL = 300

    while True:
        remaining = database.get_db().count_apps(unscraped_only=True)
        if remaining > 0:
            print(f"{remaining} apps remaining, checking again in {CHECK_INTERVAL // 60}min...")
            time.sleep(CHECK_INTERVAL)
            continue

        wait = seconds_until_next_monday()
        wake = datetime.now() + timedelta(seconds=wait)
        print(f"All apps scraped. Next cycle: {wake.strftime('%Y-%m-%d %H:%M')} ({wait / 3600:.1f}h from now)")
        time.sleep(wait)

        print("Starting weekly reset...")
        weekly_reset(db=args.output)
        database.init(args.output)
        fill_app_entries(args)
        removed = database.get_db().delete_orphaned_reviews()
        if removed:
            print(f"Removed {removed} reviews for apps no longer on Steam")
        print("Weekly cycle started.")


def main():
    parser = argparse.ArgumentParser(prog="Steam scraper", description="Scrape Steam app list into SQLite")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("-k", "--key", help="Steam Web API key (fetches live data)")
    source.add_argument("-al", "--app-list", help="Path to existing app list JSON file")
    parser.add_argument("-sapf", "--skip-app-list-fetch", action="store_true", help="Skip the initial app list fetch")
    parser.add_argument("-o", "--output", default="steam.db", help="SQLite database file path")
    parser.add_argument("-p", "--port", type=int, default=8000, help="Port to listen on")
    parser.add_argument("--no-reset", action="store_true", help="Skip the initial backup and reset")
    args = parser.parse_args()

    if not args.no_reset:
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
