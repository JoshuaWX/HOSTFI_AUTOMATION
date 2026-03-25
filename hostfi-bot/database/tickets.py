"""
Module: tickets.py
Purpose: Support ticket CRUD operations via Supabase
Author: HOSTFI Bot Team
"""

import asyncio
import logging
from typing import Any

from database.client import get_supabase_client

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_ticket_id(ticket_number: int) -> str:
    """
    Format a numeric ticket_number as HSTF-0001 style string.

    Args:
        ticket_number: The auto-incremented serial number

    Returns:
        Formatted ticket ID string
    """
    return f"HSTF-{ticket_number:04d}"


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


async def create_ticket(
    user_telegram_id: int,
    issue_description: str,
) -> dict[str, Any] | None:
    """
    Create a new support ticket in the database.

    Up to two open/claimed tickets per user are allowed at a time.

    Args:
        user_telegram_id: Telegram ID of the user filing the ticket
        issue_description: Brief description of the issue

    Returns:
        The inserted row dict with formatted ticket_id, or None if the
        user already has two active tickets
    """

    def _op() -> dict[str, Any] | None:
        client = get_supabase_client()

        # Enforce max two active tickets per user
        existing = (
            client.table("tickets")
            .select("id")
            .eq("user_telegram_id", user_telegram_id)
            .in_("status", ["open", "claimed"])
            .execute()
        )
        if len(existing.data or []) >= 2:
            return None  # Already at max active tickets

        result = (
            client.table("tickets")
            .insert(
                {
                    "user_telegram_id": user_telegram_id,
                    "issue_description": issue_description,
                }
            )
            .execute()
        )
        if result.data:
            row = result.data[0]
            row["ticket_id"] = _format_ticket_id(row["ticket_number"])
            return row
        return None

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("Failed to create ticket: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Claim
# ---------------------------------------------------------------------------


async def claim_ticket(
    ticket_id: str,
    admin_telegram_id: int,
) -> dict[str, Any] | None:
    """
    Assign an open ticket to an admin.

    Args:
        ticket_id: Formatted ticket ID (e.g. "HSTF-0001")
        admin_telegram_id: Telegram ID of the claiming admin

    Returns:
        The updated row dict, or None if ticket not found / already claimed
    """

    def _op() -> dict[str, Any] | None:
        client = get_supabase_client()
        # Extract ticket_number from formatted ID
        ticket_number = int(ticket_id.split("-")[1])

        result = (
            client.table("tickets")
            .update(
                {
                    "status": "claimed",
                    "assigned_admin_id": admin_telegram_id,
                }
            )
            .eq("ticket_number", ticket_number)
            .eq("status", "open")
            .execute()
        )
        if result.data:
            row = result.data[0]
            row["ticket_id"] = _format_ticket_id(row["ticket_number"])
            return row
        return None

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("Failed to claim ticket %s: %s", ticket_id, exc)
        return None


# ---------------------------------------------------------------------------
# Resolve / Close
# ---------------------------------------------------------------------------


async def resolve_ticket(ticket_id: str) -> dict[str, Any] | None:
    """
    Mark a claimed ticket as resolved.

    Sets status to 'resolved' and records the resolution timestamp.

    Args:
        ticket_id: Formatted ticket ID (e.g. "HSTF-0001")

    Returns:
        The updated row dict, or None if not found / not in claimed status
    """

    def _op() -> dict[str, Any] | None:
        client = get_supabase_client()
        ticket_number = int(ticket_id.split("-")[1])

        result = (
            client.table("tickets")
            .update(
                {
                    "status": "resolved",
                    "resolved_at": "now()",
                }
            )
            .eq("ticket_number", ticket_number)
            .eq("status", "claimed")
            .execute()
        )
        if result.data:
            row = result.data[0]
            row["ticket_id"] = _format_ticket_id(row["ticket_number"])
            return row
        return None

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("Failed to resolve ticket %s: %s", ticket_id, exc)
        return None


async def cancel_ticket(ticket_id: str, user_telegram_id: int) -> dict[str, Any] | None:
    """
    Cancel an open (unclaimed) ticket. Only the ticket owner can cancel.

    Args:
        ticket_id: Formatted ticket ID (e.g. "HSTF-0001")
        user_telegram_id: Telegram ID of the user requesting cancellation

    Returns:
        The updated row dict, or None if not found / not cancellable
    """

    def _op() -> dict[str, Any] | None:
        client = get_supabase_client()
        ticket_number = int(ticket_id.split("-")[1])

        result = (
            client.table("tickets")
            .update({"status": "cancelled"})
            .eq("ticket_number", ticket_number)
            .eq("user_telegram_id", user_telegram_id)
            .eq("status", "open")
            .execute()
        )
        if result.data:
            row = result.data[0]
            row["ticket_id"] = _format_ticket_id(row["ticket_number"])
            return row
        return None

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("Failed to cancel ticket %s: %s", ticket_id, exc)
        return None


# ---------------------------------------------------------------------------
# Rate
# ---------------------------------------------------------------------------


async def rate_ticket(ticket_id: str, rating: int) -> dict[str, Any] | None:
    """
    Store a user's rating for a resolved ticket.

    Args:
        ticket_id: Formatted ticket ID (e.g. "HSTF-0001")
        rating: Star rating 1-5

    Returns:
        The updated row dict, or None
    """

    def _op() -> dict[str, Any] | None:
        client = get_supabase_client()
        ticket_number = int(ticket_id.split("-")[1])

        result = (
            client.table("tickets")
            .update({"rating": rating, "status": "closed"})
            .eq("ticket_number", ticket_number)
            .eq("status", "resolved")
            .execute()
        )
        if result.data:
            row = result.data[0]
            row["ticket_id"] = _format_ticket_id(row["ticket_number"])
            return row
        return None

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("Failed to rate ticket %s: %s", ticket_id, exc)
        return None


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


async def get_open_tickets() -> list[dict[str, Any]]:
    """
    Retrieve all open (unclaimed) tickets, ordered by creation date.

    Returns:
        List of ticket row dicts with formatted ticket_id field
    """

    def _op() -> list[dict[str, Any]]:
        client = get_supabase_client()
        result = (
            client.table("tickets")
            .select("*")
            .eq("status", "open")
            .order("created_at", desc=False)
            .execute()
        )
        tickets = result.data or []
        for t in tickets:
            t["ticket_id"] = _format_ticket_id(t["ticket_number"])
        return tickets

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("Failed to get open tickets: %s", exc)
        return []


async def get_user_active_ticket(
    user_telegram_id: int,
) -> dict[str, Any] | None:
    """
    Check if a user has an active (open or claimed) ticket.

    Args:
        user_telegram_id: Telegram ID of the user

    Returns:
        The active ticket row dict, or None
    """

    def _op() -> dict[str, Any] | None:
        client = get_supabase_client()
        result = (
            client.table("tickets")
            .select("*")
            .eq("user_telegram_id", user_telegram_id)
            .in_("status", ["open", "claimed"])
            .limit(1)
            .execute()
        )
        if result.data:
            row = result.data[0]
            row["ticket_id"] = _format_ticket_id(row["ticket_number"])
            return row
        return None

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("Failed to check active ticket for %s: %s", user_telegram_id, exc)
        return None


async def get_user_active_tickets(
    user_telegram_id: int,
) -> list[dict[str, Any]]:
    """
    Retrieve all active (open or claimed) tickets for a user.

    Args:
        user_telegram_id: Telegram ID of the user

    Returns:
        List of active ticket rows with formatted ticket_id
    """

    def _op() -> list[dict[str, Any]]:
        client = get_supabase_client()
        result = (
            client.table("tickets")
            .select("*")
            .eq("user_telegram_id", user_telegram_id)
            .in_("status", ["open", "claimed"])
            .order("created_at", desc=False)
            .execute()
        )
        rows = result.data or []
        for row in rows:
            row["ticket_id"] = _format_ticket_id(row["ticket_number"])
        return rows

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("Failed to list active tickets for %s: %s", user_telegram_id, exc)
        return []


async def get_ticket_by_id(ticket_id: str) -> dict[str, Any] | None:
    """
    Retrieve a single ticket by its formatted ID.

    Args:
        ticket_id: Formatted ticket ID (e.g. "HSTF-0001")

    Returns:
        The ticket row dict, or None
    """

    def _op() -> dict[str, Any] | None:
        client = get_supabase_client()
        ticket_number = int(ticket_id.split("-")[1])

        result = (
            client.table("tickets")
            .select("*")
            .eq("ticket_number", ticket_number)
            .limit(1)
            .execute()
        )
        if result.data:
            row = result.data[0]
            row["ticket_id"] = _format_ticket_id(row["ticket_number"])
            return row
        return None

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("Failed to get ticket %s: %s", ticket_id, exc)
        return None


async def get_all_active_tickets() -> list[dict[str, Any]]:
    """
    Retrieve all active tickets (open + claimed), ordered by creation date.

    Returns:
        List of active ticket row dicts with formatted ticket_id
    """

    def _op() -> list[dict[str, Any]]:
        client = get_supabase_client()
        result = (
            client.table("tickets")
            .select("*")
            .in_("status", ["open", "claimed"])
            .order("created_at", desc=False)
            .execute()
        )
        tickets = result.data or []
        for t in tickets:
            t["ticket_id"] = _format_ticket_id(t["ticket_number"])
        return tickets

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("Failed to get active tickets: %s", exc)
        return []


async def get_unclaimed_old_tickets(hours: int = 2) -> list[dict[str, Any]]:
    """
    Retrieve open tickets that have been unclaimed for more than *hours*.

    Used by the escalation checker to re-alert the admin channel.

    Args:
        hours: Number of hours threshold (default 2)

    Returns:
        List of stale open ticket row dicts
    """

    def _op() -> list[dict[str, Any]]:
        from datetime import datetime, timedelta, timezone

        client = get_supabase_client()
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=hours)
        ).isoformat()

        result = (
            client.table("tickets")
            .select("*")
            .eq("status", "open")
            .lt("created_at", cutoff)
            .order("created_at", desc=False)
            .execute()
        )
        tickets = result.data or []
        for t in tickets:
            t["ticket_id"] = _format_ticket_id(t["ticket_number"])
        return tickets

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("Failed to get unclaimed old tickets: %s", exc)
        return []
