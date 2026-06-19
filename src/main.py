import argparse
import dataclasses
import json
import sqlite_utils
from appListBuilder import fetch_all_apps
from schema.data_objects import SteamApp

def fill_app_entries(db, args: argparse.Namespace):
    def iter_pages_from_json(path: str):
        with open(path, encoding="utf-8") as f:
            yield json.load(f)
            
    pages = iter_pages_from_json(args.app_list) if args.app_list else fetch_all_apps(args.key)

    total = 0
    for page in pages:
        apps = [SteamApp.from_dict(a) for a in page]
        db["apps"].insert_all(  # type: ignore[union-attr]
            [dataclasses.asdict(a) for a in apps],
            pk="appid",  # type: ignore[arg-type]
            replace=True,  # type: ignore[arg-type]
        )
        total += len(apps)
        
    print(f"{total} apps saved to {args.output}")


def main():
    parser = argparse.ArgumentParser(prog="Steam scraper", description="Scrape Steam app list into SQLite")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("-k", "--key", help="Steam Web API key (fetches live data)")
    source.add_argument("-al", "--app-list", help="Path to existing app list JSON file")
    parser.add_argument("-o", "--output", default="steam.db", help="SQLite database file path")
    args = parser.parse_args()

    db = sqlite_utils.Database(args.output)

    fill_app_entries(db, args)


if __name__ == "__main__":
    main()
