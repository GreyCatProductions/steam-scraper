"""Health checker configuration, loaded from a JSON file (see health_checker/health.example.json)."""
import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ServiceConfig:
    """A FastAPI service pinged on GET {url}/, which every service here answers with {"status": "ok"}."""
    name: str
    url: str
    timeout: float = 5.0
    # consecutive failed pings before the service counts as down (one miss is only a warning)
    fail_after: int = 3


@dataclass
class ProgressConfig:
    """Watches the db server's GET /apps/stats and fails if the done count stops growing while work remains."""
    url: str
    stall_minutes: float = 30.0
    timeout: float = 10.0


@dataclass
class QualityConfig:
    """Watches the db server's GET /apps/stats for scrapes that keep "progressing" while failing.
    Overall: share of finished apps given up on (scraped_ok=-1). Recent: share of failed attempts within
    window_minutes, which reacts much faster, since an app is only given up on after 10 failed attempts."""
    url: str
    warn_failed_percent: float = 10.0
    fail_failed_percent: float = 30.0
    # don't judge before this many finished apps / attempts in the window, small samples are noise
    min_finished: int = 200
    window_minutes: float = 60.0
    min_window_events: int = 50
    timeout: float = 30.0


@dataclass
class TableExpectation:
    name: str
    min_rows: int = 0
    # an integer unix-time column whose newest value must be recent, e.g. playercounts_fetched.fetched_at
    fresh_column: str | None = None
    max_age_hours: float | None = None
    # the table must gain at least this many rows within growth_window_minutes, catches a scraper that keeps
    # writing but adds nothing (e.g. every app coming back empty)
    min_growth_rows: int | None = None
    growth_window_minutes: float = 60.0
    # true for tables only the weekly scrape writes (apps, reviews): freshness, growth and min_rows are skipped
    # while the processing server reports the scrape complete (idle until Monday) or the apps table empty (reset)
    pause_when_scrape_idle: bool = False


@dataclass
class DatabaseConfig:
    """A live sqlite database a scraper is writing to. Opened read-only."""
    name: str
    path: Path
    min_bytes: int = 0
    # the file must have been written to within this many minutes (None: don't check)
    max_mtime_age_minutes: float | None = None
    # "none", "quick" (PRAGMA quick_check) or "full" (PRAGMA integrity_check). Slow on multi-GB files.
    integrity: str = "none"
    tables: list[TableExpectation] = field(default_factory=list)
    # every query is aborted after this long (the check then fails), so the checker never competes with the
    # scraper for long. Capped at 2 s for files not in WAL mode, where a running read blocks the scraper's writes.
    max_read_seconds: float = 10.0
    # skip the max_mtime_age_minutes check while the weekly scrape is idle, see TableExpectation
    pause_when_scrape_idle: bool = False


@dataclass
class DiskConfig:
    """Free space on the filesystem holding `path`. Fails below the minimum, warns below twice the minimum."""
    path: Path
    min_free_gb: float | None = None
    min_free_percent: float | None = None


@dataclass
class BackupConfig:
    """One kind of backup file, named {stem}_{YYYY-MM-DD}_{NNN}.db by next_backup_path, e.g.
    steam (apps table), steam_reviews, playercounts (file mode) or steam_playercounts (db mode)."""
    name: str
    stem: str
    min_bytes: int = 0
    # the newest backup must be younger than this (None: don't check)
    max_age_hours: float | None = None
    integrity: str = "quick"
    tables: list[TableExpectation] = field(default_factory=list)
    # warn when a table has shrunk by more than this percentage compared to the previous backup of the same kind
    max_shrink_percent: float | None = 10.0
    # how many of the newest backups to validate (results are cached, so older files are checked once)
    check_last: int = 3
    # files modified within this many minutes are being written and left alone, opening one could make the
    # backup fail
    settle_minutes: float = 10.0


@dataclass
class Intervals:
    services_seconds: float = 60
    progress_seconds: float = 300
    quality_seconds: float = 300
    disk_seconds: float = 300
    databases_seconds: float = 900
    backups_seconds: float = 3600


@dataclass
class Config:
    backup_dir: Path = Path("backups")
    host: str = "127.0.0.1"
    port: int = 8003
    # optional: POSTed {"text": ...} whenever a check changes status (works with Slack/Discord-compatible hooks)
    webhook_url: str | None = None
    intervals: Intervals = field(default_factory=Intervals)
    services: list[ServiceConfig] = field(default_factory=list)
    progress: ProgressConfig | None = None
    quality: QualityConfig | None = None
    disks: list[DiskConfig] = field(default_factory=list)
    databases: list[DatabaseConfig] = field(default_factory=list)
    backups: list[BackupConfig] = field(default_factory=list)


INTEGRITY_MODES = {"none", "quick", "full"}


def _tables(raw: list[dict]) -> list[TableExpectation]:
    return [TableExpectation(**t) for t in raw]


def load_config(path: Path) -> Config:
    raw = json.loads(path.read_text(encoding="utf-8"))
    cfg = Config(
        backup_dir=Path(raw.get("backup_dir", "backups")),
        host=raw.get("host", "127.0.0.1"),
        port=raw.get("port", 8003),
        webhook_url=raw.get("webhook_url"),
        intervals=Intervals(**raw.get("intervals", {})),
        services=[ServiceConfig(**s) for s in raw.get("services", [])],
        progress=ProgressConfig(**raw["progress"]) if raw.get("progress") else None,
        quality=QualityConfig(**raw["quality"]) if raw.get("quality") else None,
        disks=[DiskConfig(**{**d, "path": Path(d["path"])}) for d in raw.get("disks", [])],
        databases=[
            DatabaseConfig(**{**d, "path": Path(d["path"]), "tables": _tables(d.get("tables", []))})
            for d in raw.get("databases", [])
        ],
        backups=[BackupConfig(**{**b, "tables": _tables(b.get("tables", []))}) for b in raw.get("backups", [])],
    )
    for item in [*cfg.databases, *cfg.backups]:
        if item.integrity not in INTEGRITY_MODES:
            raise ValueError(f"{item.name}: integrity must be one of {sorted(INTEGRITY_MODES)}, got {item.integrity!r}")
    return cfg
