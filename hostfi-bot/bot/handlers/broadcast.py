"""
Module: broadcast.py
Purpose: Broadcast, poll, XP rank/leaderboard, and referral deep-link handlers
Author: HOSTFI Bot Team
"""

import html
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions, Update
from telegram.ext import ContextTypes, ConversationHandler, filters

from bot.utils.keyboards import confirm_broadcast_keyboard
from bot.utils.permissions import is_admin
from bot.utils.rate_limiter import check_rate_limit, get_redis
from config import ADMIN_CHANNEL_ID, COMMUNITY_GROUP_ID, TELEGRAM_BOT_TOKEN
from database.logs import log_action
from database.referrals import create_referral, get_referral_count
from database.users import (
    add_xp,
    get_leaderboard,
    get_or_create_user,
    get_user_rank,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ConversationHandler states for broadcast flow
# ---------------------------------------------------------------------------

BROADCAST_CONTENT, BROADCAST_SCHEDULE = range(2)

# In-memory pending broadcasts keyed by broadcast_id
_pending_broadcasts: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# /start command with referral deep link
# ---------------------------------------------------------------------------


async def start_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle /start — welcome message and referral deep link processing.

    Deep link format: /start ref_<REFERRER_TELEGRAM_ID>
    When a user joins via a referral link, the referral is recorded
    and the referrer earns +5 XP.

    Args:
        update: Incoming Telegram update
        context: Bot context
    """
    try:
        if not update.effective_user or not update.effective_message:
            return

        user = update.effective_user
        await get_or_create_user(user.id, user.username, user.first_name)

        # Process referral deep link
        if context.args and context.args[0].startswith("ref_"):
            try:
                referrer_id = int(context.args[0][4:])
                if referrer_id != user.id:
                    referral = await create_referral(referrer_id, user.id)
                    if referral:
                        await add_xp(referrer_id, 5)
                        logger.info(
                            "Referral recorded: %s referred %s (+5 XP)",
                            referrer_id,
                            user.id,
                        )
                        # Notify referrer
                        try:
                            await context.bot.send_message(
                                chat_id=referrer_id,
                                text=(
                                    "🎉 <b>Referral Bonus!</b>\n\n"
                                    f"Your referral {html.escape(user.first_name or 'Someone')} "
                                    "just joined HostFi Bot!\n"
                                    "You earned <b>+5 XP</b>! 🏆"
                                ),
                                parse_mode="HTML",
                            )
                        except Exception:
                            pass  # Referrer may have blocked the bot
            except (ValueError, IndexError):
                pass  # Invalid referral link, ignore silently

        # Extract bot username from token for referral link
        bot_username = (await context.bot.get_me()).username

        await update.effective_message.reply_text(
            "🎉 <b>Welcome to the HOSTFI Bot!</b>\n\n"
            "I'm your all-in-one crypto community assistant.\n\n"
            "<b>What I can do:</b>\n"
            "• 💰 /price — Live crypto prices\n"
            "• 📊 /market — Market overview\n"
            "• 🤖 /ask — AI-powered support\n"
            "• 🎫 /support — Open a support ticket\n"
            "• 🏆 /rank — Check your XP rank\n"
            "• 🏅 /leaderboard — Top members\n\n"
            f"📣 <b>Share your referral link:</b>\n"
            f"<code>https://t.me/{bot_username}?start=ref_{user.id}</code>\n\n"
            "Earn <b>+5 XP</b> for every friend who joins! 🎁",
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
                "⏳ Too many requests. Please wait a moment."
            )
            return

        xp, rank, total = await get_user_rank(user_id)
        referrals = await get_referral_count(user_id)

        name = html.escape(
            update.effective_user.first_name or str(user_id)
        )

        # Rank badge based on XP
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

        # Extract bot username for referral link
        bot_username = (await context.bot.get_me()).username

        msg = (
            f"🏆 <b>{name}'s Profile</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"⭐ <b>XP:</b> {xp:,} points\n"
            f"📊 <b>Rank:</b> #{rank} of {total}\n"
            f"🏅 <b>Badge:</b> {badge}\n"
            f"👥 <b>Referrals:</b> {referrals}\n\n"
            f"<b>How to earn XP:</b>\n"
            f"• 💬 +1 XP — Send a message in the group\n"
            f"• 👥 +5 XP — Refer a new member\n"
            f"• ⭐ +10 XP — Get a 5-star ticket rating\n\n"
            f"📣 <b>Your referral link:</b>\n"
            f"<code>https://t.me/{bot_username}?start=ref_{user_id}</code>"
        )

        await update.effective_message.reply_text(msg, parse_mode="HTML")

    except Exception as exc:
        logger.error("Error in rank_command: %s", exc)
        await update.effective_message.reply_text(
            "⚠️ Something went wrong. Please try again later."
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
                "⏳ Too many requests. Please wait a moment."
            )
            return

        top_users = await get_leaderboard(10)

        if not top_users:
            await update.effective_message.reply_text(
                "📊 No leaderboard data yet. Start chatting to earn XP!"
            )
            return

        medals = ["🥇", "🥈", "🥉"]
        lines: list[str] = ["🏅 <b>XP Leaderboard — Top 10</b>\n━━━━━━━━━━━━━━━━━━"]

        for i, user in enumerate(top_users):
            prefix = medals[i] if i < 3 else f"  {i + 1}."
            name = user.get("first_name") or user.get("username") or "User"
            safe_name = html.escape(name)
            xp = user.get("xp_points", 0)
            lines.append(f"\n{prefix} <b>{safe_name}</b> — {xp:,} XP")

        lines.append("\n━━━━━━━━━━━━━━━━━━")
        lines.append("💬 Earn XP by chatting, referring friends, and helping others!")

        await update.effective_message.reply_text(
            "\n".join(lines), parse_mode="HTML"
        )

    except Exception as exc:
        logger.error("Error in leaderboard_command: %s", exc)
        await update.effective_message.reply_text(
            "⚠️ Something went wrong. Please try again later."
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

        if not await is_admin(update.effective_user.id, bot=context.bot):
            await update.effective_message.reply_text(
                "⛔ This command is for admins only."
            )
            return ConversationHandler.END

        await update.effective_message.reply_text(
            "📢 <b>Broadcast Composer</b>\n\n"
            "Send your announcement message now.\n"
            "Supported: <b>text, photo, or video</b> with optional caption.\n\n"
            "Send /cancel to abort.",
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
                "❌ Unsupported content type. Please send text, photo, or video."
            )
            return ConversationHandler.END

        _pending_broadcasts[broadcast_id] = broadcast_data

        # Show preview
        preview_header = (
            "📢 <b>Broadcast Preview</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
        )

        if broadcast_data["type"] == "text":
            preview = preview_header + (broadcast_data["text"] or "")
            await msg.reply_text(
                preview + "\n\n━━━━━━━━━━━━━━━━━━\n"
                "✅ Confirm to send to the community group.",
                parse_mode="HTML",
                reply_markup=confirm_broadcast_keyboard(broadcast_id),
            )
        elif broadcast_data["type"] == "photo":
            await msg.reply_photo(
                photo=broadcast_data["photo_id"],
                caption=(
                    preview_header
                    + (broadcast_data["caption"] or "")
                    + "\n\n━━━━━━━━━━━━━━━━━━\n"
                    "✅ Confirm to send to the community group."
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
                    + "\n\n━━━━━━━━━━━━━━━━━━\n"
                    "✅ Confirm to send to the community group."
                ),
                parse_mode="HTML",
                reply_markup=confirm_broadcast_keyboard(broadcast_id),
            )

        return ConversationHandler.END

    except Exception as exc:
        logger.error("Error in broadcast_receive_content: %s", exc)
        await update.effective_message.reply_text(
            "⚠️ Something went wrong. Please try /broadcast again."
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
        await query.answer("⛔ Admins only.", show_alert=True)
        return

    parts = query.data.split("_")
    # Expected: ["broadcast", "confirm"|"cancel", "<id>"]
    if len(parts) != 3:
        return

    action = parts[1]
    broadcast_id = parts[2]

    broadcast_data = _pending_broadcasts.pop(broadcast_id, None)

    if broadcast_data is None:
        await query.answer("❌ Broadcast expired or not found.", show_alert=True)
        return

    if action == "cancel":
        await query.answer("Broadcast cancelled.")
        await query.edit_message_text("❌ Broadcast cancelled.")
        logger.info(
            "Broadcast %s cancelled by admin %s",
            broadcast_id,
            query.from_user.id,
        )
        return

    # --- Send broadcast to community group ---
    await query.answer("📨 Sending broadcast...")

    try:
        btype = broadcast_data["type"]

        if btype == "text":
            await context.bot.send_message(
                chat_id=COMMUNITY_GROUP_ID,
                text=broadcast_data["text"],
                parse_mode="HTML",
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
        elif btype == "photo":
            await context.bot.send_photo(
                chat_id=COMMUNITY_GROUP_ID,
                photo=broadcast_data["photo_id"],
                caption=broadcast_data["caption"],
                parse_mode="HTML",
            )
        elif btype == "video":
            await context.bot.send_video(
                chat_id=COMMUNITY_GROUP_ID,
                video=broadcast_data["video_id"],
                caption=broadcast_data["caption"],
                parse_mode="HTML",
            )

        await query.edit_message_text(
            f"✅ Broadcast sent to the community group!"
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
            f"⚠️ Broadcast failed: {html.escape(str(exc))}"
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
        await update.effective_message.reply_text("❌ Broadcast cancelled.")
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
                "⛔ This command is for admins only."
            )
            return

        # Parse quoted arguments from the full message text
        text = update.effective_message.text or ""
        # Remove the /poll command prefix
        poll_text = text.split(None, 1)[1] if len(text.split(None, 1)) > 1 else ""

        if not poll_text:
            await update.effective_message.reply_text(
                'ℹ️ <b>Usage:</b>\n'
                '<code>/poll "Question?" "Option 1" "Option 2" "Option 3"</code>\n\n'
                "Minimum 2 options, maximum 10.",
                parse_mode="HTML",
            )
            return

        # Extract quoted strings
        parts = re.findall(r'"([^"]+)"', poll_text)

        if len(parts) < 3:
            await update.effective_message.reply_text(
                "❌ Please provide a question and at least 2 options, all in quotes.\n\n"
                '<code>/poll "Question?" "Option 1" "Option 2"</code>',
                parse_mode="HTML",
            )
            return

        if len(parts) > 11:
            await update.effective_message.reply_text(
                "❌ Maximum 10 options allowed."
            )
            return

        question = parts[0]
        options = parts[1:]

        # Send poll to community group
        poll_msg = await context.bot.send_poll(
            chat_id=COMMUNITY_GROUP_ID,
            question=question,
            options=options,
            is_anonymous=False,
        )

        await update.effective_message.reply_text(
            f"✅ Poll created in the community group!\n\n"
            f"📊 <b>{html.escape(question)}</b>\n"
            f"Options: {len(options)}",
            parse_mode="HTML",
        )

        # Log the poll
        await log_action(
            action="poll_created",
            admin_telegram_id=update.effective_user.id,
            metadata={
                "question": question,
                "options": options,
                "message_id": poll_msg.message_id,
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
            "⚠️ Something went wrong. Please try again."
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
    top_users = await get_leaderboard(10)

    lines: list[str] = [
        "🏅 <b>Weekly XP Leaderboard</b>",
        "━━━━━━━━━━━━━━━━━━",
        "",
        "Top community members this week:",
    ]

    if not top_users:
        lines.append("\nNo data yet — start chatting to earn XP!")
    else:
        medals = ["🥇", "🥈", "🥉"]
        for i, user in enumerate(top_users):
            prefix = medals[i] if i < 3 else f"  {i + 1}."
            name = user.get("first_name") or user.get("username") or "User"
            safe_name = html.escape(name)
            xp = user.get("xp_points", 0)
            lines.append(f"\n{prefix} <b>{safe_name}</b> — {xp:,} XP")

    lines.append("\n━━━━━━━━━━━━━━━━━━")
    lines.append(
        "💬 Earn XP: chat (+1), refer friends (+5), 5⭐ tickets (+10)\n"
        "📲 Trade on <b>HostFi</b> — https://hostfi.io"
    )

    return "\n".join(lines)
