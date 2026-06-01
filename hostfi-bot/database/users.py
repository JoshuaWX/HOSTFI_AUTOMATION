"""
Module: users.py
Purpose: User CRUD operations against the Supabase users table
Author: HOSTFI Bot Team
"""

import asyncio
import html
import logging
from datetime import datetime, timezone

from database.client import get_supabase_client

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


async def get_user(telegram_id: int) -> dict | None:
    """
    Retrieve a user record by Telegram ID.

    Args:
        telegram_id: Telegram user ID

    Returns:
        User dict or None if not found

    Raises:
        Exception: On database communication failure
    """

    def _op() -> dict | None:
        client = get_supabase_client()
        resp = (
            client.table("users")
            .select("*")
            .eq("telegram_id", telegram_id)
            .execute()
        )
        return resp.data[0] if resp.data else None

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("get_user failed for %s: %s", telegram_id, exc)
        raise


async def get_user_by_username(username: str) -> dict | None:
    """
    Retrieve a user record by Telegram username.

    Args:
        username: Telegram username with or without leading @

    Returns:
        User dict or None if not found
    """
    clean = username.strip().lstrip("@").lower()
    if not clean:
        return None

    def _op() -> dict | None:
        client = get_supabase_client()
        resp = (
            client.table("users")
            .select("*")
            .ilike("username", clean)
            .limit(1)
            .execute()
        )
        return resp.data[0] if resp.data else None

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("get_user_by_username failed for %s: %s", username, exc)
        return None


async def get_or_create_user(
    telegram_id: int,
    username: str | None = None,
    first_name: str | None = None,
) -> dict:
    """
    Fetch an existing user or create a new row in the users table.

    All string inputs are HTML-escaped before storage to prevent injection.

    Args:
        telegram_id: Telegram user ID
        username: Telegram username (optional)
        first_name: Telegram first name (optional)

    Returns:
        User record as a dictionary

    Raises:
        Exception: On database communication failure
    """

    def _op() -> dict:
        client = get_supabase_client()
        response = (
            client.table("users")
            .select("*")
            .eq("telegram_id", telegram_id)
            .execute()
        )

        if response.data:
            safe_username = html.escape(username) if username else None
            safe_first_name = html.escape(first_name) if first_name else None
            payload = {
                "username": safe_username,
                "first_name": safe_first_name,
                "last_active": datetime.now(timezone.utc).isoformat(),
            }
            client.table("users").update(payload).eq(
                "telegram_id", telegram_id
            ).execute()
            updated = dict(response.data[0])
            updated.update(payload)
            return updated

        # Create new user — sanitise all string fields
        safe_username = html.escape(username) if username else None
        safe_first_name = html.escape(first_name) if first_name else None

        new_user = {
            "telegram_id": telegram_id,
            "username": safe_username,
            "first_name": safe_first_name,
            "is_verified": False,
            "is_banned": False,
            "warn_count": 0,
            "xp_points": 0,
        }
        insert_resp = client.table("users").insert(new_user).execute()
        logger.info("Created new user: telegram_id=%s", telegram_id)
        return insert_resp.data[0]

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("get_or_create_user failed for %s: %s", telegram_id, exc)
        raise


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


async def increment_warns(telegram_id: int) -> int:
    """
    Increment a user's warning count and return the new total.

    Args:
        telegram_id: Telegram user ID

    Returns:
        Updated warn count

    Raises:
        ValueError: If the user does not exist
    """

    def _op() -> int:
        client = get_supabase_client()
        resp = (
            client.table("users")
            .select("warn_count")
            .eq("telegram_id", telegram_id)
            .execute()
        )
        if not resp.data:
            raise ValueError(f"User {telegram_id} not found")

        new_count: int = resp.data[0]["warn_count"] + 1
        client.table("users").update({"warn_count": new_count}).eq(
            "telegram_id", telegram_id
        ).execute()
        return new_count

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("increment_warns failed for %s: %s", telegram_id, exc)
        raise


async def ban_user(telegram_id: int) -> None:
    """
    Mark a user as banned in the database.

    Args:
        telegram_id: Telegram user ID
    """

    def _op() -> None:
        client = get_supabase_client()
        client.table("users").update({"is_banned": True}).eq(
            "telegram_id", telegram_id
        ).execute()

    try:
        await asyncio.to_thread(_op)
        logger.info("User %s marked as banned", telegram_id)
    except Exception as exc:
        logger.error("ban_user failed for %s: %s", telegram_id, exc)
        raise


