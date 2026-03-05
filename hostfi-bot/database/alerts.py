"""
Module: alerts.py
Purpose: Price alert CRUD operations via Supabase
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


async def create_alert(
    user_telegram_id: int,
    coin_id: str,
    target_price: float,
    direction: str,
) -> dict[str, Any] | None:
    """
    Create a new price alert for a user.

    Args:
        user_telegram_id: Telegram user ID
        coin_id: CoinGecko coin identifier (e.g. "bitcoin")
        target_price: Target price in USD
        direction: "above" or "below"

    Returns:
        The inserted row dict, or None on failure
    """

    def _op() -> dict[str, Any] | None:
        client = get_supabase_client()
        result = (
            client.table("price_alerts")
            .insert(
                {
                    "user_telegram_id": user_telegram_id,
                    "coin_id": coin_id,
                    "target_price": target_price,
                    "direction": direction,
                    "is_active": True,
                }
            )
            .execute()
        )
        if result.data:
            return result.data[0]
        return None

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error(
            "Failed to create alert for user %s: %s",
            user_telegram_id,
            exc,
        )
        return None


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


async def get_active_alerts() -> list[dict[str, Any]]:
    """
    Retrieve all active price alerts across all users.

    Returns:
        List of active alert row dicts
    """

    def _op() -> list[dict[str, Any]]:
        client = get_supabase_client()
        result = (
            client.table("price_alerts")
            .select("*")
            .eq("is_active", True)
            .execute()
        )
        return result.data or []

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("Failed to fetch active alerts: %s", exc)
        return []


async def get_user_alerts(user_telegram_id: int) -> list[dict[str, Any]]:
    """
    Retrieve all active alerts for a specific user.

    Args:
        user_telegram_id: Telegram user ID

    Returns:
        List of active alert row dicts for the user
    """

    def _op() -> list[dict[str, Any]]:
        client = get_supabase_client()
        result = (
            client.table("price_alerts")
            .select("*")
            .eq("user_telegram_id", user_telegram_id)
            .eq("is_active", True)
            .execute()
        )
        return result.data or []

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error(
            "Failed to fetch alerts for user %s: %s",
            user_telegram_id,
            exc,
        )
        return []


# ---------------------------------------------------------------------------
# Update / Deactivate
# ---------------------------------------------------------------------------


async def deactivate_alert(alert_id: int) -> bool:
    """
    Mark a price alert as inactive (triggered or cancelled).

    Args:
        alert_id: Primary key of the alert row

    Returns:
        True if deactivated successfully, False otherwise
    """

    def _op() -> bool:
        client = get_supabase_client()
        result = (
            client.table("price_alerts")
            .update({"is_active": False})
            .eq("id", alert_id)
            .execute()
        )
        return bool(result.data)

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("Failed to deactivate alert %s: %s", alert_id, exc)
        return False


async def cancel_user_alerts(user_telegram_id: int) -> int:
    """
    Cancel (deactivate) all active alerts for a user.

    Args:
        user_telegram_id: Telegram user ID

    Returns:
        Number of alerts cancelled
    """

    def _op() -> int:
        client = get_supabase_client()
        result = (
            client.table("price_alerts")
            .update({"is_active": False})
            .eq("user_telegram_id", user_telegram_id)
            .eq("is_active", True)
            .execute()
        )
        return len(result.data) if result.data else 0

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error(
            "Failed to cancel alerts for user %s: %s",
            user_telegram_id,
            exc,
        )
        return 0
