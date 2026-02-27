from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import httpx

from social_llm.config import settings
from social_llm.models import Post, UserProfile

logger = logging.getLogger(__name__)

BASE_URL = "https://graph.threads.net/v1.0"

POST_FIELDS = "id,text,username,timestamp,media_type,permalink"
CONVERSATION_FIELDS = "id,text,username,timestamp"
PROFILE_FIELDS = "id,username,threads_biography,name"
INSIGHT_METRICS = "views,likes,replies,reposts,quotes"


class ThreadsClient:
    """Client for the Meta Threads API (graph.threads.net).

    Handles pagination, rate limiting, and raw response caching.
    """

    def __init__(
        self,
        access_token: str | None = None,
        raw_data_dir: str | Path = "data/raw",
    ):
        self.access_token = access_token or settings.threads_access_token
        self.raw_dir = Path(raw_data_dir)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self._client = httpx.Client(
            base_url=BASE_URL,
            params={"access_token": self.access_token},
            timeout=30,
        )

    def _get(self, path: str, params: dict | None = None) -> dict:
        params = params or {}
        resp = self._client.get(path, params=params)
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", "60"))
            raise RateLimitError(f"Rate limited. Retry after {retry_after}s", retry_after)
        resp.raise_for_status()
        return resp.json()

    def _get_paginated(self, path: str, params: dict | None = None, max_pages: int = 50) -> list[dict]:
        """Fetch all pages from a paginated endpoint."""
        params = params or {}
        all_data: list[dict] = []
        for _ in range(max_pages):
            result = self._get(path, params)
            data = result.get("data", [])
            all_data.extend(data)
            paging = result.get("paging", {})
            next_cursor = paging.get("cursors", {}).get("after")
            if not next_cursor or not data:
                break
            params["after"] = next_cursor
        return all_data

    def _save_raw(self, name: str, data: list[dict] | dict) -> None:
        path = self.raw_dir / f"{name}.json"
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)
        logger.info("Saved raw data to %s (%d items)", path, len(data) if isinstance(data, list) else 1)

    # ── Public methods ──────────────────────────────────────────────

    def get_conversation(self, media_id: str, save: bool = True) -> list[Post]:
        """Get all replies in a conversation thread."""
        raw = self._get_paginated(
            f"/{media_id}/conversation",
            params={"fields": CONVERSATION_FIELDS},
        )
        if save:
            self._save_raw(f"conversation_{media_id}", raw)
        return [_parse_post(item) for item in raw if item.get("text")]

    def get_replies(self, media_id: str, save: bool = True) -> list[Post]:
        """Get direct replies to a post."""
        raw = self._get_paginated(
            f"/{media_id}/replies",
            params={"fields": CONVERSATION_FIELDS},
        )
        if save:
            self._save_raw(f"replies_{media_id}", raw)
        return [_parse_post(item) for item in raw if item.get("text")]

    def get_user_threads(
        self,
        user_id: str = "me",
        limit: int | None = None,
        save: bool = True,
    ) -> list[Post]:
        """Get a user's posts. Use 'me' for the authenticated user."""
        limit = limit or settings.threads_posts_per_user
        raw = self._get_paginated(
            f"/{user_id}/threads",
            params={"fields": POST_FIELDS, "limit": min(limit, 100)},
        )
        # Respect the overall limit
        raw = raw[:limit]
        if save:
            safe_id = user_id.replace("/", "_")
            self._save_raw(f"threads_{safe_id}", raw)
        return [_parse_post(item) for item in raw if item.get("text")]

    def get_post(self, media_id: str) -> Post:
        """Get a single post by ID."""
        raw = self._get(f"/{media_id}", params={"fields": POST_FIELDS})
        return _parse_post(raw)

    def get_post_insights(self, media_id: str) -> dict:
        """Get engagement metrics for a post."""
        result = self._get(f"/{media_id}/insights", params={"metric": INSIGHT_METRICS})
        data = result.get("data", [])
        return {item["name"]: item["values"][0]["value"] for item in data}

    def get_profile(self, user_id: str = "me") -> UserProfile:
        """Get a user profile."""
        raw = self._get(f"/{user_id}", params={"fields": PROFILE_FIELDS})
        return UserProfile(
            id=raw["id"],
            username=raw.get("username", ""),
            bio=raw.get("threads_biography"),
        )

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class RateLimitError(Exception):
    def __init__(self, message: str, retry_after: int):
        super().__init__(message)
        self.retry_after = retry_after


def _parse_post(item: dict) -> Post:
    return Post(
        id=item["id"],
        user_id=item.get("owner", {}).get("id", "") if isinstance(item.get("owner"), dict) else item.get("id", ""),
        username=item.get("username", ""),
        text=item.get("text", ""),
        timestamp=datetime.fromisoformat(item["timestamp"]) if item.get("timestamp") else datetime.min,
    )
