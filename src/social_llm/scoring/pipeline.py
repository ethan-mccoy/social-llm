from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import polars as pl
from tqdm import tqdm

from social_llm.llm.client import LLMClient
from social_llm.models import Post, SchizotypalRating, ScoredPost
from social_llm.threads.client import ThreadsClient

logger = logging.getLogger(__name__)

PROCESSED_DIR = Path("data/processed")


def collect_network(
    seed_post_id: str,
    posts_per_user: int = 100,
    client: ThreadsClient | None = None,
) -> pl.DataFrame:
    """Collect reply network from a seed post: get repliers, fetch their timelines.

    Returns a DataFrame of all posts from all repliers, saved to parquet.
    """
    own_client = client is None
    client = client or ThreadsClient()
    try:
        # 1. Get replies to the seed post
        logger.info("Fetching conversation for seed post %s", seed_post_id)
        replies = client.get_conversation(seed_post_id)
        logger.info("Found %d replies", len(replies))

        # 2. Get unique repliers
        usernames = sorted({r.username for r in replies if r.username})
        logger.info("Found %d unique repliers", len(usernames))

        # 3. Fetch each replier's timeline
        all_posts: list[Post] = []
        for username in tqdm(usernames, desc="Fetching user timelines"):
            try:
                user_posts = client.get_user_threads(username, limit=posts_per_user)
                all_posts.extend(user_posts)
            except Exception:
                logger.warning("Failed to fetch posts for %s, skipping", username)

        # 4. Convert to DataFrame and save
        df = _posts_to_df(all_posts)
        out_path = PROCESSED_DIR / f"network_{seed_post_id}.parquet"
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        df.write_parquet(out_path)
        logger.info("Saved %d posts from %d users to %s", len(df), len(usernames), out_path)
        return df

    finally:
        if own_client:
            client.close()


async def score_dataset(
    posts_df: pl.DataFrame,
    output_name: str = "scored",
    batch_size: int = 50,
    llm: LLMClient | None = None,
) -> pl.DataFrame:
    """Score all posts in a DataFrame for schizotypal traits.

    Processes in batches, supports incremental re-runs by skipping already-scored posts.
    """
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PROCESSED_DIR / f"{output_name}.parquet"

    # Load existing scores if resuming
    already_scored: set[str] = set()
    existing_rows: list[dict] = []
    if out_path.exists():
        existing = pl.read_parquet(out_path)
        already_scored = set(existing["post_id"].to_list())
        existing_rows = existing.to_dicts()
        logger.info("Resuming: %d posts already scored", len(already_scored))

    # Filter to unscored posts
    to_score = posts_df.filter(~pl.col("id").is_in(already_scored))
    if to_score.is_empty():
        logger.info("All posts already scored")
        return pl.read_parquet(out_path)

    logger.info("Scoring %d posts (%d already done)", len(to_score), len(already_scored))

    own_llm = llm is None
    llm = llm or LLMClient()

    try:
        posts = [
            Post(
                id=row["id"],
                user_id=row["user_id"],
                username=row["username"],
                text=row["text"],
                timestamp=row["timestamp"],
            )
            for row in to_score.to_dicts()
        ]

        new_rows: list[dict] = []
        for i in tqdm(range(0, len(posts), batch_size), desc="Scoring batches"):
            batch = posts[i : i + batch_size]
            ratings = await llm.score_posts_batch(batch)
            for post, rating in zip(batch, ratings):
                if rating is None:
                    continue
                new_rows.append(_scored_post_to_row(post, rating))

        all_rows = existing_rows + new_rows
        result = pl.DataFrame(all_rows)
        result.write_parquet(out_path)
        logger.info("Saved %d scored posts to %s", len(result), out_path)
        return result

    finally:
        if own_llm:
            await llm.close()


def _posts_to_df(posts: list[Post]) -> pl.DataFrame:
    return pl.DataFrame([p.model_dump() for p in posts])


def _scored_post_to_row(post: Post, rating: SchizotypalRating) -> dict:
    return {
        "post_id": post.id,
        "user_id": post.user_id,
        "username": post.username,
        "text": post.text,
        "timestamp": post.timestamp,
        "magical_thinking": rating.magical_thinking,
        "ideas_of_reference": rating.ideas_of_reference,
        "unusual_perceptions": rating.unusual_perceptions,
        "paranoid_ideation": rating.paranoid_ideation,
        "odd_speech": rating.odd_speech,
        "social_anxiety": rating.social_anxiety,
        "cannabis_mention": rating.cannabis_mention,
        "cannabis_context": rating.cannabis_context,
        "reasoning": rating.reasoning,
    }
