from fastapi import APIRouter
from processing_server.src.db_client import get_client
from shared.schema.review import UserReview

router = APIRouter(prefix="/reviews", tags=["reviews"])


@router.get("/latest-timestamp/{appid}")
def get_latest_review_timestamp(appid: int):
    return {"timestamp": get_client().get_latest_review_timestamp(appid)}


@router.post("/results")
def submit_reviews(reviews: list[UserReview]):
    get_client().save_reviews(reviews)
    return {"saved": len(reviews)}


@router.post("/done/{appid}")
def mark_reviews_done(appid: int):
    get_client().mark_reviews_done(appid)
    return {"ok": True}