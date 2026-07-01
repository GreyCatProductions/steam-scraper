import argparse
import json
import uvicorn
from server.src.appListBuilder import fetch_all_apps
from shared.schema.data_objects import SteamApp
import server.src.database as database
from server.src.api import app


def fill_app_entries(args: argparse.Namespace):
    '''
        Populates the database with basic steam app entries.
        Reads from a local JSON file if args.app_list is set,
        otherwise fetches live data from the Steam Web API using args.key.
    '''
    def iter_pages_from_json(path: str):
        with open(path, encoding="utf-8") as f:
            yield json.load(f)
            
    print(f"Initializing db")

    pages = iter_pages_from_json(args.app_list) if args.app_list else fetch_all_apps(args.key)

    total = 0
    for page in pages:
        apps = [SteamApp.from_dict(a) for a in page]
        database.get_db().add_apps(apps)
        total += len(apps)

    print(f"{total} apps saved to {args.output}")


def main():
    parser = argparse.ArgumentParser(prog="Steam scraper", description="Scrape Steam app list into SQLite")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("-k", "--key", help="Steam Web API key (fetches live data)")
    source.add_argument("-al", "--app-list", help="Path to existing app list JSON file")
    parser.add_argument("-sapf", "--skip-app-list-fetch", action="store_true", help="Skip fetching the app list and go straight to scraping pages. Only useful if the db is known to be sufficiently filled already")
    parser.add_argument("-o", "--output", default="steam.db", help="SQLite database file path")
    parser.add_argument("-p", "--port", type=int, default=8000, help="Port to listen on")
    args = parser.parse_args()

    database.init(args.output)

    if not args.skip_app_list_fetch:
        fill_app_entries(args)

    uvicorn.run(app, host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
