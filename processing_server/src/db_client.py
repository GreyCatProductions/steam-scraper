import dataclasses
from typing import Optional
import requests
from shared.schema.data_objects import SteamApp
from shared.schema.review import UserReview
from shared.schema.steamPage import GamePage


class DbClient:
    def __init__(self, base_url: str):
        self._base = base_url.rstrip("/")

    def add_apps(self, apps: list[SteamApp]) -> None:
        r = requests.post(f"{self._base}/apps", json=[dataclasses.asdict(a) for a in apps], timeout=30)
        r.raise_for_status()

    def claim_apps(self, amount: int) -> list[SteamApp]:
        r = requests.get(f"{self._base}/apps/claim", params={"amount": amount}, timeout=10)
        r.raise_for_status()
        return [SteamApp.from_dict(a) for a in r.json()]

    def count_apps(self, unscraped_only: bool = False) -> int:
        r = requests.get(f"{self._base}/apps/count", params={"unscraped_only": unscraped_only}, timeout=600)
        r.raise_for_status()
        return r.json()["count"]

    def save_game_page_info(self, page: GamePage) -> None:
        r = requests.post(f"{self._base}/apps/results", json=[dataclasses.asdict(page)], timeout=30)
        r.raise_for_status()

    def save_results(self, pages: list[GamePage]) -> None:
        r = requests.post(f"{self._base}/apps/results", json=[dataclasses.asdict(p) for p in pages], timeout=30)
        r.raise_for_status()

    def save_reviews(self, reviews: list[UserReview]) -> None:
        r = requests.post(f"{self._base}/reviews", json=[dataclasses.asdict(rv) for rv in reviews], timeout=30)
        r.raise_for_status()

    def get_latest_review_timestamp(self, appid: int) -> int:
        r = requests.get(f"{self._base}/reviews/latest-timestamp/{appid}", timeout=10)
        r.raise_for_status()
        return r.json()["timestamp"]

    def mark_reviews_done(self, appid: int) -> None:
        r = requests.post(f"{self._base}/reviews/done/{appid}", timeout=10)
        r.raise_for_status()

    def reset(self) -> None:
        r = requests.post(f"{self._base}/admin/reset", timeout=120)
        r.raise_for_status()

    def ping(self) -> bool:
        try:
            r = requests.get(f"{self._base}/", timeout=5)
            return r.ok
        except requests.RequestException:
            return False


_client: Optional[DbClient] = None


def init(base_url: str) -> None:
    global _client
    _client = DbClient(base_url)
    if not _client.ping():
        raise RuntimeError(f"DB server not reachable at {base_url}")


def get_client() -> DbClient:
    return _client  # type: ignore
