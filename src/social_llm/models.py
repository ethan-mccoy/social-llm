from datetime import datetime

from pydantic import BaseModel


class Post(BaseModel):
    id: str
    user_id: str
    username: str
    text: str
    timestamp: datetime
    like_count: int = 0
    reply_count: int = 0


class UserProfile(BaseModel):
    id: str
    username: str
    bio: str | None = None
    followers_count: int = 0
    following_count: int = 0


class SchizotypalRating(BaseModel):
    """Per-post LLM rating on schizotypal dimensions (0-5 scale)."""

    magical_thinking: int
    ideas_of_reference: int
    unusual_perceptions: int
    paranoid_ideation: int
    odd_speech: int
    social_anxiety: int
    cannabis_mention: bool
    cannabis_context: str | None = None
    reasoning: str


class ScoredPost(BaseModel):
    post: Post
    rating: SchizotypalRating
