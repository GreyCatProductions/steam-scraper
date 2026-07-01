import argparse
import shutil
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
    shutil.copy2(db_path, dest)
    print(f"Backed up to {dest}")

    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM apps")
    conn.commit()
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
