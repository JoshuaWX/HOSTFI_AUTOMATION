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
from bot.utils.permissions import is_admin, is_superadmin
from bot.utils.permissions import is_admin_channel_chat
from config import COMMUNITY_GROUP_ID
from database.client import get_supabase_client
from database.logs import log_action

logger = logging.getLogger(__name__)


async def _get_live_community_member_count(bot: Bot | None) -> int | None:
    """Return live member count from Telegram group, or None if unavailable."""
    if not bot or not COMMUNITY_GROUP_ID:
        return None

    try:
        return await bot.get_chat_member_count(COMMUNITY_GROUP_ID)
    except Exception as exc:
        logger.warning("Could not fetch live community member count: %s", exc)
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
                "⛔ This command can only be used in the admin group.",
            )
            return

        if not await is_admin(update.effective_user.id, bot=context.bot):
            await _reply_error(update, context, "⛔ This command is for admins only.")
            return

        await update.effective_message.reply_text("📊 Loading stats...")

        stats = await _gather_stats()
        live_members = await _get_live_community_member_count(context.bot)
        community_members = live_members if live_members is not None else stats["total_users"]

        msg = (
            f"📊 <b>HOSTFI Bot Stats</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"👥 <b>Community:</b> {community_members:,} members"
            f" (+{stats['new_today']} today)\n"
            f"✅ Verified: {stats['verified_users']:,}\n"
            f"🚫 Banned: {stats['banned_users']:,}\n\n"
            f"<b>Last 24 Hours:</b>\n"
            f"🤖 AI queries: {stats['ai_queries']}\n"
            f"🚫 Spam blocked: {stats['spam_blocked']}\n"
            f"🚨 Scam blocked: {stats['scam_blocked']}\n"
            f"⚠️ Warns: {stats['warns']} | Mutes: {stats['mutes']}"
            f" | Bans: {stats['bans']}\n\n"
            f"🎫 <b>Tickets:</b>\n"
            f"Open: {stats['open_tickets']} | "
            f"Claimed: {stats['claimed_tickets']} | "
            f"Resolved today: {stats['resolved_today']}\n\n"
            f"📢 Broadcasts: {stats['broadcasts']}\n"
            f"🏆 Total XP awarded: {stats['total_xp']:,}"
        )

        await update.effective_message.reply_text(msg, parse_mode="HTML")

    except Exception as exc:
        logger.error("Error in stats_command: %s", exc)
        await _reply_error(update, context, "⚠️ Failed to load stats. Please try again.")


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
                "⛔ This command can only be used in the admin group.",
            )
            return

        if not await is_admin(update.effective_user.id, bot=context.bot):
            await _reply_error(update, context, "⛔ This command is for admins only.")
            return

        text = update.effective_message.text or ""
        parts = text.split()

        if len(parts) < 2:
            await _reply_error(
                update,
                context,
                "ℹ️ <b>Usage:</b>\n<code>/lookup 123456789</code>",
                parse_mode="HTML",
            )
            return

        try:
            target_id = int(parts[1])
        except ValueError:
            await _reply_error(update, context, "❌ Invalid Telegram ID. Must be a number.")
            return

        user_data = await _lookup_user(target_id)

        if not user_data:
            await _reply_error(
                update,
                context,
                f"❌ User <code>{target_id}</code> not found in database.",
                parse_mode="HTML",
            )
            return

        name = html.escape(user_data.get("first_name") or "N/A")
        username = html.escape(user_data.get("username") or "N/A")
        verified = "✅ Yes" if user_data.get("is_verified") else "❌ No"
        banned = "🚫 Yes" if user_data.get("is_banned") else "✅ No"
        warns = user_data.get("warn_count", 0)
        xp = user_data.get("xp_points", 0)
        join_date = user_data.get("join_date", "N/A")
        last_active = user_data.get("last_active", "N/A")

        # Get ticket count and referral count
        ticket_count, referral_count = await _user_extras(target_id)

        msg = (
            f"🔍 <b>User Lookup</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"🆔 <b>Telegram ID:</b> <code>{target_id}</code>\n"
            f"👤 <b>Name:</b> {name}\n"
            f"🏷️ <b>Username:</b> @{username}\n"
            f"✅ <b>Verified:</b> {verified}\n"
            f"🚫 <b>Banned:</b> {banned}\n"
            f"⚠️ <b>Warnings:</b> {warns}/3\n"
            f"⭐ <b>XP:</b> {xp:,}\n"
            f"🎫 <b>Tickets:</b> {ticket_count}\n"
            f"👥 <b>Referrals:</b> {referral_count}\n"
            f"📅 <b>Joined:</b> {join_date}\n"
            f"🕐 <b>Last Active:</b> {last_active}"
        )

        await update.effective_message.reply_text(msg, parse_mode="HTML")

    except Exception as exc:
        logger.error("Error in lookup_command: %s", exc)
        await _reply_error(update, context, "⚠️ Something went wrong. Please try again.")


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
                "⛔ This command can only be used in the admin group.",
            )
            return

        if not await is_superadmin(update.effective_user.id):
            await _reply_error(update, context, "⛔ This command is for the superadmin only.")
            return

        status_msg = await update.effective_message.reply_text(
            "🔄 Re-indexing knowledge base... This may take a moment."
        )

        from rag.ingestion import run_ingestion

        summary = await run_ingestion(clear_existing=True)

        await status_msg.edit_text(
            f"✅ <b>Knowledge Base Re-indexed</b>\n\n"
            f"📄 Files loaded: {summary['files_loaded']}\n"
            f"🌐 URLs scraped: {summary['urls_scraped']}\n"
            f"📦 Total chunks: {summary['total_chunks']}\n"
            f"💾 Chunks stored: {summary['chunks_stored']}",
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
        await _reply_error(update, context, f"⚠️ Re-indexing failed: {html.escape(str(exc))}")


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
                "⛔ This command can only be used in the admin group.",
            )
            return

        if not await is_admin(update.effective_user.id, bot=context.bot):
            await _reply_error(update, context, "⛔ This command is for admins only.")
            return

        help_text = (
            "🛠️ <b>HOSTFI Bot — Admin Commands</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "<b>📊 Dashboard</b>\n"
            "/stats — Bot and community statistics\n"
            "/lookup &lt;id&gt; — Look up a user record\n"
            "/adminhelp — This help menu\n\n"
            "<b>👮 Moderation</b>\n"
            "/warn &lt;reply|id&gt; [reason] — Warn a user (3 = auto-ban)\n"
            "/mute &lt;reply|id&gt; [duration] [reason] — Mute a user\n"
            "/unmute &lt;reply|id&gt; — Unmute a user\n"
            "/ban &lt;reply|id&gt; [reason] — Ban a user\n"
            "/unban &lt;id&gt; — Unban a user\n"
            "/kick &lt;reply|id&gt; [reason] — Kick a user\n"
            "/pin — Pin a replied message\n"
            "/announce &lt;text&gt; — Send formatted announcement\n\n"
            "<b>📢 Broadcast</b>\n"
            "/broadcast — Start broadcast flow\n"
            '/poll "Q?" "Opt1" "Opt2" — Create a poll\n\n'
            "<b>🏆 XP Campaign</b>\n"
            "/cycle start|finish — Manage cycles (superadmin)\n"
            "/raid create &lt;url&gt; [hours] — Create raid\n"
            "/award helpful [reason] — Award replied helpful message\n"
            "/xp add|deduct|disqualify — Adjust campaign XP\n\n"
            "<b>🎫 Tickets</b>\n"
            "/tickets — View all active tickets\n"
            "/reply &lt;HSTF-0001&gt; &lt;msg&gt; — Reply to ticket user\n"
            "/close &lt;HSTF-0001&gt; — Resolve a ticket\n\n"
            "<b>🔧 System</b>\n"
            "/reindex — Re-ingest RAG knowledge base (superadmin)\n"
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

    return (
        f"📊 <b>HOSTFI Bot Daily Report — {today}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 Community: <b>{community_members:,}</b> members"
        f" (+{stats['new_today']} today)\n"
        f"🤖 AI queries: {stats['ai_queries']}\n"
        f"🚫 Spam blocked: {stats['spam_blocked']}\n"
        f"🚨 Scam blocked: {stats['scam_blocked']}\n"
        f"⚠️ Moderation: {stats['warns']} warns, {stats['mutes']} mutes,"
        f" {stats['bans']} bans\n"
        f"🎫 Tickets: {stats['open_tickets']} open,"
        f" {stats['resolved_today']} resolved today\n"
        f"📢 Broadcasts: {stats['broadcasts']}\n"
        f"🏆 Total XP: {stats['total_xp']:,}\n\n"
        f"Bot uptime: ✅ Running"
    )
