"""
Module: campaign.py
Purpose: Campaign cycle, XP ledger, invite, raid, and X account persistence
Author: HOSTFI Bot Team
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from config import INVITE_RETENTION_HOURS
from database.client import get_supabase_client

logger = logging.getLogger(__name__)

XP_INVITE = 70
XP_RAID = 50
XP_X_POST = 100
XP_HELPFUL = 100

REWARD_CONFIG = {
    "1": "$25",
    "2": "$20",
    "3": "$15",
    "4": "$12",
    "5": "$8",
}


def _now() -> datetime:
    """Return timezone-aware UTC now."""
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    """Return an ISO timestamp for Supabase."""
    return dt.astimezone(timezone.utc).isoformat()


def _cycle_label(cycle: dict | None) -> str:
    """Return a human label for a cycle row."""
    if not cycle:
        return "No active cycle"
    return f"Cycle #{cycle.get('cycle_number', cycle.get('id'))}"


async def get_active_cycle() -> dict | None:
    """Return the active campaign cycle, if one exists."""

    def _op() -> dict | None:
        client = get_supabase_client()
        result = (
            client.table("campaign_cycles")
            .select("*")
            .eq("status", "active")
            .order("start_at", desc=True)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("Failed to get active campaign cycle: %s", exc)
        return None


async def start_cycle(started_by: int, duration_days: int = 14) -> dict | None:
    """
    Start a new campaign cycle if no active cycle exists.

    Resets users.xp_points because that column is the current-cycle display cache.
    """

    def _op() -> dict | None:
        client = get_supabase_client()
        active = (
            client.table("campaign_cycles")
            .select("*")
            .eq("status", "active")
            .limit(1)
            .execute()
        )
        if active.data:
            return active.data[0]

        latest = (
            client.table("campaign_cycles")
            .select("cycle_number")
            .order("cycle_number", desc=True)
            .limit(1)
            .execute()
        )
        next_number = (latest.data[0]["cycle_number"] + 1) if latest.data else 1
        start_at = _now()
        end_at = start_at + timedelta(days=duration_days)

        # Reset visible campaign XP for the fresh cycle.
        client.table("users").update({"xp_points": 0}).neq("telegram_id", 0).execute()

        created = (
            client.table("campaign_cycles")
            .insert(
                {
                    "cycle_number": next_number,
                    "status": "active",
                    "start_at": _iso(start_at),
                    "end_at": _iso(end_at),
                    "started_by": started_by,
                    "reward_config": REWARD_CONFIG,
                }
            )
            .execute()
        )
        return created.data[0] if created.data else None

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("Failed to start campaign cycle: %s", exc)
        return None


async def finish_cycle(finished_by: int) -> dict | None:
    """
    Finish the active cycle, calculate winners, and reset visible XP.
    """

    def _op() -> dict | None:
        client = get_supabase_client()
        active = (
            client.table("campaign_cycles")
            .select("*")
            .eq("status", "active")
            .order("start_at", desc=True)
            .limit(1)
            .execute()
        )
        if not active.data:
            return None

        cycle = active.data[0]
        cycle_id = cycle["id"]
        winners = _leaderboard_sync(client, cycle_id, limit=5)

        client.table("campaign_cycles").update(
            {"status": "finished", "finished_at": _iso(_now()), "finished_by": finished_by}
        ).eq("id", cycle_id).execute()
        client.table("raids").update({"status": "closed"}).eq("cycle_id", cycle_id).execute()

        client.table("users").update({"xp_points": 0}).neq("telegram_id", 0).execute()

        return {
            "finished_cycle": cycle,
            "winners": winners,
            "new_cycle": None,
        }

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("Failed to finish campaign cycle: %s", exc)
        return None


def _leaderboard_sync(client, cycle_id: int, limit: int = 10) -> list[dict]:
    """Build campaign leaderboard from approved XP events."""
    events = (
        client.table("xp_events")
        .select("telegram_id,amount,approved_at")
        .eq("cycle_id", cycle_id)
        .eq("status", "approved")
        .execute()
    )

    totals: dict[int, dict[str, Any]] = {}
    for event in events.data or []:
        uid = int(event["telegram_id"])
        row = totals.setdefault(
            uid,
            {"telegram_id": uid, "xp": 0, "first_approved_at": event.get("approved_at")},
        )
        row["xp"] += int(event.get("amount") or 0)
        approved_at = event.get("approved_at")
        if approved_at and (row["first_approved_at"] is None or approved_at < row["first_approved_at"]):
            row["first_approved_at"] = approved_at

    ranked = [row for row in totals.values() if row["xp"] > 0]
    ranked.sort(key=lambda item: (-item["xp"], item["first_approved_at"] or "9999"))
    ranked = ranked[:limit]

    if not ranked:
        return []

    users = (
        client.table("users")
        .select("telegram_id,username,first_name")
        .in_("telegram_id", [row["telegram_id"] for row in ranked])
        .execute()
    )
    user_map = {int(row["telegram_id"]): row for row in users.data or []}
    for index, row in enumerate(ranked, 1):
        profile = user_map.get(row["telegram_id"], {})
        row["rank"] = index
        row["username"] = profile.get("username")
        row["first_name"] = profile.get("first_name")
    return ranked


async def get_campaign_leaderboard(limit: int = 10) -> list[dict]:
    """Return the active cycle leaderboard."""

    def _op() -> list[dict]:
        client = get_supabase_client()
        active = (
            client.table("campaign_cycles")
            .select("*")
            .eq("status", "active")
            .order("start_at", desc=True)
            .limit(1)
            .execute()
        )
        if not active.data:
            return []
        return _leaderboard_sync(client, active.data[0]["id"], limit)

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("Failed to get campaign leaderboard: %s", exc)
        return []


async def get_campaign_rank(telegram_id: int) -> tuple[int, int, int, dict | None]:
    """Return current-cycle XP, rank, total ranked users, and active cycle."""
    cycle = await get_active_cycle()
    if not cycle:
        return (0, 0, 0, None)
    leaderboard = await get_campaign_leaderboard(limit=1000)
    for row in leaderboard:
        if row["telegram_id"] == telegram_id:
            return (row["xp"], row["rank"], len(leaderboard), cycle)
    return (0, 0, len(leaderboard), cycle)


async def is_disqualified(telegram_id: int, cycle_id: int | None = None) -> bool:
    """Return True if the user has been disqualified in the target/current cycle."""

    def _op() -> bool:
        client = get_supabase_client()
        if cycle_id is None:
            cycle = (
                client.table("campaign_cycles")
                .select("id")
                .eq("status", "active")
                .order("start_at", desc=True)
                .limit(1)
                .execute()
            )
            if not cycle.data:
                return False
            target_cycle_id = cycle.data[0]["id"]
        else:
            target_cycle_id = cycle_id

        result = (
            client.table("xp_events")
            .select("id")
            .eq("cycle_id", target_cycle_id)
            .eq("telegram_id", telegram_id)
            .eq("event_type", "disqualification")
            .eq("status", "approved")
            .limit(1)
            .execute()
        )
        return bool(result.data)

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("Failed to check disqualification for %s: %s", telegram_id, exc)
        return False


async def award_xp(
    telegram_id: int,
    amount: int,
    event_type: str,
    reason: str,
    *,
    metadata: dict[str, Any] | None = None,
    evidence_url: str | None = None,
    external_id: str | None = None,
    actor_telegram_id: int = 0,
    cycle_id: int | None = None,
) -> dict | None:
    """
    Add an approved XP ledger event and update users.xp_points display cache.

    Negative amounts are allowed for superadmin deductions. The display cache is capped at 0.
    """

    def _op() -> dict | None:
        client = get_supabase_client()
        query = client.table("campaign_cycles").select("*")
        if cycle_id is None:
            query = query.eq("status", "active").order("start_at", desc=True)
        else:
            query = query.eq("id", cycle_id)
        cycle_result = query.limit(1).execute()
        if not cycle_result.data:
            return None
        cycle = cycle_result.data[0]

        if amount > 0 and event_type != "manual_add":
            disqualified = (
                client.table("xp_events")
                .select("id")
                .eq("cycle_id", cycle["id"])
                .eq("telegram_id", telegram_id)
                .eq("event_type", "disqualification")
                .eq("status", "approved")
                .limit(1)
                .execute()
            )
            if disqualified.data:
                return None

        event = (
            client.table("xp_events")
            .insert(
                {
                    "cycle_id": cycle["id"],
                    "telegram_id": telegram_id,
                    "amount": amount,
                    "event_type": event_type,
                    "status": "approved",
                    "reason": reason,
                    "evidence_url": evidence_url,
                    "external_id": external_id,
                    "actor_telegram_id": actor_telegram_id,
                    "approved_at": _iso(_now()),
                    "metadata": metadata or {},
                }
            )
            .execute()
        )
        if not event.data:
            return None

        new_total = None
        if cycle.get("status") == "active":
            user = (
                client.table("users")
                .select("xp_points")
                .eq("telegram_id", telegram_id)
                .limit(1)
                .execute()
            )
            current = int(user.data[0].get("xp_points") or 0) if user.data else 0
            new_total = max(0, current + amount)
            client.table("users").update({"xp_points": new_total}).eq(
                "telegram_id", telegram_id
            ).execute()

        row = event.data[0]
        row["cycle"] = cycle
        row["new_total"] = new_total
        return row

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("Failed to award campaign XP: %s", exc)
        return None


async def get_or_create_invite_link_record(
    cycle_id: int,
    inviter_telegram_id: int,
    invite_link: str | None = None,
    chat_id: int | None = None,
) -> dict | None:
    """Fetch or create/update a campaign invite link record."""

    def _op() -> dict | None:
        client = get_supabase_client()

        query = (
            client.table("campaign_invite_links")
            .select("*")
            .eq("cycle_id", cycle_id)
            .eq("inviter_telegram_id", inviter_telegram_id)
            .eq("is_active", True)
        )
        if chat_id is None:
            query = query.is_("chat_id", "null")
        else:
            query = query.eq("chat_id", chat_id)
        existing = query.limit(1).execute()
        if existing.data:
            return existing.data[0]

        if chat_id is not None:
            legacy = (
                client.table("campaign_invite_links")
                .select("*")
                .eq("cycle_id", cycle_id)
                .eq("inviter_telegram_id", inviter_telegram_id)
                .eq("is_active", True)
                .is_("chat_id", "null")
                .limit(1)
                .execute()
            )
            if legacy.data:
                row = legacy.data[0]
                updated = (
                    client.table("campaign_invite_links")
                    .update({"chat_id": chat_id})
                    .eq("id", row["id"])
                    .execute()
                )
                return updated.data[0] if updated.data else row

        if not invite_link:
            return None

        created = (
            client.table("campaign_invite_links")
            .insert(
                {
                    "cycle_id": cycle_id,
                    "inviter_telegram_id": inviter_telegram_id,
                    "chat_id": chat_id,
                    "invite_link": invite_link,
                }
            )
            .execute()
        )
        return created.data[0] if created.data else None

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("Failed to upsert campaign invite link: %s", exc)
        return None


async def record_invite_join(
    invite_link: str | None,
    invitee_telegram_id: int,
    *,
    invitee_is_bot: bool = False,
) -> dict:
    """Record a new member joining through a campaign invite link."""

    def _op() -> dict:
        if invitee_is_bot:
            return {"status": "bot_invitee", "record": None}
        if not invite_link:
            return {"status": "missing_invite_link", "record": None}

        client = get_supabase_client()
        link = (
            client.table("campaign_invite_links")
            .select("*")
            .eq("invite_link", invite_link)
            .limit(1)
            .execute()
        )
        if not link.data:
            return {"status": "unknown_invite_link", "record": None}
        link_row = link.data[0]
        if int(link_row["inviter_telegram_id"]) == int(invitee_telegram_id):
            return {
                "status": "self_invite",
                "record": None,
                "cycle_id": link_row.get("cycle_id"),
                "inviter_telegram_id": link_row.get("inviter_telegram_id"),
                "chat_id": link_row.get("chat_id"),
            }

        existing_query = (
            client.table("campaign_invite_joins")
            .select("*")
            .eq("cycle_id", link_row["cycle_id"])
            .eq("invitee_telegram_id", invitee_telegram_id)
        )
        if link_row.get("chat_id") is None:
            existing_query = existing_query.is_("chat_id", "null")
        else:
            existing_query = existing_query.eq("chat_id", link_row.get("chat_id"))
        existing = existing_query.limit(1).execute()
        if existing.data:
            return {
                "status": "already_recorded",
                "record": existing.data[0],
                "cycle_id": link_row.get("cycle_id"),
                "inviter_telegram_id": link_row.get("inviter_telegram_id"),
                "chat_id": link_row.get("chat_id"),
            }

        joined_at = _now()
        eligible_at = joined_at + timedelta(hours=INVITE_RETENTION_HOURS)
        created = (
            client.table("campaign_invite_joins")
            .insert(
                {
                    "cycle_id": link_row["cycle_id"],
                    "inviter_telegram_id": link_row["inviter_telegram_id"],
                    "invitee_telegram_id": invitee_telegram_id,
                    "chat_id": link_row.get("chat_id"),
                    "invite_link": invite_link,
                    "joined_at": _iso(joined_at),
                    "eligible_at": _iso(eligible_at),
                    "status": "pending",
                }
            )
            .execute()
        )
        if not created.data:
            return {
                "status": "db_error",
                "record": None,
                "cycle_id": link_row.get("cycle_id"),
                "inviter_telegram_id": link_row.get("inviter_telegram_id"),
                "chat_id": link_row.get("chat_id"),
            }
        return {
            "status": "recorded",
            "record": created.data[0],
            "cycle_id": link_row.get("cycle_id"),
            "inviter_telegram_id": link_row.get("inviter_telegram_id"),
            "chat_id": link_row.get("chat_id"),
        }

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("Failed to record invite join: %s", exc)
        return {"status": "db_error", "record": None, "error": str(exc)}


async def get_pending_invite_joins() -> list[dict]:
    """Return invite joins ready for retention checks."""

    def _op() -> list[dict]:
        client = get_supabase_client()
        result = (
            client.table("campaign_invite_joins")
            .select("*")
            .eq("status", "pending")
            .lte("eligible_at", _iso(_now()))
            .execute()
        )
        return result.data or []

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("Failed to fetch pending invite joins: %s", exc)
        return []


async def mark_invite_join(join_id: int, status: str, awarded: bool = False) -> None:
    """Update an invite join after retention check."""

    def _op() -> None:
        client = get_supabase_client()
        payload: dict[str, Any] = {"status": status}
        if awarded:
            payload["awarded_at"] = _iso(_now())
        client.table("campaign_invite_joins").update(payload).eq("id", join_id).execute()

    try:
        await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("Failed to mark invite join %s: %s", join_id, exc)


async def get_invite_stats(telegram_id: int, cycle_id: int | None = None) -> dict | None:
    """Return invite counts and XP earned for a user in the active/current cycle."""

    def _op() -> dict | None:
        client = get_supabase_client()
        if cycle_id is None:
            cycle_result = (
                client.table("campaign_cycles")
                .select("*")
                .eq("status", "active")
                .order("start_at", desc=True)
                .limit(1)
                .execute()
            )
            if not cycle_result.data:
                return None
            cycle = cycle_result.data[0]
        else:
            cycle_result = (
                client.table("campaign_cycles")
                .select("*")
                .eq("id", cycle_id)
                .limit(1)
                .execute()
            )
            if not cycle_result.data:
                return None
            cycle = cycle_result.data[0]

        joins = (
            client.table("campaign_invite_joins")
            .select("status")
            .eq("cycle_id", cycle["id"])
            .eq("inviter_telegram_id", telegram_id)
            .execute()
        )
        counts = {"pending": 0, "awarded": 0, "ineligible": 0}
        for row in joins.data or []:
            status = row.get("status")
            if status in counts:
                counts[status] += 1

        events = (
            client.table("xp_events")
            .select("amount")
            .eq("cycle_id", cycle["id"])
            .eq("telegram_id", telegram_id)
            .eq("event_type", "telegram_invite")
            .eq("status", "approved")
            .execute()
        )
        invite_xp = sum(int(row.get("amount") or 0) for row in events.data or [])

        link = (
            client.table("campaign_invite_links")
            .select("*")
            .eq("cycle_id", cycle["id"])
            .eq("inviter_telegram_id", telegram_id)
            .eq("is_active", True)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )

        return {
            "cycle": cycle,
            "pending": counts["pending"],
            "awarded": counts["awarded"],
            "ineligible": counts["ineligible"],
            "invite_xp": invite_xp,
            "invite_link": link.data[0].get("invite_link") if link.data else None,
        }

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("Failed to get invite stats for %s: %s", telegram_id, exc)
        return None


async def create_x_verification(telegram_id: int, username: str, code: str) -> dict | None:
    """Create or refresh a pending X account verification."""

    def _op() -> dict | None:
        client = get_supabase_client()
        existing = (
            client.table("x_accounts")
            .select("*")
            .eq("telegram_id", telegram_id)
            .limit(1)
            .execute()
        )
        payload = {
            "telegram_id": telegram_id,
            "username": username.lower().lstrip("@"),
            "verification_code": code,
            "status": "pending",
            "verified_at": None,
        }
        if existing.data:
            result = client.table("x_accounts").update(payload).eq("telegram_id", telegram_id).execute()
        else:
            result = client.table("x_accounts").insert(payload).execute()
        return result.data[0] if result.data else None

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("Failed to create X verification: %s", exc)
        return None


async def get_x_account(telegram_id: int) -> dict | None:
    """Return a user's X account link row."""

    def _op() -> dict | None:
        client = get_supabase_client()
        result = (
            client.table("x_accounts")
            .select("*")
            .eq("telegram_id", telegram_id)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("Failed to get X account: %s", exc)
        return None


async def verify_x_account(telegram_id: int, x_user_id: str, username: str, post_id: str) -> dict | None:
    """Mark a user's X account as verified."""

    def _op() -> dict | None:
        client = get_supabase_client()
        result = (
            client.table("x_accounts")
            .update(
                {
                    "x_user_id": x_user_id,
                    "username": username.lower(),
                    "verification_post_id": post_id,
                    "status": "verified",
                    "verified_at": _iso(_now()),
                }
            )
            .eq("telegram_id", telegram_id)
            .execute()
        )
        return result.data[0] if result.data else None

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("Failed to verify X account: %s", exc)
        return None


async def create_raid(
    cycle_id: int,
    created_by: int,
    target_post_id: str,
    target_url: str,
    deadline_at: datetime,
) -> dict | None:
    """Create a campaign raid."""

    def _op() -> dict | None:
        client = get_supabase_client()
        result = (
            client.table("raids")
            .insert(
                {
                    "cycle_id": cycle_id,
                    "created_by": created_by,
                    "target_post_id": target_post_id,
                    "target_url": target_url,
                    "deadline_at": _iso(deadline_at),
                    "status": "active",
                }
            )
            .execute()
        )
        return result.data[0] if result.data else None

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("Failed to create raid: %s", exc)
        return None


async def get_active_raids() -> list[dict]:
    """Return active raids in the current cycle."""

    def _op() -> list[dict]:
        client = get_supabase_client()
        cycle = (
            client.table("campaign_cycles")
            .select("*")
            .eq("status", "active")
            .limit(1)
            .execute()
        )
        if not cycle.data:
            return []
        result = (
            client.table("raids")
            .select("*")
            .eq("cycle_id", cycle.data[0]["id"])
            .eq("status", "active")
            .gt("deadline_at", _iso(_now()))
            .order("created_at", desc=True)
            .execute()
        )
        return result.data or []

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("Failed to get active raids: %s", exc)
        return []


async def get_raid(raid_id: int) -> dict | None:
    """Return a raid by id."""

    def _op() -> dict | None:
        client = get_supabase_client()
        result = client.table("raids").select("*").eq("id", raid_id).limit(1).execute()
        return result.data[0] if result.data else None

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("Failed to get raid %s: %s", raid_id, exc)
        return None


async def record_raid_message(raid_id: int, chat_id: int, message_id: int) -> dict | None:
    """Store a Telegram raid announcement message for expiry cleanup."""

    def _op() -> dict | None:
        client = get_supabase_client()
        result = (
            client.table("raid_messages")
            .insert({"raid_id": raid_id, "chat_id": chat_id, "message_id": message_id})
            .execute()
        )
        return result.data[0] if result.data else None

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("Failed to record raid message for raid %s: %s", raid_id, exc)
        return None


async def get_expired_active_raids() -> list[dict]:
    """Return active raids whose deadline has passed."""

    def _op() -> list[dict]:
        client = get_supabase_client()
        result = (
            client.table("raids")
            .select("*")
            .eq("status", "active")
            .lte("deadline_at", _iso(_now()))
            .execute()
        )
        return result.data or []

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("Failed to fetch expired raids: %s", exc)
        return []


async def close_raid(raid_id: int) -> None:
    """Mark a raid as closed."""

    def _op() -> None:
        client = get_supabase_client()
        client.table("raids").update({"status": "closed"}).eq("id", raid_id).execute()

    try:
        await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("Failed to close raid %s: %s", raid_id, exc)


async def get_raid_messages(raid_id: int) -> list[dict]:
    """Return stored Telegram messages for a raid announcement."""

    def _op() -> list[dict]:
        client = get_supabase_client()
        result = (
            client.table("raid_messages")
            .select("*")
            .eq("raid_id", raid_id)
            .execute()
        )
        return result.data or []

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("Failed to fetch raid messages for raid %s: %s", raid_id, exc)
        return []


async def has_raid_submission(raid_id: int, telegram_id: int) -> bool:
    """Return True if user already submitted an approved/pending raid proof."""

    def _op() -> bool:
        client = get_supabase_client()
        result = (
            client.table("raid_submissions")
            .select("id")
            .eq("raid_id", raid_id)
            .eq("telegram_id", telegram_id)
            .in_("status", ["approved", "pending"])
            .limit(1)
            .execute()
        )
        return bool(result.data)

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("Failed to check raid submission: %s", exc)
        return True


async def record_raid_submission(
    raid_id: int,
    cycle_id: int,
    telegram_id: int,
    x_post_id: str,
    proof_url: str,
    status: str,
    metadata: dict[str, Any] | None = None,
) -> dict | None:
    """Record a raid proof submission."""

    def _op() -> dict | None:
        client = get_supabase_client()
        result = (
            client.table("raid_submissions")
            .insert(
                {
                    "raid_id": raid_id,
                    "cycle_id": cycle_id,
                    "telegram_id": telegram_id,
                    "x_post_id": x_post_id,
                    "proof_url": proof_url,
                    "status": status,
                    "awarded_at": _iso(_now()) if status == "approved" else None,
                    "metadata": metadata or {},
                }
            )
            .execute()
        )
        return result.data[0] if result.data else None

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("Failed to record raid submission: %s", exc)
        return None


async def has_daily_x_post(telegram_id: int, cycle_id: int, day: str) -> bool:
    """Return True if user already has a pending/approved personal X post that day."""

    def _op() -> bool:
        client = get_supabase_client()
        result = (
            client.table("x_post_submissions")
            .select("id")
            .eq("cycle_id", cycle_id)
            .eq("telegram_id", telegram_id)
            .eq("submission_date", day)
            .in_("status", ["pending", "approved"])
            .limit(1)
            .execute()
        )
        return bool(result.data)

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("Failed to check daily X post: %s", exc)
        return True


async def record_x_post_submission(
    cycle_id: int,
    telegram_id: int,
    x_post_id: str,
    proof_url: str,
    day: str,
    status: str,
    metadata: dict[str, Any] | None = None,
) -> dict | None:
    """Record a personal HostFi X post submission."""

    def _op() -> dict | None:
        client = get_supabase_client()
        result = (
            client.table("x_post_submissions")
            .insert(
                {
                    "cycle_id": cycle_id,
                    "telegram_id": telegram_id,
                    "x_post_id": x_post_id,
                    "proof_url": proof_url,
                    "submission_date": day,
                    "status": status,
                    "awarded_at": _iso(_now()) if status == "approved" else None,
                    "xp_awarded": 0,
                    "metadata": metadata or {},
                }
            )
            .execute()
        )
        return result.data[0] if result.data else None

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("Failed to record X post submission: %s", exc)
        return None


async def get_x_post_submission(submission_id: int) -> dict | None:
    """Return a personal X post submission by id."""

    def _op() -> dict | None:
        client = get_supabase_client()
        result = (
            client.table("x_post_submissions")
            .select("*")
            .eq("id", submission_id)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("Failed to get X post submission %s: %s", submission_id, exc)
        return None


async def get_pending_x_post_submissions(limit: int = 10) -> list[dict]:
    """Return pending personal X post submissions for admin review."""

    def _op() -> list[dict]:
        client = get_supabase_client()
        result = (
            client.table("x_post_submissions")
            .select("*")
            .eq("status", "pending")
            .order("created_at", desc=False)
            .limit(limit)
            .execute()
        )
        return result.data or []

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("Failed to get pending X post submissions: %s", exc)
        return []


async def mark_x_post_submission_reviewed(
    submission_id: int,
    status: str,
    reviewed_by: int,
    *,
    xp_awarded: int = 0,
) -> dict | None:
    """Mark a personal X post submission as approved or rejected by an admin."""

    def _op() -> dict | None:
        client = get_supabase_client()
        now = _now()
        payload: dict[str, Any] = {
            "status": status,
            "reviewed_by": reviewed_by,
            "reviewed_at": _iso(now),
            "xp_awarded": xp_awarded,
        }
        if status == "approved":
            payload["awarded_at"] = _iso(now)
        result = (
            client.table("x_post_submissions")
            .update(payload)
            .eq("id", submission_id)
            .execute()
        )
        return result.data[0] if result.data else None

    try:
        return await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("Failed to review X post submission %s: %s", submission_id, exc)
        return None


async def delete_x_post_submission(submission_id: int) -> None:
    """Delete an unreviewed X post submission after a local delivery failure."""

    def _op() -> None:
        client = get_supabase_client()
        client.table("x_post_submissions").delete().eq("id", submission_id).eq(
            "status", "pending"
        ).execute()

    try:
        await asyncio.to_thread(_op)
    except Exception as exc:
        logger.error("Failed to delete X post submission %s: %s", submission_id, exc)
