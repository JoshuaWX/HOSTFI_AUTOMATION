"""
Module: spam_filter.py
Purpose: Keyword filter, link filter, and duplicate message detection for anti-spam
Author: HOSTFI Bot Team
"""

import hashlib
import html
import logging
import re
import time
from typing import NamedTuple

from bot.utils.rate_limiter import get_redis

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Spam keyword patterns — common crypto spam / promo triggers
# ---------------------------------------------------------------------------

SPAM_KEYWORDS: list[str] = [
    "airdrop",
    "free btc",
    "free crypto",
    "guaranteed profit",
    "100x",
    "1000x",
    "pump signal",
    "dm me for",
    "message me for",
    "contact me on whatsapp",
    "earn money fast",
    "double your",
    "send btc",
    "investment opportunity",
    "click here now",
    "register now and get",
    "join my group",
    "join our channel",
    "bit.ly/",
    "tinyurl.com",
    "make money online",
    "guaranteed returns",
    "limited time offer",
    "act now",
    "binary option",
    "forex signal",
    "crypto signal",
]

# Domains allowed in messages (case-insensitive comparison)
WHITELISTED_DOMAINS: list[str] = [
    "hostfi.io",
    "coingecko.com",
    "coinmarketcap.com",
    "telegram.org",
    "t.me/hostfi",
]

# Regex that matches common URL patterns
URL_PATTERN: re.Pattern = re.compile(
    r"https?://[^\s<>\"']+|www\.[^\s<>\"']+|t\.me/[^\s<>\"']+",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


class SpamCheckResult(NamedTuple):
    """Result of a spam check."""

    is_spam: bool
    reason: str


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


async def check_spam_keywords(text: str) -> SpamCheckResult:
    """
    Check message text against the spam keyword blocklist.

    Args:
        text: Raw message text

    Returns:
        SpamCheckResult indicating whether spam was detected
    """
    lower = text.lower()
    for keyword in SPAM_KEYWORDS:
        if keyword in lower:
            logger.info("Spam keyword detected: '%s'", keyword)
            return SpamCheckResult(
                is_spam=True, reason=f"Spam keyword: {keyword}"
            )
    return SpamCheckResult(is_spam=False, reason="")


async def check_links(
    text: str, user_is_verified: bool = False
) -> SpamCheckResult:
    """
    Detect non-whitelisted links in messages.

    Unverified users are blocked from posting *any* link.  Verified users
    may post whitelisted links only.

    Args:
        text: Raw message text
        user_is_verified: Whether the sender has passed verification

    Returns:
        SpamCheckResult indicating whether a prohibited link was found
    """
    urls = URL_PATTERN.findall(text)
    if not urls:
        return SpamCheckResult(is_spam=False, reason="")

    for url in urls:
        url_lower = url.lower()
        is_whitelisted = any(
            domain in url_lower for domain in WHITELISTED_DOMAINS
        )

        if not is_whitelisted:
            safe_url = html.escape(url[:80])
            if not user_is_verified:
                logger.info(
                    "Link blocked from unverified user: %s", url[:80]
                )
                return SpamCheckResult(
                    is_spam=True,
                    reason=f"Unverified user posting link: {safe_url}",
                )
            else:
                logger.info(
                    "Non-whitelisted link from verified user: %s", url[:80]
                )
                return SpamCheckResult(
                    is_spam=True,
                    reason=f"Non-whitelisted link: {safe_url}",
                )

    return SpamCheckResult(is_spam=False, reason="")


async def check_duplicate_message(
    user_id: int, text: str
) -> SpamCheckResult:
    """
    Detect duplicate messages from the same user within a rolling window.

    Each message is SHA-256 hashed (truncated).  A Redis key per
    user + hash is set with a 10-minute TTL.  If the key already exists
    the message is flagged as a duplicate.

    Args:
        user_id: Telegram user ID
        text: Raw message text

    Returns:
        SpamCheckResult indicating whether a duplicate was detected
    """
    redis = get_redis()
    msg_hash = hashlib.sha256(
        text.strip().lower().encode()
    ).hexdigest()[:16]
    dup_key = f"spam:dup:{user_id}:{msg_hash}"

    try:
        existing = await redis.get(dup_key)
        if existing is not None:
            logger.info("Duplicate message from user %s", user_id)
            return SpamCheckResult(
                is_spam=True, reason="Duplicate message detected"
            )

        # Store hash with 10-minute TTL
        await redis.set(dup_key, "1", ex=600)
        return SpamCheckResult(is_spam=False, reason="")

    except Exception as exc:
        logger.error(
            "Duplicate check failed for user %s: %s", user_id, exc
        )
        # Fail open — don't block messages if Redis is unreachable
        return SpamCheckResult(is_spam=False, reason="")


# ---------------------------------------------------------------------------
# Aggregate runner
# ---------------------------------------------------------------------------


async def run_spam_checks(
    user_id: int, text: str, user_is_verified: bool = False
) -> SpamCheckResult:
    """
    Run all spam checks against a message in sequence.

    Exits early on the first positive detection.

    Args:
        user_id: Telegram user ID
        text: Raw message text
        user_is_verified: Verification status of the sender

    Returns:
        SpamCheckResult — first positive hit, or clean result
    """
    # 1. Keyword check
    result = await check_spam_keywords(text)
    if result.is_spam:
        return result

    # 2. Link check
    result = await check_links(text, user_is_verified)
    if result.is_spam:
        return result

    # 3. Duplicate check
    result = await check_duplicate_message(user_id, text)
    if result.is_spam:
        return result

    return SpamCheckResult(is_spam=False, reason="")
