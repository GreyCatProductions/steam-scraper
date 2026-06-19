import argparse
import json
import random
import time
import requests
from tqdm import tqdm
from appListBuilder import fetch_all_apps
from schema.data_objects import SteamApp
from database import addApps, getApps, countApps, init_db, saveGamePageInfo
from pageExtractor import extract
from schema.steamPage import GamePage
from utils import reconstruct_steam_url


def fill_app_entries(args: argparse.Namespace):
    def iter_pages_from_json(path: str):
        with open(path, encoding="utf-8") as f:
            yield json.load(f)
            
    pages = iter_pages_from_json(args.app_list) if args.app_list else fetch_all_apps(args.key)

    total = 0
    for page in pages:
        apps = [SteamApp.from_dict(a) for a in page]
        addApps(apps)
        total += len(apps)
        
    print(f"{total} apps saved to {args.output}")

def process_app_steam_pages():
    '''
        Uses entries in db to reconstruct url, fetch the steam page and 
        extract all useful data to save it in db.
    '''
    
    MIN_SLEEP = 1
    MAX_SLEEP = 3
    ATTEMPTS_ON_FAIL = 5

    for app in tqdm(getApps(), total=countApps(), desc="Scraping pages"):
        url = reconstruct_steam_url(app.appid)

        r = None
        for attempt in range(ATTEMPTS_ON_FAIL):
            try:
                r = requests.get(url=url, timeout=10)
                r.raise_for_status()
                break
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 429:
                    wait = 15 * (2 ** attempt)
                    tqdm.write(f"Rate limited, retrying in {wait}s... (attempt {attempt + 1}/{ATTEMPTS_ON_FAIL})")
                    time.sleep(wait)
                else:
                    tqdm.write(f"Failed to fetch {url}: {e}")
                    break
            except requests.RequestException as e:
                tqdm.write(f"Failed to fetch {url}: {e}")
                break
        else:
            tqdm.write(f"Giving up on {url} after {ATTEMPTS_ON_FAIL} attempts")

        if r is None or not r.ok:
            continue

        try:
            data: GamePage = extract(r.text)
            saveGamePageInfo(data)
        except Exception as e:
            tqdm.write(f"Failed to extract/save {url}: {e}")
        
        time.sleep(random.uniform(MIN_SLEEP, MAX_SLEEP))

def main():
    parser = argparse.ArgumentParser(prog="Steam scraper", description="Scrape Steam app list into SQLite")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("-k", "--key", help="Steam Web API key (fetches live data)")
    source.add_argument("-al", "--app-list", help="Path to existing app list JSON file")
    parser.add_argument("-sapf", "--skip-app-list-fetch", action="store_true", help="Skip fetching the app list and go straight to scraping pages")
    parser.add_argument("-o", "--output", default="steam.db", help="SQLite database file path")
    args = parser.parse_args()

    init_db(args.output)

    if not args.skip_app_list_fetch:
        fill_app_entries(args)
    process_app_steam_pages()

if __name__ == "__main__":
    main()
