"""
Module: admin.py
Purpose: Admin-only commands — /stats, /lookup, /reindex, /adminhelp
Author: HOSTFI Bot Team
"""

import asyncio
import html
import logging
from datetime import datetime, timedelta, timezone

from telegram import Bot, Update
from telegram.ext import ContextTypes

from bot.utils.auto_delete import schedule_error_delete
from bot.utils.formatter import bullet, field, status_text, title
from bot.utils.permissions import is_admin, is_superadmin
from bot.utils.permissions import is_admin_channel_chat
from config import COMMUNITY_GROUP_IDS
from database.client import get_supabase_client
from database.logs import log_action

logger = logging.getLogger(__name__)


async def _get_live_community_member_count(bot: Bot | None) -> int | None:
    """Return live member count from Telegram group, or None if unavailable."""
    if not bot or not COMMUNITY_GROUP_IDS:
        return None

    total = 0
    found_any = False
    for chat_id in COMMUNITY_GROUP_IDS:
        try:
            total += await bot.get_chat_member_count(chat_id)
            found_any = True
        except Exception as exc:
            logger.warning("Could not fetch live community member count for %s: %s", chat_id, exc)
    if found_any:
        return total

    return None


async def _reply_error(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    parse_mode: str | None = None,
) -> None:
    """Send an error/denial message and auto-delete it in group chats."""
    if not update.effective_message:
        return
    msg = await update.effective_message.reply_text(text, parse_mode=parse_mode)
    await schedule_error_delete(msg, context, 5)


# ---------------------------------------------------------------------------
# /stats — Community and bot statistics
# ---------------------------------------------------------------------------


