import argparse
import logging
import uvicorn
from logging.handlers import RotatingFileHandler
from pathlib import Path
import db_server.src.database as database
from db_server.src.api import app

log = logging.getLogger(__name__)


def setup_logging(log_file: str = "logs/db_server.log") -> None:
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(), file_handler],
    )


def main():
    parser = argparse.ArgumentParser(prog="Steam scraper DB server", description="SQLite storage server")
    parser.add_argument("-o", "--output", default="steam.db", help="SQLite database file path")
    parser.add_argument("-p", "--port", type=int, default=8001, help="Port to listen on")
    parser.add_argument("--log-file", default="logs/db_server.log", help="Log file path")
    args = parser.parse_args()

    setup_logging(args.log_file)
    database.init(args.output)
    log.info("DB server starting on port %d, database: %s", args.port, args.output)

    uvicorn.run(app, host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
