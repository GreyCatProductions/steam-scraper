from dataclasses import dataclass
from typing import Optional


@dataclass
class UserReview:
    recommendation_id: int
    appid: int
    # author
    author_steamid: str
    author_num_games_owned: int
    author_num_reviews: int
    author_playtime_forever: int
    author_playtime_last_two_weeks: int
    author_playtime_at_review: int
    author_deck_playtime_at_review: int
    author_last_played: int
    # review
    language: str
    review: str
    timestamp_created: int
    timestamp_updated: int
    voted_up: bool
    votes_up: int
    votes_funny: int
    weighted_vote_score: float
    comment_count: int
    steam_purchase: bool
    received_for_free: bool
    written_during_early_access: bool
    primarily_steam_deck: bool
    developer_response: str
    timestamp_dev_responded: Optional[int]

    @classmethod
    def from_dict(cls, appid: int, data: dict) -> "UserReview":
        author = data["author"]
        return cls(
            recommendation_id=int(data["recommendationid"]),
            appid=appid,
            author_steamid=author["steamid"],
            author_num_games_owned=author.get("num_games_owned", 0),
            author_num_reviews=author.get("num_reviews", 0),
            author_playtime_forever=author.get("playtime_forever", 0),
            author_playtime_last_two_weeks=author.get("playtime_last_two_weeks", 0),
            author_playtime_at_review=author.get("playtime_at_review", 0),
            author_deck_playtime_at_review=author.get("deck_playtime_at_review", 0),
            author_last_played=author.get("last_played", 0),
            language=data["language"],
            review=data["review"],
            timestamp_created=data["timestamp_created"],
            timestamp_updated=data["timestamp_updated"],
            voted_up=data["voted_up"],
            votes_up=data["votes_up"],
            votes_funny=data["votes_funny"],
            weighted_vote_score=float(data["weighted_vote_score"]),
            comment_count=data["comment_count"],
            steam_purchase=data["steam_purchase"],
            received_for_free=data["received_for_free"],
            written_during_early_access=data["written_during_early_access"],
            primarily_steam_deck=data.get("primarily_steam_deck", False),
            developer_response=data.get("developer_response", ""),
            timestamp_dev_responded=data.get("timestamp_dev_responded"),
        )
