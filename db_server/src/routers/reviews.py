from fastapi import APIRouter
from db_server.src.database import get_db
from shared.schema.review import UserReview

router = APIRouter(prefix="/reviews", tags=["reviews"])


@router.post("")
def save_reviews(reviews: list[UserReview]):
    """ 
        Adds or updates reviews by recommendation_id. 
    """
    get_db().save_reviews(reviews)
    return {"saved": len(reviews)}


@router.get("/latest-timestamp/{appid}")
def get_latest_timestamp(appid: int):
    """ 
        Returns the creation time of the app's newest stored review,
        or 0 if it has none.
    """
    return {"timestamp": get_db().get_latest_review_timestamp(appid)}


@router.post("/done/{appid}")
def mark_done(appid: int):
    """ 
        reports that all reviews are fetched for given appid for 
        the weeks iteration
    """
    get_db().mark_reviews_done(appid)
    return {"ok": True}
