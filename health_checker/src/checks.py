"""The individual checks. Each returns CheckResults and never raises, so one broken check can't stop the others."""
import re
import shutil
import sqlite3
import time
from collections import deque
from contextlib import closing
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path

import requests

from health_checker.src.config import (BackupConfig, DatabaseConfig, DiskConfig, ProgressConfig, QualityConfig,
                                       ServiceConfig, TableExpectation)

SQLITE_MAGIC = b"SQLite format 3\x00"
# never wait for a lock: if a scraper is writing, the check gives up instead of making the scraper wait for it
LOCK_WAIT_SECONDS = 0.5
# in the default rollback journal mode a reader blocks writers for as long as a query runs, and sqlite writers
# give up after 5 s by default, so queries on such a file are cut off well before that
ROLLBACK_MAX_READ_SECONDS = 2.0


class Status(IntEnum):
    # ordered, so the worst status of several is max()
    OK = 0
    WARN = 1
    FAIL = 2

    def __str__(self) -> str:
        return self.name.lower()


@dataclass
class CheckResult:
    name: str
    status: Status
    message: str
    details: dict = field(default_factory=dict)
    checked_at: float = field(default_factory=time.time)
    # seconds until the check runs again, filled in by the runner, so viewers can tell a stale result
    interval: float | None = None

    def to_dict(self) -> dict:
        return {"name": self.name, "status": str(self.status), "message": self.message,
                "details": self.details, "checked_at": int(self.checked_at), "interval": self.interval}


# ---------------------------------------------------------------- services


class ServicePinger:
    """Pings services and remembers consecutive failures, so a single slow response is a warning, not an outage."""

    def __init__(self) -> None:
        self._failures: dict[str, int] = {}

    def ping(self, svc: ServiceConfig) -> CheckResult:
        name = f"service:{svc.name}"
        start = time.monotonic()
        try:
            r = requests.get(svc.url.rstrip("/") + "/", timeout=svc.timeout)
            latency_ms = round((time.monotonic() - start) * 1000)
            ok = r.status_code == 200 and r.json().get("status") == "ok"
            error = None if ok else f"HTTP {r.status_code}: {r.text[:200]}"
        except requests.Timeout:
            latency_ms, error = None, f"no answer within {svc.timeout:g}s"
        except requests.ConnectionError as e:
            latency_ms = None
            error = "connection refused (service down?)" if "refused" in str(e).lower() else "unreachable (network/host down?)"
        except (requests.RequestException, ValueError) as e:
            latency_ms = None
            error = f"{type(e).__name__}: {e}"

        if error is None:
            self._failures[svc.name] = 0
            return CheckResult(name, Status.OK, f"up ({latency_ms} ms)", {"latency_ms": latency_ms})

        fails = self._failures.get(svc.name, 0) + 1
        self._failures[svc.name] = fails
        status = Status.FAIL if fails >= svc.fail_after else Status.WARN
        return CheckResult(name, status, f"ping failed {fails}x in a row: {error}", {"consecutive_failures": fails})


# ---------------------------------------------------------------- scrape progress


def fetch_db_stats(url: str, timeout: float) -> tuple[dict | None, CheckResult | None]:
    """GET {db server}/apps/stats. Returns (stats, None), or (None, a result to report) when unavailable.
    The db server answers 503 when it skipped the query to keep the scrape fast, which is only a warning."""
    try:
        r = requests.get(url.rstrip("/") + "/apps/stats", timeout=timeout)
        if r.status_code == 503:
            return None, CheckResult("", Status.WARN, f"db server skipped the stats to protect the scrape: "
                                                      f"{r.json().get('detail', r.text[:200])}")
        r.raise_for_status()
        stats = r.json()
        for key in ("total", "ok", "failed", "pending", "fail_attempts"):
            int(stats[key])
        return stats, None
    except (requests.RequestException, ValueError, KeyError, TypeError) as e:
        return None, CheckResult("", Status.FAIL, f"can't read db server /apps/stats: {type(e).__name__}: {e}")


