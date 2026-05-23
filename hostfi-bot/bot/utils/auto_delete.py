"""
Module: auto_delete.py
Purpose: Schedule bot messages for auto-deletion in the community group
         to reduce chat clutter / spam.
Author: HOSTFI Bot Team
"""

import logging

from telegram import Message, Update
from telegram.ext import ContextTypes

from config import is_community_group_chat

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
    if not is_community_group_chat(message.chat_id):
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


async def schedule_any_delete(
    message: Message,
    context: ContextTypes.DEFAULT_TYPE,
    delay: int,
) -> None:
    """
    Schedule a bot message for cleanup in any group/supergroup chat.

    DMs are left untouched so users do not lose private reference info.
    """
    if message.chat.type not in ("group", "supergroup"):
        return

    context.job_queue.run_once(
        _delete_message_job,
        when=delay,
        data={"chat_id": message.chat_id, "message_id": message.message_id},
        name=f"auto_delete_any_{message.message_id}",
    )


async def schedule_command_delete(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    delay: int,
) -> None:
    """Schedule the user's command message for cleanup in group chats."""
    message = update.message
    if not message:
        return
    await schedule_any_delete(message, context, delay)
