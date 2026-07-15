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


def backup_apps_table(db_path: Path, backup_dir: Path) -> Path:
    '''
        Snapshots the apps table
    '''
    backup_dir.mkdir(exist_ok=True)
    dest = next_backup_path(backup_dir, db_path.stem)
    dst = sqlite3.connect(dest)
    try:
        dst.execute("ATTACH DATABASE ? AS src", (str(db_path),))
        dst.execute("CREATE TABLE apps AS SELECT * FROM src.apps")
        dst.execute("DETACH DATABASE src")
        dst.commit()
    finally:
        dst.close()
    return dest


def backup_reviews_table(db_path: Path, backup_dir: Path) -> Path:
    '''
        On-demand snapshot of the reviews table for offline analysis. Not part of the
        weekly reset - the live reviews table is never cleared or touched by it.
    '''
    backup_dir.mkdir(exist_ok=True)
    dest = next_backup_path(backup_dir, f"{db_path.stem}_reviews")
    dst = sqlite3.connect(dest)
    try:
        dst.execute("ATTACH DATABASE ? AS src", (str(db_path),))
        dst.execute("CREATE TABLE reviews AS SELECT * FROM src.reviews")
        dst.execute("DETACH DATABASE src")
        dst.commit()
    finally:
        dst.close()
    return dest


def reset(db: str = "steam.db", backup_dir: str = "backups") -> None:
    db_path = Path(db)
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        return

    dest = backup_apps_table(db_path, Path(backup_dir))
    print(f"Backed up apps table to {dest}")

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DELETE FROM apps")
        conn.commit()
    finally:
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