class ProgressWatcher:
    """Fails when the scraped count hasn't moved for stall_minutes while apps remain.
    Remaining == 0 is the idle week between completion and the Monday reset, which is fine.
    Reads the db server's /apps/stats, which doesn't take the lock the scrape clients need (the processing
    server's /apps/stats does)."""

    def __init__(self) -> None:
        self._last_scraped: int | None = None
        self._last_change = time.time()
        # "running", "complete" (idle until the Monday reset), "empty" (reset in progress) or None (unknown)
        self.state: str | None = None

    @property
    def scrape_idle(self) -> bool:
        """True only when the processing server confirmed it isn't scraping. Unknown counts as busy, so an
        unreachable processing server never switches checks off."""
        return self.state in ("complete", "empty")

    def check(self, cfg: ProgressConfig) -> CheckResult:
        name = "progress:scrape"
        stats, error = fetch_db_stats(cfg.url, cfg.timeout)
        if error:
            self.state = None
            error.name = name
            return error
        # given-up apps (scraped_ok=-1) count as done, same as the processing server's own count
        total, scraped, remaining = int(stats["total"]), int(stats["ok"]) + int(stats["failed"]), int(stats["pending"])

        self.state = "empty" if total == 0 else "complete" if remaining == 0 else "running"
        now = time.time()
        if scraped != self._last_scraped:
            self._last_scraped, self._last_change = scraped, now
        stalled_min = (now - self._last_change) / 60
        details = {"total": total, "scraped": scraped, "remaining": remaining, "minutes_since_change": round(stalled_min, 1)}

        if total == 0:
            return CheckResult(name, Status.WARN, "apps table is empty (reset in progress, or app list fetch failed?)", details)
        if remaining == 0:
            return CheckResult(name, Status.OK, f"scrape complete ({scraped}/{total}), waiting for the weekly reset", details)
        if stalled_min >= cfg.stall_minutes:
            return CheckResult(name, Status.FAIL,
                               f"stalled: scraped count stuck at {scraped}/{total} for {stalled_min:.0f} min", details)
        return CheckResult(name, Status.OK, f"{scraped}/{total} scraped, {remaining} remaining", details)


# ---------------------------------------------------------------- scrape quality


class QualityWatcher:
    """Catches a scrape that "progresses" while failing: failed apps drop out of the remaining count too, so a
    fully broken scraper would otherwise run to "complete" with nothing but failures."""

    def __init__(self) -> None:
        self._samples: deque[tuple[float, int, int]] = deque()  # (time, ok, fail_attempts)

    @staticmethod
    def _rate_status(percent: float, cfg: QualityConfig) -> Status:
        if percent >= cfg.fail_failed_percent:
            return Status.FAIL
        return Status.WARN if percent >= cfg.warn_failed_percent else Status.OK

    def check(self, cfg: QualityConfig) -> CheckResult:
        name = "quality:scrape"
        stats, error = fetch_db_stats(cfg.url, cfg.timeout)
        if error:
            error.name = name
            return error
        ok, failed, attempts = int(stats["ok"]), int(stats["failed"]), int(stats["fail_attempts"])

        now = time.time()
        # counters only go down when the weekly reset clears the apps table, the old samples are meaningless then
        if self._samples and (ok < self._samples[-1][1] or attempts < self._samples[-1][2]):
            self._samples.clear()
        self._samples.append((now, ok, attempts))
        window = cfg.window_minutes * 60
        while len(self._samples) >= 2 and self._samples[1][0] <= now - window:
            self._samples.popleft()

        status, parts = Status.OK, []
        details = {**stats}

        finished = ok + failed
        if finished >= cfg.min_finished:
            pct = failed / finished * 100
            details["failed_percent"] = round(pct, 1)
            status = max(status, self._rate_status(pct, cfg))
            parts.append(f"{pct:.1f}% of {finished} finished apps given up on")
        else:
            parts.append(f"{finished} apps finished, judging from {cfg.min_finished}")

        t0, ok0, att0 = self._samples[0]
        new_ok, new_fail = ok - ok0, attempts - att0
        minutes = (now - t0) / 60
        if new_ok + new_fail >= cfg.min_window_events:
            pct = new_fail / (new_ok + new_fail) * 100
            details["recent_failed_percent"] = round(pct, 1)
            status = max(status, self._rate_status(pct, cfg))
            parts.append(f"last {minutes:.0f} min: {new_ok} ok, {new_fail} failed attempts ({pct:.0f}%)")
        elif minutes > 0:
            parts.append(f"last {minutes:.0f} min: only {new_ok + new_fail} results, too few to judge")
        return CheckResult(name, status, "; ".join(parts), details)


# ---------------------------------------------------------------- disk space


