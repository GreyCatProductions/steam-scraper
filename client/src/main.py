import argparse
import dataclasses
import random
import time
import requests
from tqdm import tqdm
from client.src.pageExtractor import extract
from client.src.reviews import iter_reviews
from shared.schema.data_objects import SteamApp
from shared.schema.review import UserReview
from shared.schema.steamPage import GamePage
from shared.utils import reconstruct_steam_url


MIN_SLEEP = 1.0
MAX_SLEEP = 3.0
ATTEMPTS_ON_FAIL = 5


def fetch_batch(server_url: str, batch: int) -> list[SteamApp]:
    r = requests.get(f"{server_url}/apps/next", params={"batch": batch}, timeout=10)
    r.raise_for_status()
    return [SteamApp.from_dict(a) for a in r.json()]


def submit_results(server_url: str, results: list[GamePage]) -> None:
    r = requests.post(
        f"{server_url}/apps/results",
        json=[dataclasses.asdict(p) for p in results],
        timeout=30,
    )
    r.raise_for_status()


def submit_reviews(server_url: str, reviews: list[UserReview]) -> None:
    r = requests.post(
        f"{server_url}/reviews/results",
        json=[dataclasses.asdict(rv) for rv in reviews],
        timeout=30,
    )
    r.raise_for_status()


def scrape_app(app: SteamApp) -> GamePage | None:
    url = reconstruct_steam_url(app.appid)
    r = None
    for attempt in range(ATTEMPTS_ON_FAIL):
        try:
            r = requests.get(url, timeout=10, cookies={"Steam_Language": "english"})
            r.raise_for_status()
            break
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 429:
                wait = 15 * (2 ** attempt)
                tqdm.write(f"Rate limited on {url}, retrying in {wait}s... ({attempt + 1}/{ATTEMPTS_ON_FAIL})")
                time.sleep(wait)
            else:
                tqdm.write(f"HTTP error fetching {url}: {e}")
                break
        except requests.RequestException as e:
            tqdm.write(f"Request error fetching {url}: {e}")
            break
    else:
        tqdm.write(f"Giving up on {url} after {ATTEMPTS_ON_FAIL} attempts")

    if r is None or not r.ok:
        return None

    try:
        return extract(r.text)
    except Exception as e:
        tqdm.write(f"Failed to extract {url}: {e}")
        return None


def run(server_url: str, batch_size: int) -> None:
    while True:
        apps = fetch_batch(server_url, batch_size)
        if not apps:
            print("No apps available, rechecking in 5 minutes...")
            time.sleep(300)
            continue

        results: list[GamePage] = []
        for app in tqdm(apps, desc=f"Scraping batch of {len(apps)}"):
            page = scrape_app(app)
            if page is not None:
                results.append(page)
            time.sleep(random.uniform(MIN_SLEEP, MAX_SLEEP))

        if results:
            try:
                submit_results(server_url, results)
                tqdm.write(f"Submitted {len(results)}/{len(apps)} results")
            except requests.RequestException as e:
                tqdm.write(f"Failed to submit results: {e}")

        valid_pages = [p for p in results if p.scraped_ok]
        for page in tqdm(valid_pages, desc="Fetching reviews"):
            chunk: list[UserReview] = []
            total = 0
            for batch in iter_reviews(page.appid):
                chunk.extend(batch)
                total += len(batch)
                if len(chunk) >= 100:
                    try:
                        submit_reviews(server_url, chunk)
                    except requests.RequestException as e:
                        tqdm.write(f"  Failed to submit reviews for {page.appid}: {e}")
                    chunk = []
            if chunk:
                try:
                    submit_reviews(server_url, chunk)
                except requests.RequestException as e:
                    tqdm.write(f"  Failed to submit reviews for {page.appid}: {e}")
            tqdm.write(f"  appid {page.appid}: {total} reviews")


def main():
    parser = argparse.ArgumentParser(description="Steam page scraper client")
    parser.add_argument("--server", required=True, help="Server base URL (e.g. http://1.2.3.4:8000)")
    parser.add_argument("--batch", type=int, default=50, help="Apps to claim per batch")
    args = parser.parse_args()

    run(args.server, args.batch)


if __name__ == "__main__":
    main()
