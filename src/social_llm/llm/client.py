from __future__ import annotations

import asyncio
import json
import logging

from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from social_llm.config import settings
from social_llm.models import Post, SchizotypalRating
from social_llm.llm.prompts import SCHIZOTYPAL_SYSTEM_PROMPT, SCHIZOTYPAL_USER_PROMPT

logger = logging.getLogger(__name__)


class LLMClient:
    """DeepInfra LLM client for structured trait scoring."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        concurrency: int | None = None,
    ):
        self.model = model or settings.deepinfra_model
        self._client = AsyncOpenAI(
            api_key=api_key or settings.deepinfra_token,
            base_url=base_url or settings.deepinfra_base_url,
        )
        self._semaphore = asyncio.Semaphore(concurrency or settings.scoring_concurrency)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=2, max=30),
        retry=retry_if_exception_type((Exception,)),
        before_sleep=lambda rs: logger.warning("Retrying LLM call (attempt %d)", rs.attempt_number),
    )
    async def score_post(
        self,
        post_text: str,
        system_prompt: str = SCHIZOTYPAL_SYSTEM_PROMPT,
        user_prompt_template: str = SCHIZOTYPAL_USER_PROMPT,
    ) -> SchizotypalRating:
        """Score a single post for schizotypal traits."""
        async with self._semaphore:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt_template.format(post_text=post_text)},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
            )
            raw = response.choices[0].message.content
            data = json.loads(raw)
            return SchizotypalRating(**data)

    async def score_posts_batch(
        self,
        posts: list[Post],
        system_prompt: str = SCHIZOTYPAL_SYSTEM_PROMPT,
        user_prompt_template: str = SCHIZOTYPAL_USER_PROMPT,
    ) -> list[SchizotypalRating | None]:
        """Score multiple posts concurrently. Returns None for failed posts."""

        async def _safe_score(post: Post) -> SchizotypalRating | None:
            try:
                return await self.score_post(
                    post.text,
                    system_prompt=system_prompt,
                    user_prompt_template=user_prompt_template,
                )
            except Exception:
                logger.exception("Failed to score post %s", post.id)
                return None

        return await asyncio.gather(*[_safe_score(p) for p in posts])

    async def close(self):
        await self._client.close()
