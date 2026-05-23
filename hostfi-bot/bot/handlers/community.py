"""
Module: community.py
Purpose: New member welcome + verification gate, left member handler, flood control
Author: HOSTFI Bot Team
"""

import html
import logging
import random
from datetime import datetime, timedelta, timezone

from telegram import ChatPermissions, Update
from telegram.ext import ContextTypes

from bot.filters.scam_filter import run_scam_checks
from bot.filters.spam_filter import run_spam_checks
from bot.utils.auto_delete import schedule_delete
from bot.utils.formatter import (
    format_flood_mute,
    format_rules,
    format_verification_prompt,
    format_welcome,
)
from bot.utils.keyboards import (
    campaign_home_keyboard,
    generate_captcha_options,
    verification_keyboard,
    welcome_keyboard,
)
from bot.utils.permissions import get_admin_ids, is_admin
from bot.utils.rate_limiter import check_rate_limit, get_redis
from config import ADMIN_CHANNEL_ID, MAX_MESSAGES_PER_MINUTE
from database.logs import log_action
from database.users import get_or_create_user, is_user_verified, verify_user

logger = logging.getLogger(__name__)

# In-memory fallback when Redis is not configured
_captcha_store: dict[str, str] = {}

# ---------------------------------------------------------------------------
# Shared permission sets
# ---------------------------------------------------------------------------

# Fully restricted — new / unverified members
RESTRICTED_PERMISSIONS = ChatPermissions(
    can_send_messages=False,
    can_send_audios=False,
    can_send_documents=False,
    can_send_photos=False,
    can_send_videos=False,
    can_send_video_notes=False,
    can_send_voice_notes=False,
    can_send_polls=False,
    can_send_other_messages=False,
    can_add_web_page_previews=False,
    can_change_info=False,
    can_invite_users=False,
    can_pin_messages=False,
)

# Standard verified member
MEMBER_PERMISSIONS = ChatPermissions(
    can_send_messages=True,
    can_send_audios=True,
    can_send_documents=True,
    can_send_photos=True,
    can_send_videos=True,
    can_send_video_notes=True,
    can_send_voice_notes=True,
    can_send_polls=True,
    can_send_other_messages=True,
    can_add_web_page_previews=True,
    can_invite_users=True,
)


# ---------------------------------------------------------------------------
# New member handler
# ---------------------------------------------------------------------------