def check_disk(cfg: DiskConfig) -> CheckResult:
    name = f"disk:{cfg.path}"
    try:
        usage = shutil.disk_usage(cfg.path)
    except OSError as e:
        return CheckResult(name, Status.FAIL, f"can't read disk usage: {e}")
    free_gb = usage.free / 1024 ** 3
    free_pct = usage.free / usage.total * 100
    details = {"free_gb": round(free_gb, 1), "free_percent": round(free_pct, 1), "total_gb": round(usage.total / 1024 ** 3, 1)}
    status = Status.OK
    for value, minimum in ((free_gb, cfg.min_free_gb), (free_pct, cfg.min_free_percent)):
        if minimum is not None:
            status = max(status, Status.FAIL if value < minimum else Status.WARN if value < 2 * minimum else Status.OK)
    message = f"{free_gb:.1f} GB free ({free_pct:.0f}%)"
    if status != Status.OK:
        limits = [f"{cfg.min_free_gb:g} GB" if cfg.min_free_gb is not None else "",
                  f"{cfg.min_free_percent:g}%" if cfg.min_free_percent is not None else ""]
        message += f", minimum {' / '.join(x for x in limits if x)} (warns below twice that)"
    return CheckResult(name, status, message, details)


# ---------------------------------------------------------------- sqlite helpers


class ReadGaveUp(Exception):
    """A query was cut off or a lock wasn't free, so the check stood back instead of slowing a scraper."""


class GuardedConnection(sqlite3.Connection):
    """Aborts any single query running longer than max_seconds, which bounds how long it can hold a read lock."""
    max_seconds: float | None = None

    def execute(self, sql, parameters=(), /):
        if self.max_seconds is not None:
            deadline = time.monotonic() + self.max_seconds
            # a non-zero return makes sqlite abort the running query with "interrupted"
            self.set_progress_handler(lambda: time.monotonic() > deadline, 10_000)
        try:
            return super().execute(sql, parameters)
        except sqlite3.OperationalError as e:
            raise _gave_up(e, self.max_seconds) from e


def _gave_up(e: sqlite3.OperationalError, max_seconds: float | None) -> Exception:
    text = str(e).lower()
    if "interrupt" in text:
        return ReadGaveUp(f"query gave up after {max_seconds:g}s so it wouldn't slow the scraper")
    if "locked" in text or "busy" in text:
        return ReadGaveUp("file is busy (being written), skipped this round")
    return e


def _connect_ro(path: Path, max_read_seconds: float | None = None) -> GuardedConnection:
    # read-only URI, so the checker can never create, modify or lock a file for writing
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=LOCK_WAIT_SECONDS,
                           factory=GuardedConnection)
    conn.max_seconds = max_read_seconds
    return conn


def _has_sqlite_header(path: Path) -> bool:
    with path.open("rb") as f:
        return f.read(len(SQLITE_MAGIC)) == SQLITE_MAGIC


def _run_integrity(conn: sqlite3.Connection, mode: str) -> str | None:
    """None if fine (or skipped), otherwise the first problems sqlite reports."""
    if mode == "none":
        return None
    pragma = "quick_check" if mode == "quick" else "integrity_check"
    rows = [r[0] for r in conn.execute(f"PRAGMA {pragma}(20)")]
    return None if rows == ["ok"] else "; ".join(rows)


def _check_tables(conn: sqlite3.Connection, expected: list[TableExpectation], scrape_idle: bool = False,
                  growth: "GrowthTracker | None" = None) -> tuple[Status, list[str], dict[str, int]]:
    """Checks presence, row counts, freshness and growth. Returns (worst status, problems, row counts).
    Tables marked pause_when_scrape_idle skip the activity checks while scrape_idle."""
    existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    status, problems, counts = Status.OK, [], {}
    now = time.time()
    for t in expected:
        if t.name not in existing:
            status = Status.FAIL
            problems.append(f"table {t.name} missing")
            continue
        # table and column names come from our own config file, not from user input
        count = conn.execute(f'SELECT COUNT(*) FROM "{t.name}"').fetchone()[0]
        counts[t.name] = count
        if t.pause_when_scrape_idle and scrape_idle:
            if growth:
                growth.reset(t.name)
            continue
        if growth and t.min_growth_rows is not None:
            grown = growth.add(t.name, count, t.growth_window_minutes * 60)
            if grown is not None and grown < t.min_growth_rows:
                status = max(status, Status.WARN)
                problems.append(f"{t.name} grew by {grown} rows in the last {t.growth_window_minutes:g} min, "
                                f"expected >= {t.min_growth_rows}")
        if count < t.min_rows:
            status = Status.FAIL
            problems.append(f"{t.name} has {count} rows, expected >= {t.min_rows}")
        if t.fresh_column and t.max_age_hours is not None:
            newest = conn.execute(f'SELECT MAX("{t.fresh_column}") FROM "{t.name}"').fetchone()[0]
            if newest is None:
                status = max(status, Status.WARN)
                problems.append(f"{t.name}.{t.fresh_column} has no values")
            elif (age_h := (now - newest) / 3600) > t.max_age_hours:
                status = max(status, Status.WARN)
                problems.append(f"{t.name}.{t.fresh_column} newest value is {age_h:.1f}h old, expected < {t.max_age_hours}h")
    return status, problems, counts


