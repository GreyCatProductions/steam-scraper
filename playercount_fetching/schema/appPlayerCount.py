from dataclasses import dataclass, field


@dataclass
class PlayercountEntry:
    timestamp: int
    playercount: int


@dataclass
class AppPlayerCount:
    appid: int
    entries: list[PlayercountEntry] = field(default_factory=list)
