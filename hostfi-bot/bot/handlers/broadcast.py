"""
Module: broadcast.py
Purpose: Broadcast, poll, and campaign XP leaderboard handlers
Author: HOSTFI Bot Team
"""

import html
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions, Update
from telegram.ext import ContextTypes, ConversationHandler, filters

from bot.utils.formatter import bullet, field, status_text, title
from bot.utils.keyboards import confirm_broadcast_keyboard
from bot.utils.permissions import is_admin
from bot.utils.rate_limiter import check_rate_limit, get_redis
from config import ADMIN_CHANNEL_ID, COMMUNITY_GROUP_IDS, TELEGRAM_BOT_TOKEN
from database.logs import log_action
from database.campaign import get_campaign_leaderboard, get_campaign_rank
from database.users import (
    get_or_create_user,
)

logger = logging.getLogger(__name__)


async def _send_to_community_groups(bot, method: str, **kwargs) -> int:
    """Send content to every configured community group."""
    sent = 0
    for chat_id in COMMUNITY_GROUP_IDS:
        send = getattr(bot, method)
        await send(chat_id=chat_id, **kwargs)
        sent += 1
    return sent


# ---------------------------------------------------------------------------
# ConversationHandler states for broadcast flow
# ---------------------------------------------------------------------------

BROADCAST_CONTENT, BROADCAST_SCHEDULE = range(2)

# In-memory pending broadcasts keyed by broadcast_id
_pending_broadcasts: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# /start command
# ---------------------------------------------------------------------------


async def start_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle /start welcome message.

    Campaign invite XP is tracked through /invite links, not /start deep links.

    Args:
        update: Incoming Telegram update
        context: Bot context
    """
    try:
        if not update.effective_user or not update.effective_message:
            return

        user = update.effective_user
        await get_or_create_user(user.id, user.username, user.first_name)

        await update.effective_message.reply_text(
            "\n".join(
                [
                    title("HOSTFI Bot", "👋"),
                    "",
                    "Your crypto community assistant.",
                    "",
                    title("Core"),
                    bullet("<code>/ask</code> — AI support"),
                    bullet("<code>/support</code> — Open a ticket"),
                    "",
                    title("Campaign"),
                    bullet("<code>/campaign</code> — XP panel"),
                    bullet("<code>/rank</code> — Your rank"),
                    bullet("<code>/leaderboard</code> — Top members"),
                ]
            ),
            parse_mode="HTML",
        )

    except Exception as exc:
        logger.error("Error in start_command: %s", exc)


# ---------------------------------------------------------------------------
# /rank command
# ---------------------------------------------------------------------------


async def rank_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle /rank — show user's current XP and leaderboard position.

    Args:
        update: Incoming Telegram update
        context: Bot context
    """
    try:
        if not update.effective_user or not update.effective_message:
            return

        user_id = update.effective_user.id

        if not await check_rate_limit(user_id, "command", limit=10, window=60):
            await update.effective_message.reply_text(
                status_text("warning", "Too many requests. Please wait a moment.")
            )
            return

        xp, rank, total, cycle = await get_campaign_rank(user_id)

        name = html.escape(
            update.effective_user.first_name or str(user_id)
        )

        if xp >= 500:
            badge = "👑 Legend"
        elif xp >= 200:
            badge = "💎 Diamond"
        elif xp >= 100:
            badge = "🥇 Gold"
        elif xp >= 50:
            badge = "🥈 Silver"
        elif xp >= 20:
            badge = "🥉 Bronze"
        else:
            badge = "🌱 Newcomer"

        lines = [title(f"{name}'s Profile", "🏆"), ""]
        if cycle:
            lines.append(field("Cycle", f"<b>#{cycle.get('cycle_number')}</b>"))
        lines.append(field("XP", f"<b>{xp:,}</b>"))
        lines.append(field("Rank", f"<b>#{rank} of {total}</b>" if rank else "Not ranked yet"))
        lines.extend(
            [
                field("Badge", badge),
                "",
                title("Earn XP"),
                bullet("50 XP — approved X raids"),
                bullet("70 XP — Telegram invites after 5h"),
                bullet("Admin-reviewed XP — HostFi X posts, once daily"),
                bullet("100 XP — approved helpful contributions"),
                "",
                "Get your invite link with <code>/invite</code>.",
            ]
        )
        msg = "\n".join(lines)

        await update.effective_message.reply_text(msg, parse_mode="HTML")

    except Exception as exc:
        logger.error("Error in rank_command: %s", exc)
        await update.effective_message.reply_text(
            status_text("warning", "Something went wrong. Please try again later.")
        )


