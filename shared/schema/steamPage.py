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
    """
    Final object containing all data extracted from a Steam store page.

    Convention:
        - str fields use "" to mean "not present on page"
        - Optional[int] / Optional[SysReq] use None when the value is absent or not applicable
        - scraped_ok=True only if is_valid() passes; False means the scrape ran but produced unexpected data
    """

    appid: int
    title: str = ""
    short_description: str = ""
    description: str = ""
    release_date: str = ""
    early_access_release_date: str = ""
    developer: list[str] = field(default_factory=list)
    publisher: list[str] = field(default_factory=list)
    genres: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    platforms: list[str] = field(default_factory=list)
    price_final_cents: Optional[int] = None
    price_original_cents: Optional[int] = None
    discount_pct: int = 0
    discount_ends: str = ""
    review_summary_recent: str = ""
    review_count_recent: Optional[int] = None
    review_pct_recent: Optional[int] = None
    review_summary_all: str = ""
    review_count_all: Optional[int] = None
    review_pct_all: Optional[int] = None
    metacritic_score: Optional[int] = None
    metacritic_url: str = ""
    header_image: str = ""
    website: str = ""
    social_links: dict[str, str] = field(default_factory=dict)
    steam_awards: list[str] = field(default_factory=list)
    press_reviews: list[dict] = field(default_factory=list)
    dlc: list[DLC] = field(default_factory=list)
    sys_req_windows: Optional[SysReq] = None
    sys_req_mac: Optional[SysReq] = None
    ai_content_disclosure: str = ""
    bundle_count: int = 0
    scraped_ok: bool = True

    def is_valid(self) -> bool:
        required_str = [self.title, self.short_description, self.description, self.release_date, self.header_image]
        required_list = [self.developer, self.platforms]
        return bool(
            self.appid
            and all(required_str)
            and all(required_list)
        )
