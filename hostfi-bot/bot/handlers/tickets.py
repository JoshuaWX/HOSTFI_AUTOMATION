"""
Module: tickets.py
Purpose: Support ticket lifecycle — creation, claiming, replies, closing, rating
Author: HOSTFI Bot Team
"""

import html
import logging
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler

from bot.utils.auto_delete import schedule_error_delete
from bot.utils.keyboards import rating_keyboard, ticket_keyboard
from bot.utils.permissions import is_admin, is_admin_channel_chat
from bot.utils.rate_limiter import check_rate_limit
from config import ADMIN_CHANNEL_ID
from database.logs import log_action
from database.tickets import (
    cancel_ticket,
    claim_ticket,
    create_ticket,
    get_all_active_tickets,
    get_user_active_tickets,
    get_ticket_by_id,
    rate_ticket,
    resolve_ticket,
)
from database.users import add_xp

logger = logging.getLogger(__name__)


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
# ConversationHandler state for ticket creation
# ---------------------------------------------------------------------------

TICKET_DESCRIPTION = 0


# ---------------------------------------------------------------------------
# /support — Step 1: Initiate ticket creation
# ---------------------------------------------------------------------------


async def support_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """
    Handle /support — start the ticket creation flow.

    Checks if the user has reached the active-ticket limit. If not,
    prompts the user to describe their issue.

    Args:
        update: Incoming Telegram update
        context: Bot context

    Returns:
        TICKET_DESCRIPTION state for ConversationHandler
    """
    try:
        if not update.effective_user or not update.effective_message:
            return ConversationHandler.END

        if update.effective_chat and update.effective_chat.type != "private":
            await _reply_error(
                update,
                context,
                "⛔ /support is available in DM only. Please message the bot privately.",
            )
            return ConversationHandler.END

        user_id = update.effective_user.id

        if not await check_rate_limit(user_id, "command", limit=10, window=60):
            await _reply_error(update, context, "⏳ Too many requests. Please wait a moment.")
            return ConversationHandler.END

        # Enforce max two active tickets per user
        active_tickets = await get_user_active_tickets(user_id)
        if len(active_tickets) >= 2:
            ticket_lines = []
            for t in active_tickets[:2]:
                ticket_lines.append(
                    f"• <b>{html.escape(t['ticket_id'])}</b> ({html.escape(t['status'].capitalize())})"
                )
            await _reply_error(
                update,
                context,
                "🎫 You already have the maximum number of active tickets (2).\n\n"
                "<b>Your active tickets:</b>\n"
                f"{'\n'.join(ticket_lines)}\n\n"
                "Please wait for one to be resolved before opening a new ticket.",
                parse_mode="HTML",
            )
            return ConversationHandler.END

        await update.effective_message.reply_text(
            "🎫 <b>HOSTFI Support</b>\n\n"
            "Please briefly describe your issue in a single message.\n\n"
            "<i>Be specific to help our team resolve it faster.</i>\n\n"
            "Send /cancel to abort.",
            parse_mode="HTML",
        )
        return TICKET_DESCRIPTION

    except Exception as exc:
        logger.error("Error in support_command: %s", exc)
        return ConversationHandler.END


# ---------------------------------------------------------------------------
# Step 2: Receive issue description → create ticket
# ---------------------------------------------------------------------------


