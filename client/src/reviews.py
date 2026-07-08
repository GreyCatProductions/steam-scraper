import time
from typing import Generator
import requests
from shared.schema.review import UserReview


_REVIEWS_URL = "https://store.steampowered.com/appreviews/{appid}"
_PER_PAGE = 100


def iter_reviews(
    appid: int,
    language: str = "all",
    max_reviews: int | None = None,
    stop_before: int = 0,
    proxy: str | None = None,
) -> Generator[list[UserReview], None, None]:
    cursor = "*"
    total = 0
    proxies = {"http": f"http://{proxy}", "https": f"http://{proxy}"} if proxy else None

    while True:
        for attempt in range(5):
            try:
                r = requests.get(
                    _REVIEWS_URL.format(appid=appid),
                    params={
                        "json": 1,
                        "num_per_page": _PER_PAGE,
                        "language": language,
                        "filter": "recent",
                        "review_type": "all",
                        "purchase_type": "all",
                        "cursor": cursor,
                    },
                    proxies=proxies,
                    timeout=15,
                )
                r.raise_for_status()
                data = r.json()
                break
            except requests.RequestException as e:
                wait = 10 * (2 ** attempt)
                print(f"Reviews fetch failed for {appid} (attempt {attempt + 1}/5): {e}, retrying in {wait}s")
                time.sleep(wait)
        else:
            print(f"Giving up on reviews for {appid}")
            return

        if data.get("success") != 1:
            return

        batch = [UserReview.from_dict(appid, rv) for rv in data.get("reviews", [])]
        if not batch:
            return

        if stop_before:
            new = [rv for rv in batch if rv.timestamp_created >= stop_before]
            if len(new) < len(batch):
                if new:
                    yield new
                return
            batch = new

        yield batch
        total += len(batch)

        if max_reviews is not None and total >= max_reviews:
            return

        new_cursor = data.get("cursor", "")
        if not new_cursor or new_cursor == cursor:
            return

        cursor = new_cursor
        time.sleep(1.0)


def fetch_reviews(appid: int, language: str = "all", max_reviews: int | None = None) -> list[UserReview]:
    reviews: list[UserReview] = []
    for batch in iter_reviews(appid, language, max_reviews):
        reviews.extend(batch)
    return reviews


if __name__ == "__main__":
    import json
    import dataclasses
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("appid", type=int)
    parser.add_argument("--language", default="english")
    parser.add_argument("--max", type=int, default=None)
    args = parser.parse_args()

    reviews = fetch_reviews(args.appid, args.language, args.max)
    print(json.dumps([dataclasses.asdict(r) for r in reviews], indent=2))
    print(f"\nFetched {len(reviews)} reviews for appid {args.appid}")
