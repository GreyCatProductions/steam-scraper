from fastapi import APIRouter
from server.src.database import get_db
from shared.schema.review import UserReview

router = APIRouter(prefix="/reviews", tags=["reviews"])


@router.post("/results")
def submit_reviews(reviews: list[UserReview]):
    get_db().save_reviews(reviews)
    return {"saved": len(reviews)}