async def stats_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle /stats — show community and bot statistics (admin only).

    Queries Supabase for: total users, new today, tickets, moderation
    actions, spam blocks, and AI queries from the last 24 hours.

    Args:
        update: Incoming Telegram update
        context: Bot context
    """
    try:
        if not update.effective_user or not update.effective_message:
            return

        if not is_admin_channel_chat(update.effective_chat.id if update.effective_chat else None):
            await _reply_error(
                update,
                context,
                status_text("error", "This command can only be used in the admin group."),
            )
            return

        if not await is_admin(update.effective_user.id, bot=context.bot):
            await _reply_error(update, context, status_text("error", "This command is for admins only."))
            return

        await update.effective_message.reply_text("Loading stats...")

        stats = await _gather_stats()
        live_members = await _get_live_community_member_count(context.bot)
        community_members = live_members if live_members is not None else stats["total_users"]

        msg = "\n".join(
            [
                title("HOSTFI Bot Stats", "📊"),
                "",
                field("Community", f"<b>{community_members:,}</b> members (+{stats['new_today']} today)"),
                field("Verified", stats["verified_users"]),
                field("Banned", stats["banned_users"]),
                "",
                title("Last 24 Hours"),
                field("AI queries", stats["ai_queries"]),
                field("Spam blocked", stats["spam_blocked"]),
                field("Scam blocked", stats["scam_blocked"]),
                field("Moderation", f"{stats['warns']} warns, {stats['mutes']} mutes, {stats['bans']} bans"),
                "",
                title("Tickets"),
                field("Open", stats["open_tickets"]),
                field("Claimed", stats["claimed_tickets"]),
                field("Resolved today", stats["resolved_today"]),
                "",
                field("Broadcasts", stats["broadcasts"]),
                field("Total XP awarded", f"{stats['total_xp']:,}"),
            ]
        )

        await update.effective_message.reply_text(msg, parse_mode="HTML")

    except Exception as exc:
        logger.error("Error in stats_command: %s", exc)
        await _reply_error(update, context, status_text("warning", "Failed to load stats. Please try again."))


async def _gather_stats() -> dict:
    """
    Gather all statistics from Supabase in parallel.

    Returns:
        Dict with all stat fields
    """

    def _query() -> dict:
        client = get_supabase_client()
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

        # Users
        all_users = client.table("users").select("id", count="exact").execute()
        total_users = all_users.count or 0

        new_today_res = (
            client.table("users")
            .select("id", count="exact")
            .gte("join_date", today_start)
            .execute()
        )
        new_today = new_today_res.count or 0

        verified_res = (
            client.table("users")
            .select("id", count="exact")
            .eq("is_verified", True)
            .execute()
        )
        verified_users = verified_res.count or 0

        banned_res = (
            client.table("users")
            .select("id", count="exact")
            .eq("is_banned", True)
            .execute()
        )
        banned_users = banned_res.count or 0

        # XP total
        xp_res = client.table("users").select("xp_points").execute()
        total_xp = sum(u.get("xp_points", 0) for u in (xp_res.data or []))

        # Audit logs (last 24h)
        yesterday = (now - timedelta(hours=24)).isoformat()

        def _count_action(action: str) -> int:
            res = (
                client.table("audit_logs")
                .select("id", count="exact")
                .eq("action", action)
                .gte("created_at", yesterday)
                .execute()
            )
            return res.count or 0

        ai_queries = _count_action("ai_query")
        spam_blocked = _count_action("spam_blocked")
        scam_blocked = _count_action("scam_blocked")
        warns = _count_action("warn")
        mutes = _count_action("mute")
        bans = _count_action("ban")
        broadcasts = _count_action("broadcast")

        # Tickets
        open_res = (
            client.table("tickets")
            .select("id", count="exact")
            .eq("status", "open")
            .execute()
        )
        open_tickets = open_res.count or 0

        claimed_res = (
            client.table("tickets")
            .select("id", count="exact")
            .eq("status", "claimed")
            .execute()
        )
        claimed_tickets = claimed_res.count or 0

        resolved_today_res = (
            client.table("tickets")
            .select("id", count="exact")
            .in_("status", ["resolved", "closed"])
            .gte("resolved_at", today_start)
            .execute()
        )
        resolved_today = resolved_today_res.count or 0

        return {
            "total_users": total_users,
            "new_today": new_today,
            "verified_users": verified_users,
            "banned_users": banned_users,
            "total_xp": total_xp,
            "ai_queries": ai_queries,
            "spam_blocked": spam_blocked,
            "scam_blocked": scam_blocked,
            "warns": warns,
            "mutes": mutes,
            "bans": bans,
            "broadcasts": broadcasts,
            "open_tickets": open_tickets,
            "claimed_tickets": claimed_tickets,
            "resolved_today": resolved_today,
        }

    return await asyncio.to_thread(_query)


# ---------------------------------------------------------------------------
# /lookup <user_id> — Look up a user's record
# ---------------------------------------------------------------------------


async def lookup_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle /lookup — look up a user's database record (admin only).

    Usage: /lookup <telegram_id>

    Args:
        update: Incoming Telegram update
        context: Bot context
    """
    try:
        if not update.effective_user or not update.effective_message:
            return

        if not is_admin_channel_chat(update.effective_chat.id if update.effective_chat else None):
            await _reply_error(
                update,
                context,
                status_text("error", "This command can only be used in the admin group."),
            )
            return

        if not await is_admin(update.effective_user.id, bot=context.bot):
            await _reply_error(update, context, status_text("error", "This command is for admins only."))
            return

        text = update.effective_message.text or ""
        parts = text.split()

        if len(parts) < 2:
            await _reply_error(
                update,
                context,
                "<b>Usage</b>\n<code>/lookup 123456789</code>",
                parse_mode="HTML",
            )
            return

        try:
            target_id = int(parts[1])
        except ValueError:
            await _reply_error(update, context, status_text("error", "Invalid Telegram ID. Must be a number."))
            return

        user_data = await _lookup_user(target_id)

        if not user_data:
            await _reply_error(
                update,
                context,
                status_text("error", f"User {target_id} not found in database."),
                parse_mode="HTML",
            )
            return

        name = html.escape(user_data.get("first_name") or "N/A")
        username = html.escape(user_data.get("username") or "N/A")
        verified = "Yes" if user_data.get("is_verified") else "No"
        banned = "Yes" if user_data.get("is_banned") else "No"
        warns = user_data.get("warn_count", 0)
        xp = user_data.get("xp_points", 0)
        join_date = user_data.get("join_date", "N/A")
        last_active = user_data.get("last_active", "N/A")

        # Get ticket count and referral count
        ticket_count, referral_count = await _user_extras(target_id)

        msg = "\n".join(
            [
                title("User Lookup", "🔍"),
                "",
                field("Telegram ID", f"<code>{target_id}</code>"),
                field("Name", name),
                field("Username", f"@{username}"),
                field("Verified", verified),
                field("Banned", banned),
                field("Warnings", f"{warns}/3"),
                field("XP", f"{xp:,}"),
                field("Tickets", ticket_count),
                field("Referrals", referral_count),
                field("Joined", join_date),
                field("Last active", last_active),
            ]
        )

        await update.effective_message.reply_text(msg, parse_mode="HTML")

    except Exception as exc:
        logger.error("Error in lookup_command: %s", exc)
        await _reply_error(update, context, status_text("warning", "Something went wrong. Please try again."))


