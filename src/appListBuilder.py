import argparse
import os
import time
import requests
import json
from dotenv import load_dotenv

API_URL = "https://api.steampowered.com/IStoreService/GetAppList/v1/"
MAX_RESULTS = 50000  # Maximum allowed by the API


def fetch_page(api_key, last_appid=None):
    """
    Fetch a single page of apps from the Steam API.
    Returns the parsed JSON response, or raises on failure.
    """
    params = {
        "key": api_key,
        "max_results": MAX_RESULTS,
        "include_games": "true",
        "include_dlc": "true",
        "include_software": "true",
        "include_videos": "true",
        "include_hardware": "true",
    }

    if last_appid is not None:
        params["last_appid"] = last_appid

    response = requests.get(API_URL, params=params)
    response.raise_for_status()
    return response.json()


def fetch_all_apps(api_key):
    """
    Paginate through all Steam apps, yielding each page of results.
    Stops when a page returns fewer results than max_results indicating
    all apps reached.
    """
    last_appid = None
    page = 1

    while True:
        print(f"Fetching page {page} (last_appid={last_appid})")

        data = fetch_page(api_key, last_appid)
        apps = data.get("response", {}).get("apps", [])

        if not apps:
            print("No apps returned, stopping.")
            break

        yield apps

        print(f"  Got {len(apps)} apps.")

        if len(apps) < MAX_RESULTS:
            print("Last page reached.")
            break

        last_appid = apps[-1]["appid"]
        page += 1

        time.sleep(1)


def main():
    parser = argparse.ArgumentParser(description="Fetch all Steam app IDs and names.")
    parser.add_argument("--key", required=True, help="Your Steam Web API key")
    parser.add_argument("--output", default="app_index.json", help="Output file path")
    args = parser.parse_args()

    all_apps = []

    for page in fetch_all_apps(args.key):
        all_apps.extend(page)

    print(f"\nTotal apps fetched: {len(all_apps)}")

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(all_apps, f)

    print(f"Saved to {args.output}")


if __name__ == "__main__":
    main()
