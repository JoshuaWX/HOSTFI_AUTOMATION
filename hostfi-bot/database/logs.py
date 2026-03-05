"""
Module: logs.py
Purpose: Audit log writes to the Supabase audit_logs table
Author: HOSTFI Bot Team
"""

import asyncio
import logging
from typing import Any

from database.client import get_supabase_client

logger = logging.getLogger(__name__)


async def log_action(
    action: str,
    admin_telegram_id: int,
    target_telegram_id: int | None = None,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """
    Write an audit log entry for a moderation or admin action.

    This function swallows exceptions to avoid crashing the bot if
    audit logging fails — the primary action should still succeed.

    Args:
        action: Name of the action (e.g. "warn", "ban", "mute")
        admin_telegram_id: Telegram ID of the admin who performed the action
                           (use 0 for system-initiated actions)
        target_telegram_id: Telegram ID of the affected user (optional)
        reason: Human-readable reason for the action (optional)
        metadata: Arbitrary JSON-serialisable context (optional)
    """

    def _op() -> None:
        client = get_supabase_client()
        entry = {
            "action": action,
            "admin_telegram_id": admin_telegram_id,
            "target_telegram_id": target_telegram_id,
            "reason": reason,
            "metadata": metadata or {},
        }
        client.table("audit_logs").insert(entry).execute()

    try:
        await asyncio.to_thread(_op)
        logger.info(
            "Audit log: action=%s admin=%s target=%s",
            action,
            admin_telegram_id,
            target_telegram_id,
        )
    except Exception as exc:
        # Audit logging failures must never crash the bot
        logger.error("Failed to write audit log: %s", exc)