# ---------------------------------------------------------------- live databases


class GrowthTracker:
    """Row count samples of one database's tables, to measure how much each grew over its window."""

    def __init__(self) -> None:
        self._samples: dict[str, deque[tuple[float, int]]] = {}

    def reset(self, table: str) -> None:
        self._samples.pop(table, None)

    def add(self, table: str, count: int, window: float) -> int | None:
        """Records a count. Returns the growth over the window, or None until there is a full window of history."""
        now = time.time()
        samples = self._samples.setdefault(table, deque())
        if samples and count < samples[-1][1]:
            samples.clear()  # the table was cleared (weekly reset), start over
        samples.append((now, count))
        # keep exactly one sample older than the window as the baseline
        while len(samples) >= 2 and samples[1][0] <= now - window:
            samples.popleft()
        t0, c0 = samples[0]
        return count - c0 if t0 <= now - window else None


class DatabaseChecker:
    def __init__(self) -> None:
        self._growth: dict[str, GrowthTracker] = {}

    def check(self, cfg: DatabaseConfig, scrape_idle: bool = False) -> CheckResult:
        return check_database(cfg, scrape_idle, self._growth.setdefault(cfg.name, GrowthTracker()))


def check_database(cfg: DatabaseConfig, scrape_idle: bool = False, growth: GrowthTracker | None = None) -> CheckResult:
    name = f"database:{cfg.name}"
    path = cfg.path
    if not path.is_file():
        return CheckResult(name, Status.FAIL, f"{path} doesn't exist")

    stat = path.stat()
    # WAL databases (the db server) write into the -wal file first, so it counts as the latest activity too
    wal = path.with_name(path.name + "-wal")
    mtime = max(stat.st_mtime, wal.stat().st_mtime if wal.exists() else 0)
    details: dict = {"bytes": stat.st_size, "modified_minutes_ago": round((time.time() - mtime) / 60, 1)}
    status, problems = Status.OK, []

    if stat.st_size < cfg.min_bytes:
        status = Status.FAIL
        problems.append(f"size {stat.st_size} B < expected {cfg.min_bytes} B")
    if cfg.pause_when_scrape_idle and scrape_idle:
        pass  # nothing writes to it until the weekly reset
    elif cfg.max_mtime_age_minutes is not None and details["modified_minutes_ago"] > cfg.max_mtime_age_minutes:
        status = max(status, Status.WARN)
        problems.append(f"not written for {details['modified_minutes_ago']:.0f} min")

    try:
        with closing(_connect_ro(path, cfg.max_read_seconds)) as conn:
            journal = conn.execute("PRAGMA journal_mode").fetchone()[0].lower()
            details["journal_mode"] = journal
            if journal != "wal":
                # here our reads block the scraper's writes, so keep every query very short
                conn.max_seconds = min(cfg.max_read_seconds, ROLLBACK_MAX_READ_SECONDS)
            details["max_read_seconds"] = conn.max_seconds
            if err := _run_integrity(conn, cfg.integrity):
                status = Status.FAIL
                problems.append(f"integrity: {err}")
            table_status, table_problems, counts = _check_tables(conn, cfg.tables, scrape_idle, growth)
    except ReadGaveUp as e:
        hint = "" if details.get("journal_mode") != "wal" else " (raise max_read_seconds, reads don't block a WAL file)"
        return CheckResult(name, Status.FAIL, f"{e}{hint}", details)
    except sqlite3.Error as e:
        return CheckResult(name, Status.FAIL, f"can't read: {e}", details)

    details["rows"] = counts
    status = max(status, table_status)
    problems += table_problems
    paused = scrape_idle and (cfg.pause_when_scrape_idle or any(t.pause_when_scrape_idle for t in cfg.tables))
    idle_note = " (scrape idle, activity checks paused)" if paused else ""
    return CheckResult(name, status, "; ".join(problems) or f"ok, rows: {counts}{idle_note}", details)


# ---------------------------------------------------------------- backups


@dataclass
class BackupFile:
    path: Path
    day: str
    counter: int


