"""
Module: dm_conversations.py
Purpose: DM conversation history tracking for RAG context continuity
Author: HOSTFI Bot Team
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from database.client import get_supabase_client

logger = logging.getLogger(__name__)

# Session expiry: 1 hour
SESSION_EXPIRY_SECONDS = 3600


# ---------------------------------------------------------------------------
# Save Message
# ---------------------------------------------------------------------------


async def save_dm_message(
    user_telegram_id: int,
    session_id: str,
    role: str,
    content: str,
) -> None:
    """
    Save a user or assistant message to the DM conversation history.

    Args:
        user_telegram_id: Telegram ID of the user
        session_id: Session identifier (e.g. user_telegram_id + date-based id)
        role: "user" or "assistant"
        content: The message text

    Raises:
        Exception: On database communication failure
    """

    def _op() -> None:
        client = get_supabase_client()
        client.table("dm_conversations").insert(
            {
                "user_telegram_id": user_telegram_id,
                "session_id": session_id,
                "message_role": role,
                "message_content": content,
            }
        ).execute()

    try:
        await asyncio.to_thread(_op)
        logger.debug(
            "Saved DM message: user=%s session=%s role=%s",
            user_telegram_id,
            session_id,
            role,
        )
    except Exception as exc:
        logger.error("Failed to save DM message: %s", exc)


# ---------------------------------------------------------------------------
# Retrieve Recent Messages
# ---------------------------------------------------------------------------


async def get_recent_dm_messages(
    user_telegram_id: int,
    session_id: str,
    limit: int = 4,
) -> list[dict]:
    """
    Retrieve the most recent messages in a DM session.

    Only returns messages from sessions that are still "alive" (within
    SESSION_EXPIRY_SECONDS of the most recent message).

    Args:
        user_telegram_id: Telegram ID of the user
        session_id: Session identifier
        limit: Number of most recent messages to retrieve (default: 4)

    Returns:
        List of message dicts with keys: id, message_role, message_content, created_at
        Ordered chronologically (oldest first)

    Raises:
        Exception: On database communication failure
    """

    def _op() -> list[dict]:
        client = get_supabase_client()

        # Fetch the most recent message to check if session is still active
        latest = (
            client.table("dm_conversations")
            .select("created_at")
            .eq("user_telegram_id", user_telegram_id)
            .eq("session_id", session_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )

        if not latest.data:
            return []

        latest_timestamp = latest.data[0]["created_at"]
        latest_dt = datetime.fromisoformat(latest_timestamp)

        # Check if session is still within expiry window
        now = datetime.now(timezone.utc)
        # Handle both timezone-aware and naive datetimes
        if latest_dt.tzinfo is None:
            latest_dt = latest_dt.replace(tzinfo=timezone.utc)

        age = now - latest_dt
        if age.total_seconds() > SESSION_EXPIRY_SECONDS:
            logger.debug(
                "DM session %s expired (age=%.0fs)",
                session_id,
                age.total_seconds(),
            )
            return []

        # Fetch the last N messages, ordered chronologically
        messages = (
            client.table("dm_conversations")
            .select("id,message_role,message_content,created_at")
            .eq("user_telegram_id", user_telegram_id)
            .eq("session_id", session_id)
            .order("created_at", desc=False)
            .limit(limit)
            .execute()
        )

        return messages.data or []

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error(
            "Failed to retrieve DM messages: user=%s session=%s error=%s",
            user_telegram_id,
            session_id,
            exc,
        )
        return []


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


async def cleanup_expired_sessions(user_telegram_id: int) -> None:
    """
    Delete all expired conversation sessions for a user.

    Called periodically to keep the table clean. Non-blocking on failures.

    Args:
        user_telegram_id: Telegram ID of the user
    """

    def _op() -> None:
        client = get_supabase_client()

        # Find the cutoff time
        cutoff = datetime.now(timezone.utc) - timedelta(
            seconds=SESSION_EXPIRY_SECONDS
        )

        # Delete messages from sessions older than cutoff
        client.table("dm_conversations").delete().eq(
            "user_telegram_id", user_telegram_id
        ).lt("created_at", cutoff.isoformat()).execute()

    try:
        await asyncio.to_thread(_op)
        logger.debug("Cleaned up expired DM sessions for user=%s", user_telegram_id)
    except Exception as exc:
        logger.error("Failed to cleanup DM sessions: %s", exc)