async def ticket_receive_description(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """
    Receive the user's issue description and create the ticket.

    Creates the ticket in Supabase, confirms to the user, and posts
    a claim alert to the admin channel.

    Args:
        update: Incoming Telegram update
        context: Bot context

    Returns:
        ConversationHandler.END
    """
    try:
        if not update.effective_message or not update.effective_user:
            return ConversationHandler.END

        user = update.effective_user
        description = (update.effective_message.text or "").strip()

        if not description:
            await _reply_error(update, context, "❌ Please send a text description of your issue.")
            return TICKET_DESCRIPTION

        if len(description) > 2000:
            await _reply_error(
                update,
                context,
                "❌ Description too long. Please keep it under 2000 characters.",
            )
            return TICKET_DESCRIPTION

        # Create ticket in database
        ticket = await create_ticket(user.id, description)

        if ticket is None:
            await _reply_error(
                update,
                context,
                "❌ You already have 2 active tickets. "
                "Please wait for one to be resolved first.",
            )
            return ConversationHandler.END

        ticket_id = ticket["ticket_id"]
        safe_name = html.escape(user.first_name or str(user.id))
        safe_desc = html.escape(description[:500])

        # Confirm to user
        await update.effective_message.reply_text(
            f"✅ <b>Ticket Created!</b>\n\n"
            f"🎫 <b>Ticket ID:</b> {ticket_id}\n"
            f"📝 <b>Issue:</b> {safe_desc}\n\n"
            "Our support team has been notified and will get back "
            "to you shortly. You'll receive a message when an agent "
            "claims your ticket.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "❌ Cancel Ticket",
                    callback_data=f"ticket_cancel_{ticket_id}",
                )]
            ]),
        )

        # Post to admin channel with claim button
        admin_alert = (
            f"🎫 <b>New Support Ticket</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"🆔 <b>Ticket:</b> {ticket_id}\n"
            f"👤 <b>User:</b> <a href='tg://user?id={user.id}'>{safe_name}</a> (<code>{user.id}</code>)\n"
            f"📝 <b>Issue:</b>\n{safe_desc}\n\n"
            f"🕐 <b>Created:</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
        )

        await context.bot.send_message(
            chat_id=ADMIN_CHANNEL_ID,
            text=admin_alert,
            parse_mode="HTML",
            reply_markup=ticket_keyboard(ticket_id),
        )

        await log_action(
            action="ticket_created",
            admin_telegram_id=0,
            target_telegram_id=user.id,
            metadata={"ticket_id": ticket_id},
        )

        logger.info(
            "Ticket %s created by user %s",
            ticket_id,
            user.id,
        )

        return ConversationHandler.END

    except Exception as exc:
        logger.error("Error in ticket_receive_description: %s", exc)
        if update.effective_message:
            await _reply_error(
                update,
                context,
                "⚠️ Something went wrong. Please try /support again.",
            )
        return ConversationHandler.END


# ---------------------------------------------------------------------------
# Cancel ticket creation
# ---------------------------------------------------------------------------


