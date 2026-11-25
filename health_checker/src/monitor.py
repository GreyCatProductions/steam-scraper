"""Live terminal dashboard, meant to run on another device that can reach the scraper machine over the network.

It shows two things:
  - the report of the health checker service running on the scraper machine (GET /health), which is the only
    thing that can read the databases and the backups folder there
  - its own pings to every service, so a dead health checker, a crashed service or a network problem between
    this device and the scraper still shows up

    python -m health_checker.src.monitor --host 192.168.1.50
"""
import argparse
import time
from collections import deque
from datetime import datetime

import requests
from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from health_checker.src.checks import CheckResult, ServicePinger, Status
from health_checker.src.config import ServiceConfig

DEFAULT_PORTS = {"processing_server": 8000, "db_server": 8001, "playercount_fetching": 8002}
DEFAULT_CHECKER_PORT = 8003
STYLES = {Status.OK: "green", Status.WARN: "yellow", Status.FAIL: "bold red"}
LABELS = {Status.OK: " OK ", Status.WARN: "WARN", Status.FAIL: "FAIL"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="Steam scraper health monitor",
        description="Live terminal view of the scraper's health. Polls the health checker service on the scraper "
                    "machine and pings every service itself. Ctrl+C quits.",
    )
    parser.add_argument("--host", default="127.0.0.1",
                        help="address of the scraper machine; the services are expected on their default ports "
                             f"{DEFAULT_PORTS} and the health checker on {DEFAULT_CHECKER_PORT}")
    parser.add_argument("--checker", metavar="URL",
                        help=f"health checker URL (default http://HOST:{DEFAULT_CHECKER_PORT})")
    parser.add_argument("--service", action="append", default=[], metavar="NAME=URL",
                        help="ping this service instead of the defaults, repeatable, "
                             "e.g. --service db_server=http://10.0.0.5:9001")
    parser.add_argument("--no-ping", action="store_true", help="only show the health checker's report")
    parser.add_argument("-i", "--interval", type=float, default=10, help="seconds between refreshes (default 10)")
    parser.add_argument("--stale-after", type=float, default=300,
                        help="minimum seconds before a check result counts as stale (default 300); slower checks "
                             "count as stale once they missed two of their runs")
    parser.add_argument("--bell", action="store_true", help="ring the terminal bell when something starts failing")
    args = parser.parse_args()

    args.checker = (args.checker or f"http://{args.host}:{DEFAULT_CHECKER_PORT}").rstrip("/")
    services = {}
    for item in args.service:
        name, sep, url = item.partition("=")
        if not sep or not url:
            parser.error(f"--service needs NAME=URL, got {item!r}")
        services[name] = url
    if not services and not args.no_ping:
        services = {name: f"http://{args.host}:{port}" for name, port in DEFAULT_PORTS.items()}
    args.services = [] if args.no_ping else [ServiceConfig(name=n, url=u, fail_after=2) for n, u in services.items()]
    return args


class Monitor:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.pinger = ServicePinger()
        self.local: list[CheckResult] = []
        self.remote: list[dict] = []
        self.remote_time: float | None = None  # checker's clock, so result ages don't depend on clock sync
        self.checker_error: str | None = None
        self.last_refresh = 0.0
        self.events: deque[tuple[str, Status, str]] = deque(maxlen=8)
        self._last_status: dict[str, Status] = {}

    def refresh(self) -> None:
        self.local = [self.pinger.ping(s) for s in self.args.services]
        try:
            r = requests.get(self.args.checker + "/health", timeout=10)
            # /health answers 503 when something fails, which is still a valid report
            report = r.json()
            self.remote, self.remote_time, self.checker_error = report["checks"], report["checked_at"], None
        except requests.Timeout:
            self.checker_error = "no answer within 10s"
        except requests.ConnectionError as e:
            self.checker_error = ("connection refused (checker not running, or listening on 127.0.0.1 only?)"
                                  if "refused" in str(e).lower() else "unreachable (network/host down?)")
        except (requests.RequestException, ValueError, KeyError) as e:
            self.checker_error = f"{type(e).__name__}: {e}"
        self.last_refresh = time.time()
        self._track_changes()

    def rows(self) -> list[tuple[Status, str, str, str]]:
        """(status, check, message, age) for every row, worst first."""
        rows = []
        if self.checker_error:
            rows.append((Status.FAIL, "health checker", f"unreachable at {self.args.checker}: {self.checker_error}", ""))
        for c in self.remote:
            status = Status[c["status"].upper()]
            age = (self.remote_time or c["checked_at"]) - c["checked_at"]
            # a result is stale once it missed two of its runs, never earlier than --stale-after
            limit = max(self.args.stale_after, 2 * (c.get("interval") or 0) + 60)
            message = c["message"]
            if age > limit:
                status = max(status, Status.WARN)
                message = f"(stale result) {message}"
            rows.append((status, c["name"], message, _age(age)))
        rows += [(r.status, f"ping from here:{r.name.removeprefix('service:')}", r.message, "now") for r in self.local]
        return sorted(rows, key=lambda r: -r[0])

    def _track_changes(self) -> None:
        new_fail = False
        for status, name, message, _ in self.rows():
            before = self._last_status.get(name)
            if before is not None and before != status:
                self.events.appendleft((datetime.now().strftime("%H:%M:%S"), status, f"{name}: {before} -> {status}  {message}"))
                new_fail |= status == Status.FAIL
            self._last_status[name] = status
        if new_fail and self.args.bell:
            print("\a", end="", flush=True)

    def render(self) -> Group:
        rows = self.rows()
        overall = max((r[0] for r in rows), default=Status.OK)
        counts = {s: sum(r[0] == s for r in rows) for s in Status}

        header = Text.assemble(
            (f" {overall.name} ", f"reverse {STYLES[overall]}"),
            f"  {counts[Status.OK]} ok · {counts[Status.WARN]} warn · {counts[Status.FAIL]} fail",
            ("   checker ", "dim"), self.args.checker,
            ("   updated ", "dim"), datetime.fromtimestamp(self.last_refresh).strftime("%H:%M:%S") if self.last_refresh else "-",
            ("   every ", "dim"), f"{self.args.interval:g}s",
        )

        table = Table(box=box.SIMPLE_HEAD, expand=True, show_edge=False)
        table.add_column("", min_width=6, no_wrap=True)
        # a long check name (e.g. a disk path) must not squeeze the details away
        table.add_column("check", style="bold", no_wrap=True, max_width=34, overflow="ellipsis")
        table.add_column("details", ratio=1, min_width=30, overflow="fold")
        table.add_column("age", justify="right", style="dim", no_wrap=True)
        for status, name, message, age in rows:
            table.add_row(Text(LABELS[status], style=STYLES[status]), name, message, age)

        events = Table.grid(padding=(0, 1))
        for ts, status, text in self.events:
            events.add_row(Text(ts, style="dim"), Text(LABELS[status], style=STYLES[status]), text)
        if not self.events:
            events.add_row(Text("no status changes since start", style="dim"))

        return Group(
            Panel(table, title=header, title_align="left", border_style=STYLES[overall]),
            Panel(events, title="recent changes", title_align="left", border_style="dim"),
        )


def _age(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 90:
        return f"{seconds}s"
    if seconds < 5400:
        return f"{seconds // 60}m"
    return f"{seconds / 3600:.1f}h"


def main():
    args = parse_args()
    monitor = Monitor(args)
    console = Console()
    try:
        with Live(Text("connecting..."), console=console, screen=True, auto_refresh=False) as live:
            while True:
                monitor.refresh()
                live.update(monitor.render(), refresh=True)
                time.sleep(args.interval)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
