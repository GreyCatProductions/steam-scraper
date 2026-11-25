import logging
from collections.abc import Callable
import threading
import time

import requests

from health_checker.src.checks import (BackupValidator, CheckResult, DatabaseChecker, ProgressWatcher, QualityWatcher,
                                       ServicePinger, Status, check_disk)
from health_checker.src.config import Config

log = logging.getLogger(__name__)

TICK_SECONDS = 5


class HealthRunner:
    """Runs each group of checks on its own interval in a background thread and keeps the latest result per check."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._pinger = ServicePinger()
        self._progress = ProgressWatcher()
        self._quality = QualityWatcher()
        self._databases = DatabaseChecker()
        self._backups = BackupValidator()
        self._results: dict[str, CheckResult] = {}
        self._lock = threading.Lock()
        self._next_run: dict[str, float] = {}
        # one lock per group, so a manual /check can't run a group at the same time as the loop
        self._group_locks = {g: threading.Lock() for g in self._groups()}

    def _groups(self) -> dict[str, tuple[float, Callable[[], list[CheckResult]]]]:
        iv = self.cfg.intervals
        return {
            "services": (iv.services_seconds, self._run_services),
            "progress": (iv.progress_seconds, self._run_progress),
            "quality": (iv.quality_seconds, self._run_quality),
            "disk": (iv.disk_seconds, self._run_disk),
            "databases": (iv.databases_seconds, self._run_databases),
            "backups": (iv.backups_seconds, self._run_backups),
        }

    def _run_services(self) -> list[CheckResult]:
        return [self._pinger.ping(s) for s in self.cfg.services]

    def _run_progress(self) -> list[CheckResult]:
        return [self._progress.check(self.cfg.progress)] if self.cfg.progress else []

    def _run_quality(self) -> list[CheckResult]:
        return [self._quality.check(self.cfg.quality)] if self.cfg.quality else []

    def _run_disk(self) -> list[CheckResult]:
        return [check_disk(d) for d in self.cfg.disks]

    def _run_databases(self) -> list[CheckResult]:
        # runs after the progress group (dict order), so the idle state is current
        idle = self._progress.scrape_idle
        return [self._databases.check(d, idle) for d in self.cfg.databases]

    def _run_backups(self) -> list[CheckResult]:
        return [self._backups.check(self.cfg.backup_dir, b) for b in self.cfg.backups]

    @property
    def group_names(self) -> list[str]:
        return list(self._groups())

    def run_group(self, group: str) -> list[CheckResult]:
        interval, fn = self._groups()[group]
        with self._group_locks[group]:
            try:
                results = fn()
            except Exception as e:  # a bug in a check must not kill the loop
                log.exception("check group %s crashed", group)
                results = [CheckResult(f"{group}:internal", Status.FAIL, f"checker crashed: {e!r}")]
        for r in results:
            r.interval = interval
            self._record(r)
        return results

    def run_all(self) -> list[CheckResult]:
        return [r for g in self._groups() for r in self.run_group(g)]

    def _record(self, result: CheckResult) -> None:
        with self._lock:
            previous = self._results.get(result.name)
            self._results[result.name] = result
        if previous is None or previous.status != result.status:
            level = logging.INFO if result.status == Status.OK else logging.WARNING
            log.log(level, "%s: %s -> %s (%s)", result.name, previous.status if previous else "new", result.status,
                    result.message)
            # skip the first report of a check that is fine, so a restart doesn't spam the webhook
            if previous is not None or result.status != Status.OK:
                self._notify(result, previous)

    def _notify(self, result: CheckResult, previous: CheckResult | None) -> None:
        if not self.cfg.webhook_url:
            return
        icon = {Status.OK: "✅", Status.WARN: "⚠️", Status.FAIL: "❌"}[result.status]
        text = f"{icon} steam-scraper {result.name} is {result.status}: {result.message}"
        try:
            # "content" for Discord, "text" for Slack and most others
            requests.post(self.cfg.webhook_url, json={"text": text, "content": text}, timeout=10)
        except requests.RequestException as e:
            log.warning("webhook failed: %s", e)

    def report(self) -> dict:
        with self._lock:
            results = sorted(self._results.values(), key=lambda r: (-r.status, r.name))
        overall = max((r.status for r in results), default=Status.OK)
        return {"status": str(overall), "checked_at": int(time.time()), "checks": [r.to_dict() for r in results]}

    def loop(self) -> None:
        while True:
            now = time.time()
            for group, (interval, _) in self._groups().items():
                if now >= self._next_run.get(group, 0):
                    self._next_run[group] = now + interval
                    self.run_group(group)
            time.sleep(TICK_SECONDS)

    def start(self) -> None:
        threading.Thread(target=self.loop, daemon=True, name="health-loop").start()