def find_backups(backup_dir: Path, stem: str) -> list[BackupFile]:
    """Backups of one kind, oldest first. An exact pattern, since a glob like steam_* would also match steam_reviews_*."""
    pattern = re.compile(rf"^{re.escape(stem)}_(\d{{4}}-\d{{2}}-\d{{2}})_(\d{{3,}})\.db$")
    found = []
    if backup_dir.is_dir():
        for p in backup_dir.iterdir():
            if m := pattern.match(p.name):
                found.append(BackupFile(p, m.group(1), int(m.group(2))))
    return sorted(found, key=lambda b: (b.day, b.counter))


class BackupValidator:
    """Validates backup files. A finished backup never changes, so results are cached per (path, size, mtime)
    and the expensive integrity check runs once per file instead of every interval."""

    def __init__(self) -> None:
        self._cache: dict[tuple[str, int, float], tuple[Status, list[str], dict[str, int]]] = {}

    def _validate_file(self, path: Path, cfg: BackupConfig) -> tuple[Status, list[str], dict[str, int]]:
        stat = path.stat()
        key = (str(path), stat.st_size, stat.st_mtime)
        if key in self._cache:
            return self._cache[key]

        status, problems, counts = Status.OK, [], {}
        if stat.st_size < cfg.min_bytes:
            status = Status.FAIL
            problems.append(f"size {stat.st_size} B < expected {cfg.min_bytes} B")
        if not _has_sqlite_header(path):
            status = Status.FAIL
            problems.append("not a sqlite file (bad header)")
        else:
            try:
                with closing(_connect_ro(path)) as conn:
                    if err := _run_integrity(conn, cfg.integrity):
                        status = Status.FAIL
                        problems.append(f"integrity: {err}")
                    table_status, table_problems, counts = _check_tables(conn, cfg.tables)
                    status = max(status, table_status)
                    problems += table_problems
            except ReadGaveUp as e:
                # something still has the file open (shouldn't happen once settled): retry next round, don't cache
                return Status.WARN, [str(e)], {}
            except sqlite3.Error as e:
                status = Status.FAIL
                problems.append(f"can't read: {e}")

        self._cache[key] = (status, problems, counts)
        return status, problems, counts

    def check(self, backup_dir: Path, cfg: BackupConfig) -> CheckResult:
        name = f"backup:{cfg.name}"
        backups = find_backups(backup_dir, cfg.stem)
        if not backups:
            return CheckResult(name, Status.FAIL, f"no {cfg.stem}_<date>_<n>.db files in {backup_dir}")

        newest = backups[-1]
        age_h = (time.time() - newest.path.stat().st_mtime) / 3600
        status, problems = Status.OK, []
        details: dict = {"count": len(backups), "newest": newest.path.name, "newest_age_hours": round(age_h, 1), "files": {}}

        # a backup gets its final name when writing starts; opening it then could make the backup fail, so files
        # written to recently are left alone until they have settled
        settle_before = time.time() - cfg.settle_minutes * 60
        writing = [b for b in backups if b.path.stat().st_mtime > settle_before]
        backups = [b for b in backups if b.path.stat().st_mtime <= settle_before]
        if writing:
            details["being_written"] = [b.path.name for b in writing]
        if not backups:
            return CheckResult(name, Status.OK, f"{len(writing)} backup(s) being written, checked once settled", details)

        if cfg.max_age_hours is not None and age_h > cfg.max_age_hours:
            status = Status.FAIL
            problems.append(f"newest backup is {age_h:.1f}h old, expected < {cfg.max_age_hours}h")

        checked = backups[-cfg.check_last:]
        file_counts: list[dict[str, int]] = []
        for b in checked:
            file_status, file_problems, counts = self._validate_file(b.path, cfg)
            details["files"][b.path.name] = {"status": str(file_status), "rows": counts, "problems": file_problems}
            if file_status != Status.FAIL:
                file_counts.append(counts)
            if file_problems:
                status = max(status, file_status)
                problems.append(f"{b.path.name}: " + "; ".join(file_problems))

        # compare the two newest valid backups, to catch a scrape that silently lost data
        if cfg.max_shrink_percent is not None and len(file_counts) >= 2:
            prev, cur = file_counts[-2], file_counts[-1]
            for table, before in prev.items():
                after = cur.get(table)
                if after is not None and before > 0 and (shrink := (before - after) / before * 100) > cfg.max_shrink_percent:
                    status = max(status, Status.WARN)
                    problems.append(f"{table} shrank {shrink:.0f}% vs previous backup ({before} -> {after} rows)")

        settled_newest = backups[-1].path.name
        message = "; ".join(problems) or f"{len(backups)} backups, newest {settled_newest} valid"
        if writing:
            message += f" ({len(writing)} being written)"
        return CheckResult(name, status, message, details)
