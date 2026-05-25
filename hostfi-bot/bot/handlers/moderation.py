"""
Module: moderation.py
Purpose: Admin moderation commands — /warn, /mute, /unmute, /ban, /unban, /kick,
         /pin, /rules, /announce with full audit logging
Author: HOSTFI Bot Team
"""

import html
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from telegram import ChatPermissions, Update, User
from telegram.ext import ContextTypes

from bot.utils.formatter import (
    format_ban,
    format_kick,
    format_mute,
    format_rules,
    format_unban,
    format_unmute,
    format_warn,
    status_text,
    title,
)
from bot.utils.auto_delete import schedule_delete
from bot.utils.permissions import is_admin
from bot.utils.rate_limiter import check_rate_limit
from config import COMMUNITY_GROUP_IDS
from database.logs import log_action
from database.users import (
    ban_user,
    get_or_create_user,
    increment_warns,
    unban_user,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared permission sets
# ---------------------------------------------------------------------------

MUTED_PERMISSIONS = ChatPermissions(
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

UNMUTED_PERMISSIONS = ChatPermissions(
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
# Helpers
# ---------------------------------------------------------------------------

DURATION_PATTERN = re.compile(r"^(\d+)([mhd])$", re.IGNORECASE)
DURATION_LABELS = {"m": "minutes", "h": "hours", "d": "days"}
DURATION_MULTIPLIERS = {"m": 60, "h": 3600, "d": 86400}


def _parse_duration(raw: str) -> tuple[int, str] | None:
    """
    Parse a human-friendly duration string into seconds + label.

    Accepted formats: 30m, 1h, 2d.

    Args:
        raw: Duration string

    Returns:
        Tuple of (seconds, human label) or None if invalid
    """
    match = DURATION_PATTERN.match(raw.strip())
    if not match:
        return None
    value = int(match.group(1))
    unit = match.group(2).lower()
    seconds = value * DURATION_MULTIPLIERS[unit]
    label = f"{value} {DURATION_LABELS[unit]}"
    return seconds, label


async def _extract_target_and_reason(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> tuple[User, str] | None:
    """
    Extract the target user and reason from a moderation command.

    Supports two patterns:
    1. Reply to a user's message — ``/warn reason text``
    2. Explicit user ID — ``/warn 123456789 reason text``

    Args:
        update: Incoming Telegram update
        context: Bot context

    Returns:
        Tuple of (User, reason_str) or None if target cannot be resolved
    """
    # Pattern 1: Reply to message
    if (
        update.message.reply_to_message
        and update.message.reply_to_message.from_user
    ):
        target = update.message.reply_to_message.from_user
        reason = (
            " ".join(context.args) if context.args else "No reason provided"
        )
        return target, reason

    # Pattern 2: User ID as first argument
    if context.args and context.args[0].isdigit():
        user_id = int(context.args[0])
        reason = (
            " ".join(context.args[1:])
            if len(context.args) > 1
            else "No reason provided"
        )
        try:
            chat_member = await context.bot.get_chat_member(
                update.effective_chat.id, user_id
            )
            return chat_member.user, reason
        except Exception as exc:
            logger.error("Could not find user %s: %s", user_id, exc)
            return None

    return None


def _display_name(user: User) -> str:
    """
    Return the best available display name for a Telegram user.

    Args:
        user: Telegram User object

    Returns:
        Username, first name, or stringified user ID
    """
    return user.username or user.first_name or str(user.id)


# ---------------------------------------------------------------------------
# /warn
# ---------------------------------------------------------------------------


async def warn_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Issue a warning to a user. Three warnings trigger an automatic ban.

    Usage:
        Reply to a message with ``/warn [reason]``
        or ``/warn <user_id> [reason]``

    Args:
        update: Incoming Telegram update
        context: Bot context
    """
    if not await is_admin(update.effective_user.id, bot=context.bot):
        await update.message.reply_text(
            status_text("error", "You don't have permission to use this command.")
        )
        return

    if not await check_rate_limit(
        update.effective_user.id, "admin_cmd", 30, 60
    ):
        await update.message.reply_text(status_text("warning", "Please slow down."))
        return

    result = await _extract_target_and_reason(update, context)
    if result is None:
        await update.message.reply_text(
            "Usage: Reply to a message with <code>/warn [reason]</code>\n"
            "or <code>/warn &lt;user_id&gt; [reason]</code>",
            parse_mode="HTML",
        )
        return

    target, reason = result

    if await is_admin(target.id, bot=context.bot):
        await update.message.reply_text(status_text("error", "Cannot warn an admin."))
        return

    try:
        # Ensure user exists in DB
        await get_or_create_user(
            target.id, target.username, target.first_name
        )
        warn_count = await increment_warns(target.id)

        # Log the warning
        await log_action(
            action="warn",
            admin_telegram_id=update.effective_user.id,
            target_telegram_id=target.id,
            reason=reason,
            metadata={"warn_count": warn_count},
        )

        # Auto-ban at 3 warnings
        if warn_count >= 3:
            await context.bot.ban_chat_member(
                update.effective_chat.id, target.id
            )
            await ban_user(target.id)
            await log_action(
                action="auto_ban",
                admin_telegram_id=update.effective_user.id,
                target_telegram_id=target.id,
                reason=f"Auto-ban: reached {warn_count} warnings",
            )
            name = _display_name(target)
            msg = await update.message.reply_text(
                format_ban(
                    name, f"Auto-ban: {warn_count} warnings reached"
                ),
                parse_mode="HTML",
            )
            await schedule_delete(msg, context, 15)
            logger.info(
                "User %s auto-banned after %s warnings",
                target.id,
                warn_count,
            )
        else:
            name = _display_name(target)
            msg = await update.message.reply_text(
                format_warn(name, reason, warn_count),
                parse_mode="HTML",
            )
            await schedule_delete(msg, context, 15)
            logger.info(
                "User %s warned (%s/3) by admin %s",
                target.id,
                warn_count,
                update.effective_user.id,
            )

    except Exception as exc:
        logger.error("Warn command failed: %s", exc)
        await update.message.reply_text(
            status_text("error", "An error occurred. Please try again.")
        )


# ---------------------------------------------------------------------------
# /mute
# ---------------------------------------------------------------------------


async def mute_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Temporarily mute a user in the group.

    Usage:
        Reply with ``/mute <duration> [reason]``
        or ``/mute <user_id> <duration> [reason]``

    Duration format: ``30m``, ``1h``, ``2d``

    Args:
        update: Incoming Telegram update
        context: Bot context
    """
    if not await is_admin(update.effective_user.id, bot=context.bot):
        await update.message.reply_text(
            status_text("error", "You don't have permission to use this command.")
        )
        return

    if not await check_rate_limit(
        update.effective_user.id, "admin_cmd", 30, 60
    ):
        await update.message.reply_text(status_text("warning", "Please slow down."))
        return

    # Parse arguments — two possible patterns
    target: User | None = None
    duration_str: str | None = None
    reason: str = "No reason provided"

    if (
        update.message.reply_to_message
        and update.message.reply_to_message.from_user
    ):
        target = update.message.reply_to_message.from_user
        if context.args:
            duration_str = context.args[0]
            reason = (
                " ".join(context.args[1:])
                if len(context.args) > 1
                else reason
            )
    elif context.args and len(context.args) >= 2 and context.args[0].isdigit():
        user_id = int(context.args[0])
        duration_str = context.args[1]
        reason = (
            " ".join(context.args[2:])
            if len(context.args) > 2
            else reason
        )
        try:
            chat_member = await context.bot.get_chat_member(
                update.effective_chat.id, user_id
            )
            target = chat_member.user
        except Exception as exc:
            logger.error("Could not find user %s: %s", user_id, exc)

    if target is None or duration_str is None:
        await update.message.reply_text(
            "Usage: Reply to a message with "
            "<code>/mute &lt;duration&gt; [reason]</code>\n"
            "or <code>/mute &lt;user_id&gt; &lt;duration&gt; [reason]</code>"
            "\n\nDuration examples: <code>30m</code>, <code>1h</code>, "
            "<code>2d</code>",
            parse_mode="HTML",
        )
        return

    if await is_admin(target.id, bot=context.bot):
        await update.message.reply_text(status_text("error", "Cannot mute an admin."))
        return

    parsed = _parse_duration(duration_str)
    if parsed is None:
        await update.message.reply_text(
            "Invalid duration. Use format: "
            "<code>30m</code>, <code>1h</code>, <code>2d</code>",
            parse_mode="HTML",
        )
        return

    seconds, label = parsed

    try:
        until_date = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        await context.bot.restrict_chat_member(
            chat_id=update.effective_chat.id,
            user_id=target.id,
            permissions=MUTED_PERMISSIONS,
            until_date=until_date,
        )

        await log_action(
            action="mute",
            admin_telegram_id=update.effective_user.id,
            target_telegram_id=target.id,
            reason=reason,
            metadata={"duration": label, "seconds": seconds},
        )

        name = _display_name(target)
        msg = await update.message.reply_text(
            format_mute(name, label, reason),
            parse_mode="HTML",
        )
        await schedule_delete(msg, context, 15)
        logger.info(
            "User %s muted for %s by admin %s",
            target.id,
            label,
            update.effective_user.id,
        )

    except Exception as exc:
        logger.error("Mute command failed: %s", exc)
        await update.message.reply_text(
            status_text("error", "An error occurred. Please try again.")
        )


# ---------------------------------------------------------------------------
# /unmute
# ---------------------------------------------------------------------------


async def unmute_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Remove mute from a user, restoring standard messaging permissions.

    Usage:
        Reply with ``/unmute``
        or ``/unmute <user_id>``

    Args:
        update: Incoming Telegram update
        context: Bot context
    """
    if not await is_admin(update.effective_user.id, bot=context.bot):
        await update.message.reply_text(
            status_text("error", "You don't have permission to use this command.")
        )
        return

    result = await _extract_target_and_reason(update, context)
    if result is None:
        await update.message.reply_text(
            "Usage: Reply to a message with <code>/unmute</code>\n"
            "or <code>/unmute &lt;user_id&gt;</code>",
            parse_mode="HTML",
        )
        return

    target, _ = result

    try:
        await context.bot.restrict_chat_member(
            chat_id=update.effective_chat.id,
            user_id=target.id,
            permissions=UNMUTED_PERMISSIONS,
        )

        await log_action(
            action="unmute",
            admin_telegram_id=update.effective_user.id,
            target_telegram_id=target.id,
            reason="Manual unmute",
        )

        name = _display_name(target)
        msg = await update.message.reply_text(
            format_unmute(name),
            parse_mode="HTML",
        )
        await schedule_delete(msg, context, 15)
        logger.info(
            "User %s unmuted by admin %s",
            target.id,
            update.effective_user.id,
        )

    except Exception as exc:
        logger.error("Unmute command failed: %s", exc)
        await update.message.reply_text(
            status_text("error", "An error occurred. Please try again.")
        )


# ---------------------------------------------------------------------------
# /ban
# ---------------------------------------------------------------------------


async def ban_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Permanently ban a user from the group.

    Usage:
        Reply with ``/ban [reason]``
        or ``/ban <user_id> [reason]``

    Args:
        update: Incoming Telegram update
        context: Bot context
    """
    if not await is_admin(update.effective_user.id, bot=context.bot):
        await update.message.reply_text(
            status_text("error", "You don't have permission to use this command.")
        )
        return

    if not await check_rate_limit(
        update.effective_user.id, "admin_cmd", 30, 60
    ):
        await update.message.reply_text(status_text("warning", "Please slow down."))
        return

    result = await _extract_target_and_reason(update, context)
    if result is None:
        await update.message.reply_text(
            "Usage: Reply to a message with <code>/ban [reason]</code>\n"
            "or <code>/ban &lt;user_id&gt; [reason]</code>",
            parse_mode="HTML",
        )
        return

    target, reason = result

    if await is_admin(target.id, bot=context.bot):
        await update.message.reply_text(status_text("error", "Cannot ban an admin."))
        return

    try:
        await context.bot.ban_chat_member(
            update.effective_chat.id, target.id
        )
        await get_or_create_user(
            target.id, target.username, target.first_name
        )
        await ban_user(target.id)

        await log_action(
            action="ban",
            admin_telegram_id=update.effective_user.id,
            target_telegram_id=target.id,
            reason=reason,
        )

        name = _display_name(target)
        msg = await update.message.reply_text(
            format_ban(name, reason),
            parse_mode="HTML",
        )
        await schedule_delete(msg, context, 15)
        logger.info(
            "User %s banned by admin %s: %s",
            target.id,
            update.effective_user.id,
            reason,
        )

    except Exception as exc:
        logger.error("Ban command failed: %s", exc)
        await update.message.reply_text(
            status_text("error", "An error occurred. Please try again.")
        )


# ---------------------------------------------------------------------------
# /unban
# ---------------------------------------------------------------------------


async def unban_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Remove a ban, allowing the user to rejoin via invite link.

    Usage:
        ``/unban <user_id>``

    Args:
        update: Incoming Telegram update
        context: Bot context
    """
    if not await is_admin(update.effective_user.id, bot=context.bot):
        await update.message.reply_text(
            status_text("error", "You don't have permission to use this command.")
        )
        return

    result = await _extract_target_and_reason(update, context)
    if result is None:
        await update.message.reply_text(
            "Usage: <code>/unban &lt;user_id&gt;</code>",
            parse_mode="HTML",
        )
        return

    target, _ = result

    try:
        await context.bot.unban_chat_member(
            update.effective_chat.id, target.id, only_if_banned=True
        )
        await unban_user(target.id)

        await log_action(
            action="unban",
            admin_telegram_id=update.effective_user.id,
            target_telegram_id=target.id,
            reason="Manual unban",
        )

        name = _display_name(target)
        msg = await update.message.reply_text(
            format_unban(name),
            parse_mode="HTML",
        )
        await schedule_delete(msg, context, 15)
        logger.info(
            "User %s unbanned by admin %s",
            target.id,
            update.effective_user.id,
        )

    except Exception as exc:
        logger.error("Unban command failed: %s", exc)
        await update.message.reply_text(
            status_text("error", "An error occurred. Please try again.")
        )


# ---------------------------------------------------------------------------
# /kick
# ---------------------------------------------------------------------------


async def kick_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Kick a user from the group (they may rejoin via invite link).

    Uses ban → immediate unban to remove the user without a permanent ban.

    Usage:
        Reply with ``/kick [reason]``
        or ``/kick <user_id> [reason]``

    Args:
        update: Incoming Telegram update
        context: Bot context
    """
    if not await is_admin(update.effective_user.id, bot=context.bot):
        await update.message.reply_text(
            status_text("error", "You don't have permission to use this command.")
        )
        return

    if not await check_rate_limit(
        update.effective_user.id, "admin_cmd", 30, 60
    ):
        await update.message.reply_text(status_text("warning", "Please slow down."))
        return

    result = await _extract_target_and_reason(update, context)
    if result is None:
        await update.message.reply_text(
            "Usage: Reply to a message with <code>/kick [reason]</code>\n"
            "or <code>/kick &lt;user_id&gt; [reason]</code>",
            parse_mode="HTML",
        )
        return

    target, reason = result

    if await is_admin(target.id, bot=context.bot):
        await update.message.reply_text(status_text("error", "Cannot kick an admin."))
        return

    try:
        # Ban + immediate unban = kick
        await context.bot.ban_chat_member(
            update.effective_chat.id, target.id
        )
        await context.bot.unban_chat_member(
            update.effective_chat.id, target.id
        )

        await log_action(
            action="kick",
            admin_telegram_id=update.effective_user.id,
            target_telegram_id=target.id,
            reason=reason,
        )

        name = _display_name(target)
        msg = await update.message.reply_text(
            format_kick(name, reason),
            parse_mode="HTML",
        )
        await schedule_delete(msg, context, 15)
        logger.info(
            "User %s kicked by admin %s: %s",
            target.id,
            update.effective_user.id,
            reason,
        )

    except Exception as exc:
        logger.error("Kick command failed: %s", exc)
        await update.message.reply_text(
            status_text("error", "An error occurred. Please try again.")
        )


# ---------------------------------------------------------------------------
# /pin
# ---------------------------------------------------------------------------


async def pin_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Pin the replied-to message in the group chat.

    Usage: Reply to a message with ``/pin``

    Args:
        update: Incoming Telegram update
        context: Bot context
    """
    if not await is_admin(update.effective_user.id, bot=context.bot):
        await update.message.reply_text(
            status_text("error", "You don't have permission to use this command.")
        )
        return

    if not update.message.reply_to_message:
        await update.message.reply_text(
            "Usage: Reply to a message with <code>/pin</code>",
            parse_mode="HTML",
        )
        return

    try:
        await update.message.reply_to_message.pin()

        await log_action(
            action="pin",
            admin_telegram_id=update.effective_user.id,
            metadata={
                "message_id": update.message.reply_to_message.message_id
            },
        )

        msg = await update.message.reply_text(status_text("success", "Message pinned."))
        await schedule_delete(msg, context, 15)
        logger.info(
            "Message pinned by admin %s", update.effective_user.id
        )

    except Exception as exc:
        logger.error("Pin command failed: %s", exc)
        await update.message.reply_text(
            status_text("error", "Could not pin the message. Make sure the bot has pin permissions.")
        )


# ---------------------------------------------------------------------------
# /rules
# ---------------------------------------------------------------------------


async def rules_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Post the community rules to the current chat.

    Available to all users (no admin check).

    Args:
        update: Incoming Telegram update
        context: Bot context
    """
    if not await check_rate_limit(
        update.effective_user.id, "command", 10, 60
    ):
        await update.message.reply_text(status_text("warning", "Please slow down."))
        return

    msg = await update.message.reply_text(
        format_rules(),
        parse_mode="HTML",
    )
    await schedule_delete(msg, context, 60)


# ---------------------------------------------------------------------------
# /announce
# ---------------------------------------------------------------------------


async def announce_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Send a simple announcement to the community group.

    Usage: ``/announce Your announcement text here``

    Admin only.  For rich broadcasts with media, use ``/broadcast``
    (Module 4).

    Args:
        update: Incoming Telegram update
        context: Bot context
    """
    from config import ADMIN_CHANNEL_ID as _ADMIN_CH
    if update.effective_chat and update.effective_chat.id != _ADMIN_CH:
        await update.message.reply_text(
            status_text("error", "This command can only be used in the admin channel.")
        )
        return

    if not await is_admin(update.effective_user.id, bot=context.bot):
        await update.message.reply_text(
            status_text("error", "You don't have permission to use this command.")
        )
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: <code>/announce Your message here</code>",
            parse_mode="HTML",
        )
        return

    text = " ".join(context.args)
    safe_text = html.escape(text)

    try:
        sent = 0
        for chat_id in COMMUNITY_GROUP_IDS:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"{title('Announcement', '📢')}\n\n{safe_text}",
                parse_mode="HTML",
            )
            sent += 1

        await log_action(
            action="announce",
            admin_telegram_id=update.effective_user.id,
            reason="Announcement sent",
            metadata={"text": text[:500]},
        )

        await update.message.reply_text(status_text("success", f"Announcement sent to {sent} community group(s)."))
        logger.info(
            "Announcement sent by admin %s", update.effective_user.id
        )

    except Exception as exc:
        logger.error("Announce command failed: %s", exc)
        await update.message.reply_text(
            status_text("error", "Failed to send announce. Please try again.")
        )