async def new_member_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle new members joining the group.

    For each non-bot member:
    1. Register in the database
    2. Restrict messaging permissions
    3. Send a welcome message with an inline-keyboard math CAPTCHA
    4. Schedule a 5-minute verification timeout

    Args:
        update: Incoming Telegram update
        context: Bot context
    """
    if not update.message or not update.message.new_chat_members:
        return

    chat_id = update.effective_chat.id
    from bot.handlers.campaign import record_new_member_invite

    await record_new_member_invite(update)

    for member in update.message.new_chat_members:
        if member.is_bot:
            continue

        try:
            # Register user in database
            await get_or_create_user(
                member.id, member.username, member.first_name
            )

            # Restrict until verified
            await context.bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=member.id,
                permissions=RESTRICTED_PERMISSIONS,
            )

            # Generate math CAPTCHA
            num1 = random.randint(1, 9)
            num2 = random.randint(1, 9)
            correct = num1 + num2
            options = generate_captcha_options(correct)

            # Store correct answer in Redis (5-minute TTL)
            redis = get_redis()
            captcha_key = f"captcha:{member.id}:{chat_id}"
            if redis is not None:
                await redis.set(captcha_key, str(correct), ex=300)
            else:
                _captcha_store[captcha_key] = str(correct)

            # Build and send welcome + CAPTCHA
            name = member.first_name or "friend"
            welcome_text = format_welcome(name)
            captcha_text = format_verification_prompt(num1, num2)
            keyboard = verification_keyboard(member.id, correct, options)

            msg = await update.message.reply_text(
                welcome_text + captcha_text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )

            # Schedule verification timeout (5 minutes)
            context.job_queue.run_once(
                _verification_timeout,
                when=300,
                data={
                    "user_id": member.id,
                    "chat_id": chat_id,
                    "message_id": msg.message_id,
                },
                name=f"verify_timeout_{member.id}_{chat_id}",
            )

            logger.info(
                "New member %s (%s) — CAPTCHA sent in chat %s",
                member.id,
                member.first_name,
                chat_id,
            )

        except Exception as exc:
            logger.error(
                "Error handling new member %s: %s", member.id, exc
            )


# ---------------------------------------------------------------------------
# Verification callback (CAPTCHA answer)
# ---------------------------------------------------------------------------


async def verification_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle inline keyboard CAPTCHA answer clicks.

    Verifies the selected answer against the stored correct answer in
    Redis.  On success the member is unrestricted and marked verified.
    On failure the user is prompted to try again.

    Callback data format: ``captcha_{user_id}_{selected_answer}``

    Args:
        update: Incoming callback query update
        context: Bot context
    """
    query = update.callback_query
    if not query or not query.data:
        return

    data_parts = query.data.split("_")
    # Expected: ["captcha", "<user_id>", "<selected_answer>"]
    if len(data_parts) != 3 or data_parts[0] != "captcha":
        return

    try:
        target_user_id = int(data_parts[1])
        selected_answer = int(data_parts[2])
    except ValueError:
        await query.answer("Invalid data.", show_alert=True)
        return

    # Only the target user may answer their own CAPTCHA
    if query.from_user.id != target_user_id:
        await query.answer(
            "⛔ This verification is not for you!", show_alert=True
        )
        return

    chat_id = query.message.chat.id
    redis = get_redis()
    captcha_key = f"captcha:{target_user_id}:{chat_id}"
    if redis is not None:
        correct_raw = await redis.get(captcha_key)
    else:
        correct_raw = _captcha_store.get(captcha_key)

    if correct_raw is None:
        await query.answer(
            "⏰ Verification expired. Please leave and rejoin the group.",
            show_alert=True,
        )
        return

    correct_answer = int(correct_raw)

    if selected_answer == correct_answer:
        # ✅ Correct — unrestrict, verify, clean up
        try:
            await context.bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=target_user_id,
                permissions=MEMBER_PERMISSIONS,
            )
            await verify_user(target_user_id)
            if redis is not None:
                await redis.delete(captcha_key)
            else:
                _captcha_store.pop(captcha_key, None)

            # Cancel the timeout job
            jobs = context.job_queue.get_jobs_by_name(
                f"verify_timeout_{target_user_id}_{chat_id}"
            )
            for job in jobs:
                job.schedule_removal()

            name = query.from_user.first_name or "friend"
            await query.edit_message_text(
                format_welcome(name)
                + "\n\n✅ <b>Verified!</b> Welcome to the community!",
                parse_mode="HTML",
                reply_markup=welcome_keyboard(),
            )
            await query.answer("✅ Verified! Welcome!")

            logger.info("User %s verified successfully", target_user_id)

        except Exception as exc:
            logger.error(
                "Error verifying user %s: %s", target_user_id, exc
            )
            await query.answer(
                "An error occurred. Please try again.", show_alert=True
            )
    else:
        # ❌ Wrong answer
        await query.answer(
            "❌ Wrong answer — try again!", show_alert=True
        )
        logger.info(
            "User %s failed CAPTCHA (selected=%s correct=%s)",
            target_user_id,
            selected_answer,
            correct_answer,
        )


# ---------------------------------------------------------------------------
# Verification timeout job
# ---------------------------------------------------------------------------


