import sqlite3
from pathlib import Path
from fastapi import APIRouter
from db_server.src.database import get_path
from scripts.weekly_reset import reset as weekly_reset, next_backup_path

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/reset")
def reset():
    weekly_reset(db=get_path())
    return {"ok": True}


@router.post("/backup")
def backup():
    db_path = Path(get_path())
    bdir = Path("backups")
    bdir.mkdir(exist_ok=True)
    dest = next_backup_path(bdir, db_path.stem)
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return {"backup": str(dest)}
