"""
Module: permissions.py
Purpose: Admin and superadmin permission verification helpers
Author: HOSTFI Bot Team
"""

import logging

from config import ADMIN_IDS, SUPERADMIN_ID

logger = logging.getLogger(__name__)


def is_admin(user_id: int) -> bool:
    """
    Check whether a Telegram user ID belongs to a configured admin.

    Args:
        user_id: Telegram user ID

    Returns:
        True if the user is in the admin list
    """
    return user_id in ADMIN_IDS


def is_superadmin(user_id: int) -> bool:
    """
    Check whether a Telegram user ID is the superadmin.

    Args:
        user_id: Telegram user ID

    Returns:
        True if the user is the superadmin
    """
    return user_id == SUPERADMIN_ID


def get_admin_ids() -> list[int]:
    """
    Return the full list of admin Telegram IDs.

    Returns:
        Copy of the configured admin Telegram user IDs
    """
    return list(ADMIN_IDS)
