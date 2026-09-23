from fastapi import APIRouter
from db_server.src.database import get_db

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/reset")
def reset(backup: bool = True):
    """
    Backups then deletes the `apps` table so the next weekly scrape starts clean.
    The `reviews` table is kept.
    """
    
    get_db().reset_apps(backup=backup)
    return {"ok": True}


@router.post("/backup")
def backup():
    """
    Copies the `apps` table into a new SQLite file in the `backups/` directory.
    The live table is not changed.
    """

    dest = get_db().backup_apps_table()
    return {"backup": str(dest)}


@router.post("/export-reviews")
def export_reviews():
    """
    Copies the `reviews` table into a new SQLite file (`<db>_reviews`) in the `backups/` directory.
    The live table is not changed.
    """
    
    dest = get_db().backup_reviews_table()
    return {"export": str(dest)}