async def ticket_cancel(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """
    Cancel the ticket creation conversation flow.

    Args:
        update: Incoming Telegram update
        context: Bot context

    Returns:
        ConversationHandler.END
    """
    if update.effective_message:
        await update.effective_message.reply_text(
            "❌ Ticket creation cancelled."
        )
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Inline ticket cancel callback
# ---------------------------------------------------------------------------


async def ticket_cancel_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle the inline [Cancel Ticket] button press from the user.

    Only cancels open (unclaimed) tickets owned by the pressing user.
    Callback data format: ticket_cancel_HSTF-0001
    """
    query = update.callback_query
    if not query or not query.data:
        return

    # Parse ticket_id: ticket_cancel_HSTF-0001
    parts = query.data.split("_", 2)  # ["ticket", "cancel", "HSTF-0001"]
    if len(parts) != 3:
        return

    ticket_id = parts[2]
    user_id = query.from_user.id

    ticket = await cancel_ticket(ticket_id, user_id)

    if ticket is None:
        await query.answer(
            "❌ Cannot cancel — ticket not found, already claimed, or not yours.",
            show_alert=True,
        )
        return

    await query.answer(f"✅ Ticket {ticket_id} cancelled")

    # Update the user's message to reflect cancellation
    await query.edit_message_text(
        f"🎫 <b>Ticket {ticket_id}</b> — <i>Cancelled</i>\n\n"
        "You can open a new ticket anytime with /support.",
        parse_mode="HTML",
    )

    await log_action(
        action="ticket_cancelled",
        admin_telegram_id=0,
        target_telegram_id=user_id,
        metadata={"ticket_id": ticket_id},
    )

    logger.info("Ticket %s cancelled by user %s", ticket_id, user_id)


# ---------------------------------------------------------------------------
# Ticket claim callback
# ---------------------------------------------------------------------------


async def ticket_claim_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle the [Claim Ticket] inline button press from the admin channel.

    Callback data format: ticket_claim_HSTF-0001

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

    # Parse ticket_id from callback data: ticket_claim_HSTF-0001
    parts = query.data.split("_", 2)  # ["ticket", "claim", "HSTF-0001"]
    if len(parts) != 3:
        return

    ticket_id = parts[2]
    admin_id = query.from_user.id
    admin_name = html.escape(query.from_user.first_name or str(admin_id))

    ticket = await claim_ticket(ticket_id, admin_id)

    if ticket is None:
        await query.answer(
            "❌ Ticket not found or already claimed.",
            show_alert=True,
        )
        return

    await query.answer(f"✅ You claimed {ticket_id}")

    # Update the admin channel message with claimed status and remove claim button
    await query.edit_message_text(
        query.message.text_html
        + f"\n\n✅ <b>Claimed by:</b> <a href='tg://user?id={admin_id}'>{admin_name}</a>",
        parse_mode="HTML",
        reply_markup=None,
    )

    # Notify the user with clickable admin link
    user_telegram_id = ticket.get("user_telegram_id")
    try:
        await context.bot.send_message(
            chat_id=user_telegram_id,
            text=(
                f"🎫 <b>Ticket Update — {ticket_id}</b>\n\n"
                f"An agent has picked up your ticket. "
                "They'll contact you shortly.\n\n"
                f"🧑‍💼 <b>Agent:</b> <a href='tg://user?id={admin_id}'>{admin_name}</a>\n\n"
                "<i>Please be patient while we review your issue.</i>"
            ),
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.warning("Could not notify user %s about claim: %s", user_telegram_id, exc)

    await log_action(
        action="ticket_claimed",
        admin_telegram_id=admin_id,
        target_telegram_id=user_telegram_id,
        metadata={"ticket_id": ticket_id},
    )

    logger.info(
        "Ticket %s claimed by admin %s",
        ticket_id,
        admin_id,
    )


# ---------------------------------------------------------------------------
# /reply {ticket_id} {message} — Admin replies to a ticket user
# ---------------------------------------------------------------------------


async def reply_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle /reply — admin sends a message to the ticket user.

    Usage: /reply HSTF-0001 Your issue has been resolved...

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
        parts = text.split(None, 2)  # ["/reply", "HSTF-0001", "message..."]

        if len(parts) < 3:
            await _reply_error(
                update,
                context,
                "ℹ️ <b>Usage:</b>\n"
                "<code>/reply HSTF-0001 Your message here</code>",
                parse_mode="HTML",
            )
            return

        ticket_id = parts[1].upper()
        message = parts[2]

        # Validate ticket exists and is claimed
        ticket = await get_ticket_by_id(ticket_id)
        if not ticket:
            await _reply_error(
                update,
                context,
                f"❌ Ticket <b>{html.escape(ticket_id)}</b> not found.",
                parse_mode="HTML",
            )
            return

        if ticket["status"] not in ("claimed", "open"):
            await _reply_error(
                update,
                context,
                f"❌ Ticket <b>{ticket_id}</b> is already {ticket['status']}.",
            )
            return

        user_telegram_id = ticket["user_telegram_id"]
        admin_name = html.escape(
            update.effective_user.first_name or str(update.effective_user.id)
        )
        safe_message = html.escape(message)

        # Send message to the user with clickable admin link
        admin_id = update.effective_user.id
        try:
            await context.bot.send_message(
                chat_id=user_telegram_id,
                text=(
                    f"💬 <b>Support Reply — {ticket_id}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━\n\n"
                    f"🧑‍💼 <b><a href='tg://user?id={admin_id}'>{admin_name}</a>:</b>\n"
                    f"{safe_message}"
                ),
                parse_mode="HTML",
            )
        except Exception as send_exc:
            await _reply_error(update, context, f"⚠️ Could not send message to user: {send_exc}")
            return

        await update.effective_message.reply_text(
            f"✅ Reply sent to the user for ticket <b>{ticket_id}</b>.",
            parse_mode="HTML",
        )

        await log_action(
            action="ticket_reply",
            admin_telegram_id=update.effective_user.id,
            target_telegram_id=user_telegram_id,
            metadata={
                "ticket_id": ticket_id,
                "message_preview": message[:100],
            },
        )

    except Exception as exc:
        logger.error("Error in reply_command: %s", exc)


# ---------------------------------------------------------------------------
# /close {ticket_id} — Admin resolves a ticket
# ---------------------------------------------------------------------------


async def close_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle /close — admin resolves a ticket and triggers rating flow.

    Usage: /close HSTF-0001

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
                "ℹ️ <b>Usage:</b>\n"
                "<code>/close HSTF-0001</code>",
                parse_mode="HTML",
            )
            return

        ticket_id = parts[1].upper()

        ticket = await resolve_ticket(ticket_id)
        if not ticket:
            await _reply_error(
                update,
                context,
                f"❌ Ticket <b>{html.escape(ticket_id)}</b> not found "
                "or not in claimed status.",
                parse_mode="HTML",
            )
            return

        user_telegram_id = ticket["user_telegram_id"]

        # Notify user and send rating keyboard
        try:
            await context.bot.send_message(
                chat_id=user_telegram_id,
                text=(
                    f"✅ <b>Ticket Resolved — {ticket_id}</b>\n\n"
                    "Your support ticket has been resolved.\n\n"
                    "How would you rate our support? "
                    "Tap a rating below:"
                ),
                parse_mode="HTML",
                reply_markup=rating_keyboard(ticket_id),
            )
        except Exception as send_exc:
            logger.warning(
                "Could not send rating request to user %s: %s",
                user_telegram_id,
                send_exc,
            )

        await update.effective_message.reply_text(
            f"✅ Ticket <b>{ticket_id}</b> resolved. "
            "User has been notified with a rating prompt.",
            parse_mode="HTML",
        )

        await log_action(
            action="ticket_resolved",
            admin_telegram_id=update.effective_user.id,
            target_telegram_id=user_telegram_id,
            metadata={"ticket_id": ticket_id},
        )

        logger.info(
            "Ticket %s resolved by admin %s",
            ticket_id,
            update.effective_user.id,
        )

    except Exception as exc:
        logger.error("Error in close_command: %s", exc)


# ---------------------------------------------------------------------------
# Rating callback
# ---------------------------------------------------------------------------


async def rating_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle rating button press for resolved tickets.

    Callback data format: rate_HSTF-0001_3

    Awards +10 XP if the rating is 5 stars.

    Args:
        update: Incoming callback query update
        context: Bot context
    """
    query = update.callback_query
    if not query or not query.data:
        return

    # Parse: rate_HSTF-0001_3
    parts = query.data.split("_")
    # ["rate", "HSTF-0001", "3"]
    if len(parts) != 3 or parts[0] != "rate":
        return

    ticket_id = parts[1]
    try:
        rating = int(parts[2])
    except ValueError:
        await query.answer("Invalid rating.", show_alert=True)
        return

    if rating < 1 or rating > 5:
        await query.answer("Invalid rating.", show_alert=True)
        return

    # Verify the ticket belongs to this user
    ticket = await get_ticket_by_id(ticket_id)
    if not ticket:
        await query.answer("Ticket not found.", show_alert=True)
        return

    if ticket.get("user_telegram_id") != query.from_user.id:
        await query.answer(
            "⛔ This rating is not for you!", show_alert=True
        )
        return

    if ticket.get("status") != "resolved":
        await query.answer(
            "This ticket has already been rated.", show_alert=True
        )
        return

    # Store rating
    result = await rate_ticket(ticket_id, rating)
    if not result:
        await query.answer("❌ Failed to save rating.", show_alert=True)
        return

    stars = "⭐" * rating
    await query.answer(f"Thank you! {stars}")
    await query.edit_message_text(
        f"✅ <b>Ticket {ticket_id} — Closed</b>\n\n"
        f"Your rating: {stars} ({rating}/5)\n\n"
        "Thank you for your feedback! 🙏",
        parse_mode="HTML",
    )

    # +10 XP for 5-star rating
    if rating == 5:
        admin_id = ticket.get("assigned_admin_id")
        if admin_id:
            try:
                await add_xp(admin_id, 10)
                logger.info(
                    "Admin %s earned +10 XP from 5-star ticket %s",
                    admin_id,
                    ticket_id,
                )
            except Exception:
                pass  # XP is non-critical

    # Notify admin channel about the rating
    try:
        await context.bot.send_message(
            chat_id=ADMIN_CHANNEL_ID,
            text=(
                f"📊 <b>Ticket Rating — {ticket_id}</b>\n\n"
                f"Rating: {stars} ({rating}/5)\n"
                f"User: <code>{query.from_user.id}</code>"
            ),
            parse_mode="HTML",
        )
    except Exception:
        pass

    await log_action(
        action="ticket_rated",
        admin_telegram_id=0,
        target_telegram_id=query.from_user.id,
        metadata={"ticket_id": ticket_id, "rating": rating},
    )

    logger.info("Ticket %s rated %d stars", ticket_id, rating)


# ---------------------------------------------------------------------------
# /tickets — Admin views all active tickets
# ---------------------------------------------------------------------------


async def tickets_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle /tickets — admin-only view of all active tickets.

    Shows open and claimed tickets with their current status.

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

        tickets = await get_all_active_tickets()

        if not tickets:
            await _reply_error(update, context, "📭 No active tickets at the moment.")
            return

        status_emoji = {"open": "🟡", "claimed": "🟢"}
        lines: list[str] = [
            "🎫 <b>Active Support Tickets</b>",
            "━━━━━━━━━━━━━━━━━━",
        ]

        for t in tickets:
            tid = t["ticket_id"]
            status = t.get("status", "open")
            emoji = status_emoji.get(status, "⚪")
            user_id = t.get("user_telegram_id", "?")
            desc = html.escape((t.get("issue_description") or "")[:80])
            admin_id = t.get("assigned_admin_id")
            admin_info = f" → Admin <code>{admin_id}</code>" if admin_id else ""

            lines.append(
                f"\n{emoji} <b>{tid}</b> [{status}]{admin_info}\n"
                f"   👤 <code>{user_id}</code>\n"
                f"   📝 {desc}"
            )

        lines.append(f"\n━━━━━━━━━━━━━━━━━━")
        lines.append(f"Total: <b>{len(tickets)}</b> active ticket(s)")

        await update.effective_message.reply_text(
            "\n".join(lines), parse_mode="HTML"
        )

    except Exception as exc:
        logger.error("Error in tickets_command: %s", exc)
        await _reply_error(update, context, "⚠️ Something went wrong. Please try again.")


# ---------------------------------------------------------------------------
# Escalation builder (for scheduler)
# ---------------------------------------------------------------------------


async def build_escalation_alerts(bot) -> int:
    """
    Check for stale unclaimed tickets and re-alert the admin channel.

    Called by the scheduler every 30 minutes. Posts a reminder for
    tickets that have been open (unclaimed) for 2+ hours.

    Args:
        bot: The telegram Bot instance

    Returns:
        Number of escalation alerts sent
    """
    from database.tickets import get_unclaimed_old_tickets

    stale_tickets = await get_unclaimed_old_tickets(hours=2)
    if not stale_tickets:
        return 0

    count = 0
    for t in stale_tickets:
        ticket_id = t["ticket_id"]
        user_id = t.get("user_telegram_id", "?")
        desc = html.escape((t.get("issue_description") or "")[:200])
        created = t.get("created_at", "Unknown")

        try:
            await bot.send_message(
                chat_id=ADMIN_CHANNEL_ID,
                text=(
                    f"🚨 <b>ESCALATION — Unclaimed Ticket!</b>\n"
                    f"━━━━━━━━━━━━━━━━━━\n\n"
                    f"🆔 <b>Ticket:</b> {ticket_id}\n"
                    f"👤 <b>User:</b> <code>{user_id}</code>\n"
                    f"📝 <b>Issue:</b>\n{desc}\n\n"
                    f"🕐 <b>Created:</b> {created}\n\n"
                    "⚠️ This ticket has been waiting <b>2+ hours</b> "
                    "without being claimed!"
                ),
                parse_mode="HTML",
                reply_markup=ticket_keyboard(ticket_id),
            )
            count += 1
        except Exception as exc:
            logger.error(
                "Failed to send escalation for ticket %s: %s",
                ticket_id,
                exc,
            )

    if count:
        logger.info("Sent %d ticket escalation alert(s)", count)

    return count
