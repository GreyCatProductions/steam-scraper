import time
import requests
from shared.schema.review import UserReview


_REVIEWS_URL = "https://store.steampowered.com/appreviews/{appid}"
_PER_PAGE = 100


def fetch_reviews(appid: int, language: str = "all", max_reviews: int | None = None) -> list[UserReview]:
    '''
        Fetches all reviews for an app via the Steam reviews API.
        Paginates using the cursor until no more reviews are returned.
    '''
    reviews: list[UserReview] = []
    cursor = "*"

    while True:
        try:
            r = requests.get(
                _REVIEWS_URL.format(appid=appid),
                params={
                    "json": 1,
                    "num_per_page": _PER_PAGE,
                    "language": language,
                    "filter": "all",
                    "cursor": cursor,
                },
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
        except requests.RequestException as e:
            print(f"Failed to fetch reviews for {appid}: {e}")
            break

        if data.get("success") != 1:
            break

        batch = data.get("reviews", [])
        reviews.extend(UserReview.from_dict(appid, rv) for rv in batch)
        print(f"\rFetched {len(reviews)} reviews...", end="", flush=True)

        if len(batch) < _PER_PAGE:
            break

        if max_reviews is not None and len(reviews) >= max_reviews:
            break

        cursor = data["cursor"]
        time.sleep(1.0)

    print()
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
