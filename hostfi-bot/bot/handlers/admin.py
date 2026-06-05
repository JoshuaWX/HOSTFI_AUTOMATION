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
from bot.utils.keyboards import (
    admin_back_keyboard,
    admin_campaign_keyboard,
    admin_confirm_keyboard,
    admin_dashboard_keyboard,
    admin_system_keyboard,
    xpost_review_keyboard,
)
from bot.utils.permissions import is_admin, is_admin_channel_chat, is_superadmin
from bot.utils.x_api import is_x_api_configured
from config import (
    ADMIN_CHANNEL_ID,
    COMMUNITY_GROUP_IDS,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    PRIMARY_COMMUNITY_GROUP_ID,
    SUPABASE_KEY,
    SUPABASE_SERVICE_ROLE_KEY,
    SUPABASE_URL,
    X_BEARER_TOKEN,
)
from database.campaign import (
    finish_cycle,
    get_active_cycle,
    get_campaign_leaderboard,
    get_pending_x_post_submissions,
    start_cycle,
)
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


def _yes_no(value: bool) -> str:
    """Return a compact configured/missing status label."""
    return "<b>OK</b>" if value else "<b>Missing</b>"


async def _send_to_community_groups(bot, **kwargs) -> int:
    """Send one message to every configured community group."""
    sent = 0
    for chat_id in COMMUNITY_GROUP_IDS:
        await bot.send_message(chat_id=chat_id, **kwargs)
        sent += 1
    return sent


def _admin_dashboard_text(superadmin: bool = False) -> str:
    """Build the admin dashboard text."""
    lines = [
        title("Admin Dashboard", "🛠️"),
        "",
        "Use the buttons below for the common operational flows.",
        "",
        bullet("Commands still work as shortcuts."),
        bullet("Use <code>/adminhelp</code> for the full reference."),
    ]
    if superadmin:
        lines.extend(["", field("Mode", "<b>Superadmin</b>")])
    return "\n".join(lines)


async def _admin_access_ok(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    superadmin_required: bool = False,
) -> bool:
    """Validate admin dashboard access for commands and callbacks."""
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return False
    if not is_admin_channel_chat(chat.id):
        if update.callback_query:
            await update.callback_query.answer("Use this in the admin group.", show_alert=True)
        else:
            await _reply_error(update, context, status_text("error", "Use this in the admin group."))
        return False
    if superadmin_required:
        allowed = await is_superadmin(user.id)
    else:
        allowed = await is_admin(user.id, bot=context.bot)
    if not allowed:
        text = "Superadmin only." if superadmin_required else "Admins only."
        if update.callback_query:
            await update.callback_query.answer(text, show_alert=True)
        else:
            await _reply_error(update, context, status_text("error", text))
        return False
    return True


async def _send_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the main admin dashboard."""
    if not await _admin_access_ok(update, context):
        return
    superadmin = await is_superadmin(update.effective_user.id)
    await update.effective_message.reply_text(
        _admin_dashboard_text(superadmin),
        parse_mode="HTML",
        reply_markup=admin_dashboard_keyboard(superadmin),
    )


# ---------------------------------------------------------------------------
# /admin — Button dashboard
# ---------------------------------------------------------------------------


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /admin — show the button-first admin dashboard."""
    try:
        if not update.effective_user or not update.effective_message:
            return
        await _send_admin_panel(update, context)
    except Exception as exc:
        logger.error("Error in admin_command: %s", exc)
        await _reply_error(update, context, status_text("warning", "Could not open admin dashboard."))


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
                bullet("<code>/admin</code> — Button dashboard"),
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
                bullet("<code>/referrals open|close|status</code> — Referral controls"),
                bullet("<code>/raid create &lt;url&gt; [minutes]</code> — Create raid"),
                bullet("<code>/invites @username</code> — Invite stats"),
                bullet("Reply with <code>/award</code> — Award helpful message"),
                bullet("Reply: <code>/xp add 100</code> — Add XP to replied user"),
                bullet("Reply: <code>/xp deduct 50</code> — Deduct XP from replied user"),
                bullet("<code>/xp add @username AMOUNT</code> — Add XP"),
                bullet("<code>/xp deduct @username AMOUNT</code> — Deduct XP"),
                bullet("<code>/xp disqualify @username reason</code> — Disqualify user"),
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
# Admin dashboard callbacks
# ---------------------------------------------------------------------------