async def unban_user(telegram_id: int) -> None:
    """
    Remove the ban flag and reset warnings for a user.

    Args:
        telegram_id: Telegram user ID
    """

    def _op() -> None:
        client = get_supabase_client()
        client.table("users").update(
            {"is_banned": False, "warn_count": 0}
        ).eq("telegram_id", telegram_id).execute()

    try:
        await asyncio.to_thread(_op)
        logger.info("User %s unbanned and warnings reset", telegram_id)
    except Exception as exc:
        logger.error("unban_user failed for %s: %s", telegram_id, exc)
        raise


async def verify_user(telegram_id: int) -> None:
    """
    Set a user's verification status to True.

    Args:
        telegram_id: Telegram user ID
    """

    def _op() -> None:
        client = get_supabase_client()
        client.table("users").update({"is_verified": True}).eq(
            "telegram_id", telegram_id
        ).execute()

    try:
        await asyncio.to_thread(_op)
        logger.info("User %s verified", telegram_id)
    except Exception as exc:
        logger.error("verify_user failed for %s: %s", telegram_id, exc)
        raise


async def is_user_verified(telegram_id: int) -> bool:
    """
    Check whether a user has passed the verification gate.

    Args:
        telegram_id: Telegram user ID

    Returns:
        True if verified, False otherwise (including when user not found)
    """
    user = await get_user(telegram_id)
    if user is None:
        return False
    return bool(user.get("is_verified", False))


# ---------------------------------------------------------------------------
# XP System
# ---------------------------------------------------------------------------


async def add_xp(telegram_id: int, amount: int) -> int:
    """
    Add XP points to a user and return the new total.

    Args:
        telegram_id: Telegram user ID
        amount: Number of XP points to add

    Returns:
        Updated XP total, or 0 if user not found
    """

    def _op() -> int:
        client = get_supabase_client()
        resp = (
            client.table("users")
            .select("xp_points")
            .eq("telegram_id", telegram_id)
            .execute()
        )
        if not resp.data:
            return 0

        new_total = resp.data[0]["xp_points"] + amount
        client.table("users").update({"xp_points": new_total}).eq(
            "telegram_id", telegram_id
        ).execute()
        return new_total

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("add_xp failed for %s: %s", telegram_id, exc)
        return 0


async def get_user_rank(telegram_id: int) -> tuple[int, int, int]:
    """
    Get a user's XP total, rank position, and total user count.

    Args:
        telegram_id: Telegram user ID

    Returns:
        Tuple of (xp_points, rank_position, total_users).
        Returns (0, 0, 0) if user not found.
    """

    def _op() -> tuple[int, int, int]:
        client = get_supabase_client()

        # Get the target user's XP
        user_resp = (
            client.table("users")
            .select("xp_points")
            .eq("telegram_id", telegram_id)
            .execute()
        )
        if not user_resp.data:
            return (0, 0, 0)

        user_xp = user_resp.data[0]["xp_points"]

        # Count users with higher XP (rank = that count + 1)
        all_resp = (
            client.table("users")
            .select("xp_points")
            .order("xp_points", desc=True)
            .execute()
        )
        total = len(all_resp.data) if all_resp.data else 0
        rank = 1
        for row in (all_resp.data or []):
            if row["xp_points"] > user_xp:
                rank += 1
            else:
                break

        return (user_xp, rank, total)

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("get_user_rank failed for %s: %s", telegram_id, exc)
        return (0, 0, 0)


async def get_leaderboard(limit: int = 10) -> list[dict]:
    """
    Get the top users by XP points.

    Args:
        limit: Maximum number of users to return

    Returns:
        List of user dicts ordered by xp_points descending
    """

    def _op() -> list[dict]:
        client = get_supabase_client()
        resp = (
            client.table("users")
            .select("telegram_id, username, first_name, xp_points")
            .order("xp_points", desc=True)
            .limit(limit)
            .execute()
        )
        return resp.data or []

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("get_leaderboard failed: %s", exc)
        return []
