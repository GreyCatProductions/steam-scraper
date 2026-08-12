from fastapi import APIRouter
from db_server.src.database import get_db

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/reset")
def reset():
    get_db().reset_apps()
    return {"ok": True}


@router.post("/backup")
def backup():
    dest = get_db().backup_apps_table()
    return {"backup": str(dest)}


@router.post("/export-reviews")
def export_reviews():
    dest = get_db().backup_reviews_table()
    return {"export": str(dest)}
