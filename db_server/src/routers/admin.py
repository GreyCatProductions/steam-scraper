from fastapi import APIRouter
from db_server.src.database import get_db, get_path
from scripts.weekly_reset import reset as weekly_reset

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/reset")
def reset():
    path = get_path()
    weekly_reset(db=path)
    return {"ok": True}
