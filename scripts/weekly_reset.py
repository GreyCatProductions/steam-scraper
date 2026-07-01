import argparse
import sqlite3
from datetime import date
from pathlib import Path


def next_backup_path(backup_dir: Path, stem: str) -> Path:
    today = date.today().isoformat()
    counter = 1
    while True:
        path = backup_dir / f"{stem}_{today}_{counter:03d}.db"
        if not path.exists():
            return path
        counter += 1


def reset(db: str = "steam.db", backup_dir: str = "backups") -> None:
    db_path = Path(db)
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        return

    bdir = Path(backup_dir)
    bdir.mkdir(exist_ok=True)

    dest = next_backup_path(bdir, db_path.stem)
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    print(f"Backed up to {dest}")

    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM apps")
        conn.close()
    print(f"Cleared apps table in {db_path}")


def main():
    parser = argparse.ArgumentParser(description="Back up the DB and reset scrape state for a fresh weekly run")
    parser.add_argument("--db", default="steam.db")
    parser.add_argument("--backup-dir", default="backups")
    args = parser.parse_args()
    reset(args.db, args.backup_dir)


if __name__ == "__main__":
    main()
