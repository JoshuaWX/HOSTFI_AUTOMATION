"""
Module: auto_delete.py
Purpose: Schedule bot messages for auto-deletion in the community group
         to reduce chat clutter / spam.
Author: HOSTFI Bot Team
"""

import logging

from telegram import Message
from telegram.ext import ContextTypes

from config import COMMUNITY_GROUP_ID

logger = logging.getLogger(__name__)


async def _delete_message_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job callback that deletes a scheduled message."""
    data = context.job.data
    try:
        await context.bot.delete_message(data["chat_id"], data["message_id"])
    except Exception:
        pass  # Message may already be deleted


async def schedule_delete(
    message: Message,
    context: ContextTypes.DEFAULT_TYPE,
    delay: int,
) -> None:
    """
    Schedule a bot message for auto-deletion after *delay* seconds.

    Only takes effect in the community group. In DMs and other chats
    the message is left untouched.

    Args:
        message: The sent Message object to delete later
        context: Bot context (must have job_queue)
        delay: Seconds before deletion
    """
    if not COMMUNITY_GROUP_ID or message.chat_id != COMMUNITY_GROUP_ID:
        return

    context.job_queue.run_once(
        _delete_message_job,
        when=delay,
        data={"chat_id": message.chat_id, "message_id": message.message_id},
        name=f"auto_delete_{message.message_id}",
    )


async def schedule_error_delete(
    message: Message,
    context: ContextTypes.DEFAULT_TYPE,
    delay: int = 5,
) -> None:
    """
    Schedule error/denial messages for quick cleanup in group chats only.

    Args:
        message: The sent Message object to delete later
        context: Bot context (must have job_queue)
        delay: Seconds before deletion (default 5)
    """
    if message.chat.type not in ("group", "supergroup"):
        return

    context.job_queue.run_once(
        _delete_message_job,
        when=delay,
        data={"chat_id": message.chat_id, "message_id": message.message_id},
        name=f"error_delete_{message.message_id}",
    )
