from fastapi import APIRouter
from db_server.src.database import get_db
from shared.schema.review import UserReview

router = APIRouter(prefix="/reviews", tags=["reviews"])


@router.post("")
def save_reviews(reviews: list[UserReview]):
    get_db().save_reviews(reviews)
    return {"saved": len(reviews)}


@router.get("/latest-timestamp/{appid}")
def get_latest_timestamp(appid: int):
    return {"timestamp": get_db().get_latest_review_timestamp(appid)}


@router.post("/done/{appid}")
def mark_done(appid: int):
    get_db().mark_reviews_done(appid)
    return {"ok": True}


@router.delete("/orphaned")
def delete_orphaned():
    removed = get_db().delete_orphaned_reviews()
    return {"removed": removed}
