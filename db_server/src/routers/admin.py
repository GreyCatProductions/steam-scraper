from pathlib import Path
from fastapi import APIRouter
from db_server.src.database import get_path
from scripts.weekly_reset import reset as weekly_reset, backup_apps_table, backup_reviews_table

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/reset")
def reset():
    weekly_reset(db=get_path())
    return {"ok": True}


@router.post("/backup")
def backup():
    dest = backup_apps_table(Path(get_path()), Path("backups"))
    return {"backup": str(dest)}


@router.post("/export-reviews")
def export_reviews():
    dest = backup_reviews_table(Path(get_path()), Path("backups"))
    return {"export": str(dest)}
