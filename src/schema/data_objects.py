from dataclasses import dataclass, field
from typing import Optional

@dataclass
class SteamApp:
    appid: int
    name: str
    last_modified: int
    price_change_number: int

    @classmethod
    def from_dict(cls, data: dict) -> "SteamApp":
        return cls(
            appid=data["appid"],
            name=data.get("name", ""),
            last_modified=data.get("last_modified", 0),
            price_change_number=data.get("price_change_number", 0),
        )