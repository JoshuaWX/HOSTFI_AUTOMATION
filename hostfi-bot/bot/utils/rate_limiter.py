"""
Module: rate_limiter.py
Purpose: Async per-user rate limiting backed by Upstash Redis
Author: HOSTFI Bot Team
"""

import logging

from upstash_redis.asyncio import Redis

from config import UPSTASH_REDIS_TOKEN, UPSTASH_REDIS_URL

logger = logging.getLogger(__name__)

_redis: Redis | None = None


def get_redis() -> Redis:
    """
    Return the async Upstash Redis client singleton.

    Creates the client on first call.  Upstash Redis uses HTTP under the
    hood so there is no persistent connection to manage.

    Returns:
        Async Redis client instance
    """
    global _redis
    if _redis is None:
        _redis = Redis(url=UPSTASH_REDIS_URL, token=UPSTASH_REDIS_TOKEN)
        logger.info("Upstash Redis client initialised")
    return _redis


# ---------------------------------------------------------------------------
# Core rate-limit check
# ---------------------------------------------------------------------------


async def check_rate_limit(
    user_id: int,
    action: str = "command",
    limit: int = 10,
    window: int = 60,
) -> bool:
    """
    Check whether a user is within the allowed rate limit for an action.

    Increments a per-user counter in Redis.  Returns **True** if the
    request is allowed, **False** if the limit has been exceeded.

    The counter is created with a TTL equal to *window* on the first
    increment so it auto-expires.

    Args:
        user_id: Telegram user ID
        action: Namespace for the rate limit (e.g. "command", "ai_query",
                "flood")
        limit: Maximum allowed requests within the window
        window: Sliding window size in seconds

    Returns:
        True if within limits, False if rate-limited
    """
    redis = get_redis()
    key = f"rate:{action}:{user_id}"

    try:
        count = await redis.incr(key)

        # Set TTL only on the first increment
        if count == 1:
            await redis.expire(key, window)

        if count > limit:
            logger.warning(
                "Rate limit exceeded: user=%s action=%s count=%s limit=%s",
                user_id,
                action,
                count,
                limit,
            )
            return False

        return True

    except Exception as exc:
        logger.error("Rate limiter error for user %s: %s", user_id, exc)
        # Fail open — allow the request if Redis is unreachable
        return True


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


async def get_rate_count(user_id: int, action: str = "command") -> int:
    """
    Read the current request count for a user / action pair.

    Args:
        user_id: Telegram user ID
        action: Rate-limit namespace

    Returns:
        Current count (0 if the key does not exist)
    """
    redis = get_redis()
    key = f"rate:{action}:{user_id}"

    try:
        val = await redis.get(key)
        return int(val) if val else 0
    except Exception as exc:
        logger.error("Failed to get rate count: %s", exc)
        return 0
