from dataclasses import dataclass, field
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
    title: str = ""
    short_description: str = ""
    description: str = ""
    release_date: str = ""
    early_access_release_date: Optional[str] = None
    developer: list[str] = field(default_factory=list)
    publisher: list[str] = field(default_factory=list)
    genres: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    platforms: list[str] = field(default_factory=list)
    price_final_cents: Optional[int] = None
    price_original_cents: Optional[int] = None
    discount_pct: int = 0
    discount_ends: Optional[str] = None
    review_summary_recent: Optional[str] = None
    review_count_recent: Optional[int] = None
    review_pct_recent: Optional[int] = None
    review_summary_all: Optional[str] = None
    review_count_all: Optional[int] = None
    review_pct_all: Optional[int] = None
    metacritic_score: Optional[int] = None
    metacritic_url: Optional[str] = None
    header_image: Optional[str] = None
    website: Optional[str] = None
    social_links: dict[str, str] = field(default_factory=dict)
    steam_awards: list[str] = field(default_factory=list)
    press_reviews: list[dict] = field(default_factory=list)
    dlc: list[DLC] = field(default_factory=list)
    sys_req_windows: Optional[SysReq] = None
    sys_req_mac: Optional[SysReq] = None
    ai_content_disclosure: Optional[str] = None
    bundle_count: int = 0
    scraped_ok: bool = True