def _leaderboard_preview_lines(rows: list[dict], *, limit: int = 5) -> list[str]:
    """Build compact leaderboard preview lines."""
    rewards = ["$25", "$20", "$15", "$12", "$8"]
    if not rows:
        return ["No ranked users yet."]
    lines = []
    for index, row in enumerate(rows[:limit], 1):
        name = row.get("first_name") or row.get("username") or row.get("telegram_id")
        reward = f" ({rewards[index - 1]})" if index <= len(rewards) else ""
        lines.append(f"{index}. <b>{html.escape(str(name))}</b> — {int(row.get('xp') or 0):,} XP{reward}")
    return lines


def _cycle_window_lines(cycle: dict) -> list[str]:
    """Build compact start/end fields for a campaign cycle."""
    def _parse(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            return None

    start_at = _parse(cycle.get("start_at"))
    end_at = _parse(cycle.get("end_at"))
    start_label = start_at.strftime("%Y-%m-%d %H:%M UTC") if start_at else "Now"
    end_label = end_at.strftime("%Y-%m-%d %H:%M UTC") if end_at else "Manual finish"
    duration_label = "14 days" if start_at and end_at and (end_at - start_at).days == 14 else "Until finished"
    return [
        field("Started", f"<b>{start_label}</b>"),
        field("Scheduled end", f"<b>{end_label}</b>"),
        field("Default length", f"<b>{duration_label}</b>"),
    ]


async def _run_reindex_from_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Run the knowledge base reindex flow from a dashboard callback."""
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


async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle admin dashboard inline buttons."""
    query = update.callback_query
    if not query or not query.data or not query.message:
        return

    data = query.data

    if not await _admin_access_ok(update, context):
        return
    await query.answer()

    superadmin = await is_superadmin(query.from_user.id)

    if data == "admin_home":
        await query.message.reply_text(
            _admin_dashboard_text(superadmin),
            parse_mode="HTML",
            reply_markup=admin_dashboard_keyboard(superadmin),
        )
        return

    if data == "admin_tickets":
        from bot.handlers.tickets import tickets_command

        await tickets_command(update, context)
        return

    if data == "admin_stats":
        await stats_command(update, context)
        return

    if data == "admin_xposts":
        submissions = await get_pending_x_post_submissions(limit=10)
        await query.message.reply_text(
            "\n".join(
                [
                    title("X Post Reviews", "📝"),
                    "",
                    field("Pending", f"<b>{len(submissions)}</b>"),
                    "",
                    "Review cards are posted here automatically when users submit posts.",
                ]
            ),
            parse_mode="HTML",
            reply_markup=admin_back_keyboard(),
        )
        if submissions:
            from bot.handlers.campaign import _xpost_review_context, _xpost_review_text

            for submission in submissions:
                user_link, linked_handle, url_handle, url_handle_matches = await _xpost_review_context(submission)
                await query.message.reply_text(
                    _xpost_review_text(
                        submission,
                        user_link=user_link,
                        linked_handle=linked_handle,
                        url_handle=url_handle,
                        url_handle_matches=url_handle_matches,
                    ),
                    parse_mode="HTML",
                    reply_markup=xpost_review_keyboard(int(submission["id"]), str(submission.get("proof_url") or "")),
                )
        return

    if data == "admin_campaign":
        cycle = await get_active_cycle()
        lines = [title("Campaign Admin", "🏆"), ""]
        if cycle:
            lines.append(field("Active cycle", f"<b>#{cycle.get('cycle_number')}</b>"))
            lines.append(field("Status", f"<b>{html.escape(str(cycle.get('status')))}</b>"))
        else:
            lines.append("No active cycle.")
        lines.extend(["", "Use the buttons below for campaign operations."])
        await query.message.reply_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=admin_campaign_keyboard(superadmin),
        )
        return

    if data == "admin_campaign_leaderboard":
        rows = await get_campaign_leaderboard(5)
        await query.message.reply_text(
            "\n".join([title("Campaign Top 5", "🏅"), "", *_leaderboard_preview_lines(rows)]),
            parse_mode="HTML",
            reply_markup=admin_campaign_keyboard(superadmin),
        )
        return

    if data == "admin_raids":
        await query.message.reply_text(
            "\n".join(
                [
                    title("Raid Admin", "⚡"),
                    "",
                    bullet("<code>/raid create X_POST_URL [minutes]</code> — Create a raid"),
                    bullet("<code>/raids</code> — View active raids"),
                    "",
                    "Raid proof submission remains private for users.",
                ]
            ),
            parse_mode="HTML",
            reply_markup=admin_back_keyboard(),
        )
        return

    if data == "admin_xp":
        await query.message.reply_text(
            "\n".join(
                [
                    title("XP Tools", "⭐"),
                    "",
                    title("Fast Reply Shortcuts"),
                    bullet("Reply to a user message with <code>/xp add 100</code>"),
                    bullet("Reply to a user message with <code>/xp deduct 50</code>"),
                    "",
                    title("Direct Commands"),
                    bullet("<code>/xp add @username 100</code>"),
                    bullet("<code>/xp deduct @username 50</code>"),
                    bullet("<code>/xp disqualify @username reason</code>"),
                    "",
                    "Large XP changes and disqualification should be double-checked before sending.",
                ]
            ),
            parse_mode="HTML",
            reply_markup=admin_back_keyboard(),
        )
        return

    if data == "admin_broadcasts":
        await query.message.reply_text(
            "\n".join(
                [
                    title("Broadcasts", "📣"),
                    "",
                    bullet("<code>/broadcast</code> — Guided rich broadcast flow"),
                    bullet("<code>/announce your message</code> — Quick announcement"),
                    bullet('<code>/poll "Question?" "Option 1" "Option 2"</code> — Poll'),
                ]
            ),
            parse_mode="HTML",
            reply_markup=admin_back_keyboard(),
        )
        return

    if data == "admin_moderation":
        await query.message.reply_text(
            "\n".join(
                [
                    title("Moderation", "🛡️"),
                    "",
                    bullet("Reply with <code>/warn reason</code>"),
                    bullet("Reply with <code>/mute 30m reason</code>"),
                    bullet("Reply with <code>/ban reason</code>"),
                    bullet("<code>/kick</code>, <code>/unmute</code>, <code>/unban</code>, <code>/pin</code>"),
                ]
            ),
            parse_mode="HTML",
            reply_markup=admin_back_keyboard(),
        )
        return

    if data == "admin_system":
        if not await _admin_access_ok(update, context, superadmin_required=True):
            return
        await query.message.reply_text(
            "\n".join(
                [
                    title("System", "⚙️"),
                    "",
                    "Superadmin-only checks and maintenance actions.",
                ]
            ),
            parse_mode="HTML",
            reply_markup=admin_system_keyboard(),
        )
        return

    if data == "admin_config_health":
        if not await _admin_access_ok(update, context, superadmin_required=True):
            return
        await query.message.reply_text(
            "\n".join(
                [
                    title("Config Health", "🧪"),
                    "",
                    field("Admin channel", _yes_no(bool(ADMIN_CHANNEL_ID))),
                    field("Community groups", f"<b>{len(COMMUNITY_GROUP_IDS)}</b>"),
                    field("Primary group", f"<code>{PRIMARY_COMMUNITY_GROUP_ID}</code>" if PRIMARY_COMMUNITY_GROUP_ID else "<b>Missing</b>"),
                    field("Gemini key", _yes_no(bool(GEMINI_API_KEY))),
                    field("Gemini model", f"<code>{html.escape(GEMINI_MODEL)}</code>"),
                    field("X bearer token", _yes_no(bool(X_BEARER_TOKEN))),
                    field("Supabase URL", _yes_no(bool(SUPABASE_URL))),
                    field("Service role key", _yes_no(bool(SUPABASE_SERVICE_ROLE_KEY))),
                    field("Supabase key active", _yes_no(bool(SUPABASE_KEY))),
                ]
            ),
            parse_mode="HTML",
            reply_markup=admin_system_keyboard(),
        )
        return

    if data == "admin_api_status":
        if not await _admin_access_ok(update, context, superadmin_required=True):
            return
        await query.message.reply_text(
            "\n".join(
                [
                    title("API Status", "🔌"),
                    "",
                    field("Gemini", _yes_no(bool(GEMINI_API_KEY))),
                    field("Gemini model", f"<code>{html.escape(GEMINI_MODEL)}</code>"),
                    field("X API", _yes_no(is_x_api_configured())),
                    field("Supabase", _yes_no(bool(SUPABASE_URL and SUPABASE_KEY))),
                    "",
                    "This checks configuration only; it does not spend API credits.",
                ]
            ),
            parse_mode="HTML",
            reply_markup=admin_system_keyboard(),
        )
        return

    if data == "admin_cycle_start_preview":
        if not await _admin_access_ok(update, context, superadmin_required=True):
            return
        cycle = await get_active_cycle()
        if cycle:
            await query.message.reply_text(
                status_text("info", f"Cycle #{cycle.get('cycle_number')} is already active."),
                parse_mode="HTML",
                reply_markup=admin_campaign_keyboard(superadmin),
            )
            return
        await query.message.reply_text(
            title("Start Campaign Cycle", "⚠️")
            + "\n\nThis will start a new cycle and reset visible campaign XP.",
            parse_mode="HTML",
            reply_markup=admin_confirm_keyboard("admin_cycle_start_confirm", "admin_campaign"),
        )
        return

    if data == "admin_cycle_start_confirm":
        if not await _admin_access_ok(update, context, superadmin_required=True):
            return
        cycle = await start_cycle(query.from_user.id)
        if not cycle:
            await query.message.reply_text(status_text("error", "Could not start cycle."), parse_mode="HTML")
            return
        await query.message.reply_text(
            title("Campaign Cycle Started", "✅")
            + "\n\n"
            + field("Cycle", f"<b>#{cycle.get('cycle_number')}</b>")
            + "\n"
            + "\n".join(_cycle_window_lines(cycle)),
            parse_mode="HTML",
            reply_markup=admin_campaign_keyboard(superadmin),
        )
        return

    if data == "admin_cycle_finish_preview":
        if not await _admin_access_ok(update, context, superadmin_required=True):
            return
        cycle = await get_active_cycle()
        if not cycle:
            await query.message.reply_text(status_text("error", "No active cycle to finish."), parse_mode="HTML")
            return
        winners = await get_campaign_leaderboard(5)
        lines = [
            title("Finish Campaign Cycle", "⚠️"),
            "",
            field("Cycle", f"<b>#{cycle.get('cycle_number')}</b>"),
            "",
            title("Projected Winners"),
            *_leaderboard_preview_lines(winners),
            "",
            "Confirming will finalize winners, reset visible XP, and close current raids.",
            "No new cycle will open until you start one manually.",
        ]
        await query.message.reply_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=admin_confirm_keyboard("admin_cycle_finish_confirm", "admin_campaign"),
        )
        return

    if data == "admin_cycle_finish_confirm":
        if not await _admin_access_ok(update, context, superadmin_required=True):
            return
        result = await finish_cycle(query.from_user.id)
        if not result:
            await query.message.reply_text(status_text("error", "No active cycle to finish."), parse_mode="HTML")
            return
        winners = result["winners"]
        lines = [
            title(f"Cycle #{result['finished_cycle'].get('cycle_number')} Finished", "🏁"),
            "",
            *_leaderboard_preview_lines(winners),
        ]
        lines.extend(["", "No new cycle was opened. Run <code>/cycle start</code> when ready."])
        text = "\n".join(lines)
        await query.message.reply_text(text, parse_mode="HTML", reply_markup=admin_campaign_keyboard(superadmin))
        try:
            await _send_to_community_groups(context.bot, text=text, parse_mode="HTML")
        except Exception as exc:
            logger.warning("Could not announce cycle finish to community: %s", exc)
        return

    if data == "admin_reindex_preview":
        if not await _admin_access_ok(update, context, superadmin_required=True):
            return
        await query.message.reply_text(
            title("Reindex Knowledge Base", "⚠️")
            + "\n\nThis clears and rebuilds the RAG knowledge base. It may take a moment.",
            parse_mode="HTML",
            reply_markup=admin_confirm_keyboard("admin_reindex_confirm", "admin_system"),
        )
        return

    if data == "admin_reindex_confirm":
        if not await _admin_access_ok(update, context, superadmin_required=True):
            return
        try:
            await _run_reindex_from_message(update, context)
        except Exception as exc:
            logger.error("Dashboard reindex failed: %s", exc)
            await _reply_error(update, context, status_text("warning", f"Re-indexing failed: {str(exc)}"))
        return


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
