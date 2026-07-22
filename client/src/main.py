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


def _post_with_retry(url: str, json_payload, timeout: int, attempts: int = 5) -> bool:
    for attempt in range(attempts):
        try:
            r = requests.post(url, json=json_payload, timeout=timeout)
            r.raise_for_status()
            return True
        except requests.RequestException as e:
            wait = 10 * (2 ** attempt)
            tqdm.write(f"Upload to {url} failed (attempt {attempt + 1}/{attempts}): {e}, retrying in {wait}s")
            time.sleep(wait)
    tqdm.write(f"Giving up on uploading to {url}")
    return False


def submit_results(server_url: str, results: list[GamePage]) -> bool:
    return _post_with_retry(
        f"{server_url}/apps/results",
        [dataclasses.asdict(p) for p in results],
        timeout=30,
    )


def submit_reviews(server_url: str, reviews: list[UserReview]) -> bool:
    return _post_with_retry(
        f"{server_url}/reviews/results",
        [dataclasses.asdict(rv) for rv in reviews],
        timeout=30,
    )


def mark_reviews_done(server_url: str, appid: int) -> bool:
    return _post_with_retry(f"{server_url}/reviews/done/{appid}", None, timeout=10)


def get_latest_review_timestamp(server_url: str, appid: int) -> int:
    r = requests.get(f"{server_url}/reviews/latest-timestamp/{appid}", timeout=10)
    r.raise_for_status()
    return r.json()["timestamp"]


def scrape_app(app: SteamApp, proxy: str | None) -> GamePage | None:
    url = reconstruct_steam_url(app.appid)
    proxy_url = proxy if proxy and proxy.startswith("http") else f"http://{proxy}" if proxy else None
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    r = None
    for attempt in range(ATTEMPTS_ON_FAIL):
        try:
            r = requests.get(url, timeout=10, cookies={"Steam_Language": "english"},
                            proxies=proxies)
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


def run(server_url: str, proxy: str | None, batch_size: int) -> None:
    if proxy:
        print(f"Using proxy: {proxy}")
    else:
        print(f"Using default IP. No proxy.")
        
    while True:
        try:
            apps = fetch_batch(server_url, batch_size)
        except requests.RequestException as e:
            print(f"Could not reach server: {e}, retrying in 5 minutes...")
            time.sleep(300)
            continue
        if not apps:
            print("No apps available, rechecking in 5 minutes...")
            time.sleep(300)
            continue

        results: list[GamePage] = []
        for app in tqdm(apps, desc=f"Scraping batch of {len(apps)}"):
            page = scrape_app(app, proxy)
            if page is not None:
                results.append(page)
            time.sleep(random.uniform(MIN_SLEEP, MAX_SLEEP))

        if results:
            if submit_results(server_url, results):
                tqdm.write(f"Submitted {len(results)}/{len(apps)} results")
            else:
                tqdm.write("Failed to submit results after retries")

        _ONE_DAY = 86400
        valid_pages = [p for p in results if p.scraped_ok]
        for page in tqdm(valid_pages, desc="Fetching reviews"):
            chunk: list[UserReview] = []
            total = 0
            failed = False
            
            #reviews are fetched by recent. Fetching already oldest saved state from db for offset
            try:
                latest_ts = get_latest_review_timestamp(server_url, page.appid)
            except requests.RequestException:
                latest_ts = 0
            stop_before = max(0, latest_ts - _ONE_DAY)
            
            
            try:
                for batch in iter_reviews(page.appid, stop_before=stop_before, proxy=proxy):
                    chunk.extend(batch)
                    total += len(batch)
                    if len(chunk) >= 100:
                        if submit_reviews(server_url, chunk):
                            chunk = []
                        else:
                            tqdm.write(f"  Giving up on reviews for {page.appid} after failed upload")
                            failed = True
                            break
            except requests.RequestException as e:
                tqdm.write(f"  Reviews fetch failed for {page.appid}: {e}")
                failed = True

            if not failed and chunk:
                if not submit_reviews(server_url, chunk):
                    tqdm.write(f"  Giving up on reviews for {page.appid} after failed upload")
                    failed = True
            if not failed:
                if not mark_reviews_done(server_url, page.appid):
                    tqdm.write(f"  Failed to mark reviews done for {page.appid} after retries")
            tqdm.write(f"  appid {page.appid}: {total} reviews")


def main():
    parser = argparse.ArgumentParser(description="Steam page scraper client")
    parser.add_argument("--server", required=True, help="Server base URL (e.g. http://1.2.3.4:8000)")
    parser.add_argument("--proxy", type=str, help="Proxy to route the traffic trough (e.g. USER:PASSWORD@IP:HTTP_PORT)")
    parser.add_argument("--batch", type=int, default=50, help="Apps to claim per batch")
    args = parser.parse_args()

    run(args.server, args.proxy, args.batch)


if __name__ == "__main__":
    main()
