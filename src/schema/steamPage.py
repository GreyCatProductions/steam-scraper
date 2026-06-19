from dataclasses import dataclass
from typing import Optional


@dataclass
class DLC:
    appid: int
    name: str
    price_final_cents: Optional[int]
    price_original_cents: Optional[int]
    discount_pct: int
    url: str


@dataclass
class SysReq:
    minimum: str
    recommended: str


@dataclass
class GamePage:
    appid: int
    title: str
    short_description: str
    description: str
    release_date: str
    early_access_release_date: Optional[str]
    developer: list[str]
    publisher: list[str]
    genres: list[str]
    tags: list[str]
    platforms: list[str]
    price_final_cents: Optional[int]
    price_original_cents: Optional[int]
    discount_pct: int
    discount_ends: Optional[str]
    review_summary_recent: Optional[str]
    review_count_recent: Optional[int]
    review_pct_recent: Optional[int]
    review_summary_all: Optional[str]
    review_count_all: Optional[int]
    review_pct_all: Optional[int]
    metacritic_score: Optional[int]
    metacritic_url: Optional[str]
    header_image: Optional[str]
    website: Optional[str]
    social_links: dict[str, str]
    steam_awards: list[str]
    press_reviews: list[dict]
    dlc: list[DLC]
    sys_req_windows: Optional[SysReq]
    sys_req_mac: Optional[SysReq]
    ai_content_disclosure: Optional[str]
    bundle_count: int
