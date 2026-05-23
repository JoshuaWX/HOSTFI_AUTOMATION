"""
Module: permissions.py
Purpose: Admin and superadmin permission verification helpers.
         Admins are auto-detected from the Telegram group (like Rose bot) —
         anyone promoted to admin/creator in the community group automatically
         has admin access to the bot.  ADMIN_IDS env var is an optional
         override list for extra IDs that should always be treated as admin.
Author: HOSTFI Bot Team
"""

import logging

from telegram import Bot

from config import ADMIN_CHANNEL_ID, ADMIN_IDS, COMMUNITY_GROUP_IDS, SUPERADMIN_IDS

logger = logging.getLogger(__name__)

# Cache to avoid hitting Telegram API on every command
# Stores {user_id: True/False} — cleared on bot restart
_admin_cache: dict[int, bool] = {}


async def is_admin(user_id: int, bot: Bot | None = None) -> bool:
    """
    Check whether a Telegram user is an admin.

    Checks in order:
    1. Is the user one of the SUPERADMIN_ID entries? → True
    2. Is the user in the ADMIN_IDS override list? → True
    3. Is the user an admin/creator in the community group? → True
       (auto-detected from Telegram, like Rose bot)

    Args:
        user_id: Telegram user ID
        bot: Telegram Bot instance (needed for group admin check)

    Returns:
        True if the user is an admin
    """
    if user_id in SUPERADMIN_IDS:
        return True

    if user_id in ADMIN_IDS:
        return True

    if user_id in _admin_cache:
        return _admin_cache[user_id]

    if bot and COMMUNITY_GROUP_IDS:
        for chat_id in COMMUNITY_GROUP_IDS:
            try:
                member = await bot.get_chat_member(chat_id, user_id)
                if member.status in ("administrator", "creator"):
                    _admin_cache[user_id] = True
                    return True
            except Exception as exc:
                logger.debug(
                    "Could not check group admin status for %s in %s: %s",
                    user_id,
                    chat_id,
                    exc,
                )
        _admin_cache[user_id] = False

    return False


async def is_superadmin(user_id: int) -> bool:
    """
    Check whether a Telegram user ID is one of the configured superadmins.

    The superadmin can do everything admins can, plus:
    - /reindex (rebuild AI knowledge base)
    - Cannot be warned/muted/banned by other admins

    Args:
        user_id: Telegram user ID

    Returns:
        True if the user is the superadmin
    """
    return user_id in SUPERADMIN_IDS


def is_admin_channel_chat(chat_id: int | None) -> bool:
    """Return True if chat_id matches ADMIN_CHANNEL_ID (supports legacy/-100 forms)."""
    if not chat_id or not ADMIN_CHANNEL_ID:
        return False

    allowed = {ADMIN_CHANNEL_ID}
    abs_str = str(abs(ADMIN_CHANNEL_ID))

    if ADMIN_CHANNEL_ID < 0 and not abs_str.startswith("100"):
        allowed.add(int(f"-100{abs_str}"))

    if ADMIN_CHANNEL_ID < 0 and abs_str.startswith("100") and len(abs_str) > 3:
        allowed.add(-int(abs_str[3:]))

    return chat_id in allowed


def clear_admin_cache() -> None:
    """Clear the admin cache (call when admin list might have changed)."""
    _admin_cache.clear()


def get_admin_ids() -> list[int]:
    """
    Return the static list of admin IDs from environment config.

    Used by scam filter to skip impersonation checks for known admins.

    Returns:
        Copy of the configured admin Telegram user IDs
    """
    return list(dict.fromkeys([*SUPERADMIN_IDS, *ADMIN_IDS]))