# ---------------------------------------------------------------------------
# /leaderboard command
# ---------------------------------------------------------------------------


async def leaderboard_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle /leaderboard — show top 10 members by XP.

    Args:
        update: Incoming Telegram update
        context: Bot context
    """
    try:
        if not update.effective_user or not update.effective_message:
            return

        user_id = update.effective_user.id

        if not await check_rate_limit(user_id, "command", limit=10, window=60):
            await update.effective_message.reply_text(
                status_text("warning", "Too many requests. Please wait a moment.")
            )
            return

        top_users = await get_campaign_leaderboard(10)

        if not top_users:
            await update.effective_message.reply_text(
                status_text("info", "No campaign leaderboard data yet. Earn XP through raids, invites, posts, and approved helpful contributions.")
            )
            return

        lines: list[str] = [title("XP Leaderboard", "🏅"), ""]

        for i, user in enumerate(top_users):
            prefix = f"{i + 1}."
            name = user.get("first_name") or user.get("username") or "User"
            safe_name = html.escape(name)
            xp = user.get("xp", 0)
            lines.append(f"{prefix} <b>{safe_name}</b> — {xp:,} XP")

        lines.append("")
        lines.append("Campaign ties are ranked by earliest approved XP event.")

        await update.effective_message.reply_text(
            "\n".join(lines), parse_mode="HTML"
        )

    except Exception as exc:
        logger.error("Error in leaderboard_command: %s", exc)
        await update.effective_message.reply_text(
            status_text("warning", "Something went wrong. Please try again later.")
        )


# ---------------------------------------------------------------------------
# /broadcast command — step 1: initiate (admin only)
# ---------------------------------------------------------------------------


async def broadcast_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """
    Handle /broadcast — initiate broadcast flow (admin only).

    Prompts the admin to send the announcement content.

    Args:
        update: Incoming Telegram update
        context: Bot context

    Returns:
        BROADCAST_CONTENT state for ConversationHandler
    """
    try:
        if not update.effective_user or not update.effective_message:
            return ConversationHandler.END

        from config import ADMIN_CHANNEL_ID as _ADMIN_CH
        if update.effective_chat and update.effective_chat.id != _ADMIN_CH:
            await update.effective_message.reply_text(
                status_text("error", "This command can only be used in the admin channel.")
            )
            return ConversationHandler.END

        if not await is_admin(update.effective_user.id, bot=context.bot):
            await update.effective_message.reply_text(
                status_text("error", "This command is for admins only.")
            )
            return ConversationHandler.END

        await update.effective_message.reply_text(
            f"{title('Broadcast Composer', '📢')}\n\n"
            "Send your announcement message now.\n"
            "Supported: <b>text, photo, or video</b> with optional caption.\n\n"
            "Send <code>/cancel</code> to abort.",
            parse_mode="HTML",
        )
        return BROADCAST_CONTENT

    except Exception as exc:
        logger.error("Error in broadcast_command: %s", exc)
        return ConversationHandler.END


# ---------------------------------------------------------------------------
# Broadcast step 2: receive content
# ---------------------------------------------------------------------------


async def broadcast_receive_content(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """
    Receive broadcast content (text, photo, or video) and show preview.

    Stores the content in pending broadcasts and shows a preview with
    confirm/cancel buttons.

    Args:
        update: Incoming Telegram update
        context: Bot context

    Returns:
        ConversationHandler.END (async flow continues via callback)
    """
    try:
        if not update.effective_message or not update.effective_user:
            return ConversationHandler.END

        msg = update.effective_message
        broadcast_id = str(uuid.uuid4())[:8]

        broadcast_data: dict = {
            "admin_id": update.effective_user.id,
            "type": "text",
            "text": None,
            "photo_id": None,
            "video_id": None,
            "caption": None,
        }

        if msg.photo:
            broadcast_data["type"] = "photo"
            broadcast_data["photo_id"] = msg.photo[-1].file_id
            broadcast_data["caption"] = msg.caption or ""
        elif msg.video:
            broadcast_data["type"] = "video"
            broadcast_data["video_id"] = msg.video.file_id
            broadcast_data["caption"] = msg.caption or ""
        elif msg.text:
            broadcast_data["type"] = "text"
            broadcast_data["text"] = msg.text
        else:
            await msg.reply_text(
                status_text("error", "Unsupported content type. Please send text, photo, or video.")
            )
            return ConversationHandler.END

        _pending_broadcasts[broadcast_id] = broadcast_data

        # Show preview
        preview_header = f"{title('Broadcast Preview', '📢')}\n\n"
        confirm_text = "\n\nConfirm to send to the community group."

        if broadcast_data["type"] == "text":
            preview = preview_header + (broadcast_data["text"] or "")
            await msg.reply_text(
                preview + confirm_text,
                parse_mode="HTML",
                reply_markup=confirm_broadcast_keyboard(broadcast_id),
            )
        elif broadcast_data["type"] == "photo":
            await msg.reply_photo(
                photo=broadcast_data["photo_id"],
                caption=(
                    preview_header
                    + (broadcast_data["caption"] or "")
                    + confirm_text
                ),
                parse_mode="HTML",
                reply_markup=confirm_broadcast_keyboard(broadcast_id),
            )
        elif broadcast_data["type"] == "video":
            await msg.reply_video(
                video=broadcast_data["video_id"],
                caption=(
                    preview_header
                    + (broadcast_data["caption"] or "")
                    + confirm_text
                ),
                parse_mode="HTML",
                reply_markup=confirm_broadcast_keyboard(broadcast_id),
            )

        return ConversationHandler.END

    except Exception as exc:
        logger.error("Error in broadcast_receive_content: %s", exc)
        await update.effective_message.reply_text(
            status_text("warning", "Something went wrong. Please try /broadcast again.")
        )
        return ConversationHandler.END


# ---------------------------------------------------------------------------
# Broadcast confirmation callback
# ---------------------------------------------------------------------------


async def broadcast_confirm_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle broadcast confirm/cancel button presses.

    Callback data format: broadcast_confirm_{id} or broadcast_cancel_{id}

    Args:
        update: Incoming callback query update
        context: Bot context
    """
    query = update.callback_query
    if not query or not query.data:
        return

    if not await is_admin(query.from_user.id, bot=context.bot):
        await query.answer("Admins only.", show_alert=True)
        return

    parts = query.data.split("_")
    # Expected: ["broadcast", "confirm"|"cancel", "<id>"]
    if len(parts) != 3:
        return

    action = parts[1]
    broadcast_id = parts[2]

    broadcast_data = _pending_broadcasts.pop(broadcast_id, None)

    if broadcast_data is None:
        await query.answer("Broadcast expired or not found.", show_alert=True)
        return

    if action == "cancel":
        await query.answer("Broadcast cancelled.")
        await query.edit_message_text(status_text("info", "Broadcast cancelled."))
        logger.info(
            "Broadcast %s cancelled by admin %s",
            broadcast_id,
            query.from_user.id,
        )
        return

    # --- Send broadcast to community group ---
    await query.answer("Sending broadcast...")

    try:
        btype = broadcast_data["type"]

        if btype == "text":
            sent = await _send_to_community_groups(
                context.bot,
                "send_message",
                text=broadcast_data["text"],
                parse_mode="HTML",
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
        elif btype == "photo":
            sent = await _send_to_community_groups(
                context.bot,
                "send_photo",
                photo=broadcast_data["photo_id"],
                caption=broadcast_data["caption"],
                parse_mode="HTML",
            )
        elif btype == "video":
            sent = await _send_to_community_groups(
                context.bot,
                "send_video",
                video=broadcast_data["video_id"],
                caption=broadcast_data["caption"],
                parse_mode="HTML",
            )
        else:
            sent = 0

        await query.edit_message_text(
            status_text("success", f"Broadcast sent to {sent} community group(s).")
        )

        # Log the broadcast
        await log_action(
            action="broadcast",
            admin_telegram_id=broadcast_data["admin_id"],
            metadata={
                "broadcast_id": broadcast_id,
                "type": btype,
                "text_preview": (broadcast_data.get("text") or broadcast_data.get("caption") or "")[:100],
            },
        )

        logger.info(
            "Broadcast %s sent by admin %s (type=%s)",
            broadcast_id,
            broadcast_data["admin_id"],
            btype,
        )

    except Exception as exc:
        logger.error("Broadcast %s send failed: %s", broadcast_id, exc)
        await query.edit_message_text(
            status_text("warning", f"Broadcast failed: {html.escape(str(exc))}")
        )


# ---------------------------------------------------------------------------
# Broadcast cancel (conversation handler)
# ---------------------------------------------------------------------------


async def broadcast_cancel(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """
    Cancel the broadcast conversation flow.

    Args:
        update: Incoming Telegram update
        context: Bot context

    Returns:
        ConversationHandler.END
    """
    if update.effective_message:
        await update.effective_message.reply_text(status_text("info", "Broadcast cancelled."))
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# /poll command (admin only)
# ---------------------------------------------------------------------------


async def poll_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle /poll — create a native Telegram poll (admin only).

    Usage: /poll "Question?" "Option 1" "Option 2" "Option 3"
    Minimum 2 options, maximum 10.

    Args:
        update: Incoming Telegram update
        context: Bot context
    """
    try:
        if not update.effective_user or not update.effective_message:
            return

        if not await is_admin(update.effective_user.id, bot=context.bot):
            await update.effective_message.reply_text(
                status_text("error", "This command is for admins only.")
            )
            return

        # Parse quoted arguments from the full message text
        text = update.effective_message.text or ""
        # Remove the /poll command prefix
        poll_text = text.split(None, 1)[1] if len(text.split(None, 1)) > 1 else ""

        if not poll_text:
            await update.effective_message.reply_text(
                '<b>Usage</b>\n'
                '<code>/poll "Question?" "Option 1" "Option 2" "Option 3"</code>\n\n'
                "Minimum 2 options, maximum 10.",
                parse_mode="HTML",
            )
            return

        # Extract quoted strings
        parts = re.findall(r'"([^"]+)"', poll_text)

        if len(parts) < 3:
            await update.effective_message.reply_text(
                "Please provide a question and at least 2 options, all in quotes.\n\n"
                '<code>/poll "Question?" "Option 1" "Option 2"</code>',
                parse_mode="HTML",
            )
            return

        if len(parts) > 11:
            await update.effective_message.reply_text(
                status_text("error", "Maximum 10 options allowed.")
            )
            return

        question = parts[0]
        options = parts[1:]

        # Send poll to community group
        poll_message_ids = []
        for chat_id in COMMUNITY_GROUP_IDS:
            poll_msg = await context.bot.send_poll(
                chat_id=chat_id,
                question=question,
                options=options,
                is_anonymous=False,
            )
            poll_message_ids.append({"chat_id": chat_id, "message_id": poll_msg.message_id})

        await update.effective_message.reply_text(
            title("Poll Created", "✅")
            + f"\n\n{field('Groups', len(poll_message_ids))}"
            + f"\n{field('Question', html.escape(question))}"
            + f"\n{field('Options', len(options))}",
            parse_mode="HTML",
        )

        # Log the poll
        await log_action(
            action="poll_created",
            admin_telegram_id=update.effective_user.id,
            metadata={
                "question": question,
                "options": options,
                "messages": poll_message_ids,
            },
        )

        logger.info(
            "Poll created by admin %s: %s",
            update.effective_user.id,
            question,
        )

    except Exception as exc:
        logger.error("Error in poll_command: %s", exc)
        await update.effective_message.reply_text(
            status_text("warning", "Something went wrong. Please try again.")
        )


# ---------------------------------------------------------------------------
# Leaderboard builder for scheduler
# ---------------------------------------------------------------------------


async def build_leaderboard_message() -> str:
    """
    Build a formatted leaderboard message for the weekly auto-post.

    Returns:
        HTML-formatted leaderboard string
    """
    top_users = await get_campaign_leaderboard(10)

    lines: list[str] = [title("Weekly XP Leaderboard", "🏅"), "", "Top community members this week:"]

    if not top_users:
        lines.append("")
        lines.append("No data yet. Complete raids, invite members, post about HostFi, or earn helpful contribution awards.")
    else:
        for i, user in enumerate(top_users):
            prefix = f"{i + 1}."
            name = user.get("first_name") or user.get("username") or "User"
            safe_name = html.escape(name)
            xp = user.get("xp", 0)
            lines.append(f"{prefix} <b>{safe_name}</b> — {xp:,} XP")

    lines.append("")
    lines.append(
        "Earn XP: raids, retained invites, HostFi X posts, and helpful contributions\n"
        "Trade on <b>HostFi</b>: https://hostfi.io"
    )

    return "\n".join(lines)
