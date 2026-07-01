from fastapi import APIRouter
from server.src.database import get_db
from shared.schema.review import UserReview

router = APIRouter(prefix="/reviews", tags=["reviews"])


@router.post("/results")
def submit_reviews(reviews: list[UserReview]):
    get_db().save_reviews(reviews)
    return {"saved": len(reviews)}


@router.post("/done/{appid}")
def mark_reviews_done(appid: int):
    get_db().mark_reviews_done(appid)
    return {"ok": True}