async def _lookup_user(telegram_id: int) -> dict | None:
    """Query a single user record from Supabase."""

    def _op() -> dict | None:
        client = get_supabase_client()
        result = (
            client.table("users")
            .select("*")
            .eq("telegram_id", telegram_id)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None

    return await asyncio.to_thread(_op)


async def _user_extras(telegram_id: int) -> tuple[int, int]:
    """Get ticket count and referral count for a user."""

    def _op() -> tuple[int, int]:
        client = get_supabase_client()

        tickets = (
            client.table("tickets")
            .select("id", count="exact")
            .eq("user_telegram_id", telegram_id)
            .execute()
        )
        ticket_count = tickets.count or 0

        referrals = (
            client.table("referrals")
            .select("id", count="exact")
            .eq("referrer_telegram_id", telegram_id)
            .execute()
        )
        referral_count = referrals.count or 0

        return ticket_count, referral_count

    return await asyncio.to_thread(_op)


# ---------------------------------------------------------------------------
# /reindex — Trigger RAG knowledge base re-ingestion
# ---------------------------------------------------------------------------


async def reindex_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle /reindex — trigger RAG knowledge base re-ingestion (superadmin only).

    Clears the existing ChromaDB collection and re-ingests all
    knowledge base files.

    Args:
        update: Incoming Telegram update
        context: Bot context
    """
    try:
        if not update.effective_user or not update.effective_message:
            return

        if not is_admin_channel_chat(update.effective_chat.id if update.effective_chat else None):
            await _reply_error(
                update,
                context,
                status_text("error", "This command can only be used in the admin group."),
            )
            return

        if not await is_superadmin(update.effective_user.id):
            await _reply_error(update, context, status_text("error", "This command is for the superadmin only."))
            return

        status_msg = await update.effective_message.reply_text(
            "Re-indexing knowledge base. This may take a moment."
        )

        from rag.ingestion import run_ingestion

        summary = await run_ingestion(clear_existing=True)

        await status_msg.edit_text(
            title("Knowledge Base Re-indexed", "✅")
            + f"\n\n{field('Files loaded', summary['files_loaded'])}"
            + f"\n{field('URLs scraped', summary['urls_scraped'])}"
            + f"\n{field('Total chunks', summary['total_chunks'])}"
            + f"\n{field('Chunks stored', summary['chunks_stored'])}",
            parse_mode="HTML",
        )

        await log_action(
            action="reindex",
            admin_telegram_id=update.effective_user.id,
            metadata=summary,
        )

        logger.info(
            "Knowledge base re-indexed by admin %s: %s",
            update.effective_user.id,
            summary,
        )

    except Exception as exc:
        logger.error("Error in reindex_command: %s", exc)
        await _reply_error(update, context, status_text("warning", f"Re-indexing failed: {str(exc)}"))


# ---------------------------------------------------------------------------
# /adminhelp — Show admin command reference
# ---------------------------------------------------------------------------


async def adminhelp_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle /adminhelp — show all admin-only commands.

    Args:
        update: Incoming Telegram update
        context: Bot context
    """
    try:
        if not update.effective_user or not update.effective_message:
            return

        if not is_admin_channel_chat(update.effective_chat.id if update.effective_chat else None):
            await _reply_error(
                update,
                context,
                status_text("error", "This command can only be used in the admin group."),
            )
            return

        if not await is_admin(update.effective_user.id, bot=context.bot):
            await _reply_error(update, context, status_text("error", "This command is for admins only."))
            return

        help_text = "\n".join(
            [
                title("Admin Commands", "🛠️"),
                "",
                title("Dashboard"),
                bullet("<code>/stats</code> — Bot and community stats"),
                bullet("<code>/lookup &lt;id&gt;</code> — User record"),
                "",
                title("Moderation"),
                bullet("<code>/warn</code>, <code>/mute</code>, <code>/ban</code>, <code>/kick</code>"),
                bullet("<code>/pin</code>, <code>/announce &lt;text&gt;</code>"),
                "",
                title("Broadcast"),
                bullet("<code>/broadcast</code> — Broadcast flow"),
                bullet('<code>/poll "Q?" "Opt1" "Opt2"</code> — Poll'),
                "",
                title("XP Campaign"),
                bullet("<code>/cycle start|finish</code> — Manage cycles"),
                bullet("<code>/raid create &lt;url&gt; [hours]</code> — Create raid"),
                bullet("<code>/award helpful [reason]</code> — Award helpful message"),
                bullet("<code>/xp add|deduct|disqualify</code> — Adjust XP"),
                "",
                title("Tickets"),
                bullet("<code>/tickets</code> — Active tickets"),
                bullet("<code>/reply &lt;HSTF-0001&gt; &lt;msg&gt;</code> — Reply"),
                bullet("<code>/close &lt;HSTF-0001&gt;</code> — Resolve"),
                "",
                title("System"),
                bullet("<code>/reindex</code> — Re-ingest RAG knowledge base"),
            ]
        )

        await update.effective_message.reply_text(
            help_text, parse_mode="HTML"
        )

    except Exception as exc:
        logger.error("Error in adminhelp_command: %s", exc)


# ---------------------------------------------------------------------------
# Daily report builder (for scheduler)
# ---------------------------------------------------------------------------


async def build_daily_report(bot: Bot | None = None) -> str:
    """
    Build the daily admin report message.

    Called by the scheduler at 7 AM WAT to post to the admin channel.

    Returns:
        HTML-formatted daily report string
    """
    stats = await _gather_stats()
    live_members = await _get_live_community_member_count(bot)
    community_members = live_members if live_members is not None else stats["total_users"]
    today = datetime.now(timezone.utc).strftime("%B %d, %Y")
    community_line = f"<b>{community_members:,}</b> members (+{stats['new_today']} today)"
    moderation_line = f"{stats['warns']} warns, {stats['mutes']} mutes, {stats['bans']} bans"
    ticket_line = f"{stats['open_tickets']} open, {stats['resolved_today']} resolved today"
    total_xp = f"{stats['total_xp']:,}"

    return (
        f"{title(f'HOSTFI Bot Daily Report — {today}', '📊')}\n\n"
        f"{field('Community', community_line)}\n"
        f"{field('AI queries', stats['ai_queries'])}\n"
        f"{field('Spam blocked', stats['spam_blocked'])}\n"
        f"{field('Scam blocked', stats['scam_blocked'])}\n"
        f"{field('Moderation', moderation_line)}\n"
        f"{field('Tickets', ticket_line)}\n"
        f"{field('Broadcasts', stats['broadcasts'])}\n"
        f"{field('Total XP', total_xp)}\n\n"
        f"{field('Bot uptime', 'Running')}"
    )
