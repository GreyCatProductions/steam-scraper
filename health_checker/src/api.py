import threading
import time

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse

from health_checker.src.runner import HealthRunner

ICONS = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL"}
# manual checks read the databases too, so they are rate limited: anyone who can reach the API can trigger them
MANUAL_CHECK_COOLDOWN_SECONDS = 60


def create_app(runner: HealthRunner) -> FastAPI:
    app = FastAPI(title="steam-scraper health checker")
    last_manual: dict[str, float] = {}
    manual_lock = threading.Lock()

    @app.get("/")
    def root():
        return {"status": "ok"}

    @app.get("/health")
    def health():
        """Latest result of every check. HTTP 503 when anything fails, so uptime monitors can watch this URL."""
        report = runner.report()
        return JSONResponse(report, status_code=503 if report["status"] == "fail" else 200)

    @app.get("/health/summary", response_class=PlainTextResponse)
    def summary():
        """One line per check, for a quick look with curl."""
        report = runner.report()
        lines = [f"overall: {report['status'].upper()}", ""]
        lines += [f"[{ICONS[c['status']]}] {c['name']}: {c['message']}" for c in report["checks"]]
        return PlainTextResponse("\n".join(lines) + "\n", status_code=503 if report["status"] == "fail" else 200)

    @app.post("/check")
    def check_now(group: str | None = None):
        """Runs checks right away instead of waiting for their interval: all, or one group
        (services, progress, quality, disk, databases, backups)."""
        if group is not None and group not in runner.group_names:
            raise HTTPException(status_code=400, detail=f"unknown group {group!r}")
        key = group or "all"
        with manual_lock:
            wait = MANUAL_CHECK_COOLDOWN_SECONDS - (time.monotonic() - last_manual.get(key, float("-inf")))
            if wait > 0:
                raise HTTPException(status_code=429, detail=f"checked manually just now, try again in {wait:.0f}s")
            last_manual[key] = time.monotonic()
        if group is None:
            runner.run_all()
        else:
            runner.run_group(group)
        return runner.report()

    return app