async def _verification_timeout(
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Kick a user who failed to verify within the 5-minute timeout.

    Uses ban → immediate unban so the user is removed but may rejoin
    via an invite link.

    Args:
        context: Job context with user_id, chat_id, message_id in data
    """
    data = context.job.data
    user_id: int = data["user_id"]
    chat_id: int = data["chat_id"]
    message_id: int = data["message_id"]

    redis = get_redis()
    captcha_key = f"captcha:{user_id}:{chat_id}"
    if redis is not None:
        still_pending = await redis.get(captcha_key)
    else:
        still_pending = _captcha_store.get(captcha_key)

    if still_pending is not None:
        try:
            # Ban + immediate unban = kick (user can rejoin via link)
            await context.bot.ban_chat_member(chat_id, user_id)
            await context.bot.unban_chat_member(chat_id, user_id)
            if redis is not None:
                await redis.delete(captcha_key)
            else:
                _captcha_store.pop(captcha_key, None)

            # Edit the original CAPTCHA message
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=(
                        "⏰ <b>Verification timed out.</b>\n\n"
                        "The user was removed. They may rejoin and try again."
                    ),
                    parse_mode="HTML",
                )
            except Exception:
                pass  # Message may have been deleted already

            logger.info(
                "User %s kicked for verification timeout in chat %s",
                user_id,
                chat_id,
            )

        except Exception as exc:
            logger.error(
                "Failed to kick unverified user %s: %s", user_id, exc
            )


# ---------------------------------------------------------------------------
# Left member handler
# ---------------------------------------------------------------------------


async def left_member_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle a member leaving the group.

    Deletes the Telegram service message to reduce chat clutter.

    Args:
        update: Incoming Telegram update
        context: Bot context
    """
    if not update.message or not update.message.left_chat_member:
        return

    try:
        await update.message.delete()
        logger.info(
            "Deleted leave message for user %s",
            update.message.left_chat_member.id,
        )
    except Exception as exc:
        logger.warning("Could not delete leave message: %s", exc)


# ---------------------------------------------------------------------------
# Group message filter (spam + scam + flood control)
# ---------------------------------------------------------------------------


async def group_message_filter(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Filter every message in the community group through spam, scam,
    and flood-control checks.

    Processing order:
    1. Skip admins.
    2. Flood control — mute for 5 min if >MAX_MESSAGES_PER_MINUTE.
    3. Scam check — delete message, alert admin channel.
    4. Spam check — delete message silently.

    Args:
        update: Incoming Telegram update
        context: Bot context
    """
    if not update.message:
        return

    user = update.effective_user
    if not user:
        return

    # Admins bypass all message filters
    if await is_admin(user.id, bot=context.bot):
        return

    chat_id = update.effective_chat.id
    text = update.message.text or update.message.caption or ""

    # --- Flood control -------------------------------------------------------
    allowed = await check_rate_limit(
        user.id,
        action="flood",
        limit=MAX_MESSAGES_PER_MINUTE,
        window=60,
    )
    if not allowed:
        try:
            until_date = datetime.now(timezone.utc) + timedelta(minutes=5)
            await context.bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=user.id,
                permissions=RESTRICTED_PERMISSIONS,
                until_date=until_date,
            )
            name = user.first_name or user.username or str(user.id)
            msg = await update.message.reply_text(
                format_flood_mute(name), parse_mode="HTML"
            )
            await schedule_delete(msg, context, 15)
            await log_action(
                action="flood_mute",
                admin_telegram_id=0,
                target_telegram_id=user.id,
                reason="Exceeded message rate limit",
                metadata={"limit": MAX_MESSAGES_PER_MINUTE},
            )
            logger.info("Flood-muted user %s in chat %s", user.id, chat_id)
        except Exception as exc:
            logger.error("Flood mute failed for %s: %s", user.id, exc)
        return

    # Skip further text-based checks if there is no text content
    if not text:
        return

    # --- Scam check ----------------------------------------------------------
    scam_result = await run_scam_checks(
        text=text,
        username=user.username,
        display_name=user.first_name,
        admin_ids=get_admin_ids(),
        user_id=user.id,
    )
    if scam_result.is_scam:
        try:
            await update.message.delete()
            alert = (
                f"🚨 <b>Scam Detected</b>\n\n"
                f"👤 User: {html.escape(user.first_name or str(user.id))}\n"
                f"🆔 ID: <code>{user.id}</code>\n"
                f"📝 Reason: {html.escape(scam_result.reason)}\n"
                f"⚠️ Severity: {scam_result.severity}"
            )
            await context.bot.send_message(
                chat_id=ADMIN_CHANNEL_ID,
                text=alert,
                parse_mode="HTML",
            )
            await log_action(
                action="scam_blocked",
                admin_telegram_id=0,
                target_telegram_id=user.id,
                reason=scam_result.reason,
                metadata={"severity": scam_result.severity},
            )
            logger.warning(
                "Scam message deleted from user %s: %s",
                user.id,
                scam_result.reason,
            )
        except Exception as exc:
            logger.error(
                "Failed to handle scam message from %s: %s", user.id, exc
            )
        return

    # --- Spam check ----------------------------------------------------------
    verified = await is_user_verified(user.id)
    spam_result = await run_spam_checks(user.id, text, verified)
    if spam_result.is_spam:
        try:
            await update.message.delete()
            await log_action(
                action="spam_blocked",
                admin_telegram_id=0,
                target_telegram_id=user.id,
                reason=spam_result.reason,
            )
            logger.info(
                "Spam message deleted from user %s: %s",
                user.id,
                spam_result.reason,
            )
        except Exception as exc:
            logger.error(
                "Failed to delete spam from %s: %s", user.id, exc
            )
        return

    # General chatting intentionally earns no campaign XP.


# ---------------------------------------------------------------------------
# Rules callback (from welcome keyboard)
# ---------------------------------------------------------------------------


async def rules_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Show community rules when the user taps the Rules button.

    Args:
        update: Incoming callback query update
        context: Bot context
    """
    query = update.callback_query
    if not query:
        return

    await query.answer()
    await query.message.reply_text(
        format_rules(),
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# Help callback (from welcome keyboard)
# ---------------------------------------------------------------------------


async def help_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Show the help command list when the user taps the Help button.

    Args:
        update: Incoming callback query update
        context: Bot context
    """
    query = update.callback_query
    if not query:
        return

    await query.answer()
    help_text = (
        "📚 <b>HOSTFI Bot Commands</b>\n\n"
        "/help — Show this help menu\n"
        "/rules — Community rules\n"
        "/ask [question] — Ask the AI assistant\n"
        "/support — Open a support ticket\n"
        "/campaign — Open XP campaign panel\n"
        "/rank — Your XP and rank\n"
        "/leaderboard — Top 10 members\n"
    )
    await query.message.reply_text(
        help_text,
        parse_mode="HTML",
        reply_markup=campaign_home_keyboard(),
    )


# ---------------------------------------------------------------------------
# /help command
# ---------------------------------------------------------------------------

HELP_TEXT = (
    "📚 <b>HOSTFI Bot Commands</b>\n\n"
    "<b>General:</b>\n"
    "/help — Show this help menu\n"
    "/rules — Community rules\n\n"
    "<b>AI &amp; Support:</b>\n"
    "/ask [question] — Ask the AI assistant\n"
    "/support — Open a support ticket\n\n"
    "<b>Community:</b>\n"
    "/campaign — Open XP campaign panel\n"
    "/rank — Your XP and rank\n"
    "/leaderboard — Top 10 members\n"
)


async def help_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /help — display available commands."""
    if not update.effective_message:
        return
    msg = await update.effective_message.reply_text(
        HELP_TEXT,
        parse_mode="HTML",
        reply_markup=campaign_home_keyboard(),
    )
    await schedule_delete(msg, context, 30)
