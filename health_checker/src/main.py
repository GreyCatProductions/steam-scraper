import argparse
import json
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn

from health_checker.src.api import create_app
from health_checker.src.checks import Status
from health_checker.src.config import load_config
from health_checker.src.runner import HealthRunner

log = logging.getLogger(__name__)


def setup_logging(log_file: str) -> None:
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(), file_handler],
    )


def lower_priority() -> None:
    """Gives the scraper the CPU first. On Linux this also lowers disk priority (it follows the nice value) with the
    BFQ/CFQ IO schedulers; mq-deadline and none, common for NVMe, ignore it."""
    try:
        os.nice(19)
    except (AttributeError, OSError) as e:  # AttributeError: not available on Windows
        log.info("couldn't lower process priority: %s", e)


def main():
    parser = argparse.ArgumentParser(
        prog="Steam scraper health checker",
        description="Pings the FastAPI services, watches scrape progress and validates the live databases and the "
                    "backups folder against expected sizes and row counts. Serves the results on GET /health.",
    )
    parser.add_argument("-c", "--config", type=Path, default=Path("health.json"),
                        help="JSON config, see health_checker/health.example.json (default health.json)")
    parser.add_argument("--once", action="store_true",
                        help="run every check once, print the report and exit (code 1 on fail, 0 otherwise), "
                             "e.g. for cron or CI")
    parser.add_argument("--host", help="overrides the config's host")
    parser.add_argument("-p", "--port", type=int, help="overrides the config's port")
    parser.add_argument("--log-file", default="logs/health_checker.log", help="Log file path")
    args = parser.parse_args()

    if not args.config.is_file():
        parser.error(f"config not found: {args.config} (copy health_checker/health.example.json)")
    try:
        cfg = load_config(args.config)
    except (ValueError, TypeError, KeyError) as e:
        parser.error(f"invalid config {args.config}: {e}")

    setup_logging(args.log_file)
    lower_priority()
    runner = HealthRunner(cfg)

    if args.once:
        runner.run_all()
        report = runner.report()
        print(json.dumps(report, indent=2))
        raise SystemExit(1 if report["status"] == str(Status.FAIL) else 0)

    runner.start()
    host, port = args.host or cfg.host, args.port or cfg.port
    log.info("Health checker on http://%s:%d/health", host, port)
    uvicorn.run(create_app(runner), host=host, port=port)


if __name__ == "__main__":
    main()
