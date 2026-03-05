"""
Module: referrals.py
Purpose: Referral tracking CRUD operations via Supabase
Author: HOSTFI Bot Team
"""

import asyncio
import logging
from typing import Any

from database.client import get_supabase_client

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


async def create_referral(
    referrer_telegram_id: int,
    referred_telegram_id: int,
) -> dict[str, Any] | None:
    """
    Record a new referral in the database.

    Args:
        referrer_telegram_id: Telegram ID of the user who shared the link
        referred_telegram_id: Telegram ID of the newly joined user

    Returns:
        The inserted row dict, or None on failure
    """

    def _op() -> dict[str, Any] | None:
        client = get_supabase_client()

        # Check for duplicate referral
        existing = (
            client.table("referrals")
            .select("id")
            .eq("referred_telegram_id", referred_telegram_id)
            .execute()
        )
        if existing.data:
            return None  # Already referred

        result = (
            client.table("referrals")
            .insert(
                {
                    "referrer_telegram_id": referrer_telegram_id,
                    "referred_telegram_id": referred_telegram_id,
                }
            )
            .execute()
        )
        return result.data[0] if result.data else None

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error(
            "Failed to create referral %s -> %s: %s",
            referrer_telegram_id,
            referred_telegram_id,
            exc,
        )
        return None


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


async def get_referral_count(referrer_telegram_id: int) -> int:
    """
    Count how many users a referrer has successfully referred.

    Args:
        referrer_telegram_id: Telegram ID of the referrer

    Returns:
        Number of referrals
    """

    def _op() -> int:
        client = get_supabase_client()
        result = (
            client.table("referrals")
            .select("id", count="exact")
            .eq("referrer_telegram_id", referrer_telegram_id)
            .execute()
        )
        return result.count if result.count is not None else 0

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error(
            "Failed to get referral count for %s: %s",
            referrer_telegram_id,
            exc,
        )
        return 0


async def get_top_referrers(limit: int = 10) -> list[dict[str, Any]]:
    """
    Get the top referrers ranked by number of referrals.

    Uses a raw count query grouped by referrer.

    Args:
        limit: Maximum number of referrers to return

    Returns:
        List of dicts with referrer_telegram_id and referral_count
    """

    def _op() -> list[dict[str, Any]]:
        client = get_supabase_client()
        # Fetch all referrals and aggregate in Python
        result = (
            client.table("referrals")
            .select("referrer_telegram_id")
            .execute()
        )
        if not result.data:
            return []

        # Count referrals per referrer
        counts: dict[int, int] = {}
        for row in result.data:
            rid = row["referrer_telegram_id"]
            counts[rid] = counts.get(rid, 0) + 1

        # Sort by count descending, take top N
        sorted_referrers = sorted(
            counts.items(), key=lambda x: x[1], reverse=True
        )[:limit]

        return [
            {"referrer_telegram_id": rid, "referral_count": count}
            for rid, count in sorted_referrers
        ]

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("Failed to get top referrers: %s", exc)
        return []
