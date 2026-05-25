"""
Module: application.py
Purpose: Build the telegram.ext.Application with ALL handlers registered
         in the correct order
Author: HOSTFI Bot Team
"""

import logging

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    TypeHandler,
    filters,
)

from config import (
    ADMIN_CHANNEL_ID,
    COMMUNITY_GROUP_ID_VARIANTS,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_WEBHOOK_SECRET,
)

logger = logging.getLogger(__name__)


def build_application() -> Application:
    """
    Build and configure the telegram.ext.Application with all handlers.

    Handler registration order (per spec):
    1. ConversationHandlers (broadcast, ticket — these have internal state)
    2. Command handlers (all /commands)
    3. Callback query handlers (inline button presses)
    4. Message handlers (new member, left member, group filter)

    Returns:
        Fully configured Application instance (not yet running)
    """
    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )

    _register_group_guard(app)
    _register_conversation_handlers(app)
    _register_command_handlers(app)
    _register_callback_handlers(app)
    _register_message_handlers(app)

    logger.info("Application built with all handlers registered")
    return app


# ---------------------------------------------------------------------------
# Group guard — block unauthorised groups
# ---------------------------------------------------------------------------


def _register_group_guard(app: Application) -> None:
    """Block the bot from operating in unauthorised groups."""

    def _id_variants(chat_id: int) -> set[int]:
        """Return safe variants for Telegram chat IDs (legacy vs -100 style)."""
        variants = {chat_id}
        if chat_id == 0:
            return variants

        abs_str = str(abs(chat_id))
        # If user stored legacy negative form, accept modern -100-prefixed form too.
        if chat_id < 0 and not abs_str.startswith("100"):
            variants.add(int(f"-100{abs_str}"))

        # If user stored -100-prefixed form, accept legacy negative form too.
        if chat_id < 0 and abs_str.startswith("100") and len(abs_str) > 3:
            variants.add(-int(abs_str[3:]))

        return variants

    async def _guard(update: Update, context):
        chat = update.effective_chat
        if chat is None:
            return
        if chat.type in ("group", "supergroup"):
            allowed: set[int] = set(COMMUNITY_GROUP_ID_VARIANTS)
            allowed.update(_id_variants(ADMIN_CHANNEL_ID))
            allowed.discard(0)
            if chat.id not in allowed:
                logger.warning(
                    "Unauthorised group %s (%s) — leaving. Allowed IDs: %s",
                    chat.id,
                    chat.title,
                    sorted(allowed),
                )
                try:
                    await context.bot.leave_chat(chat.id)
                except Exception as exc:
                    logger.error("Failed to leave chat %s: %s", chat.id, exc)
                from telegram.ext import ApplicationHandlerStop
                raise ApplicationHandlerStop
            logger.info("Authorised group detected: %s (%s)", chat.id, chat.title)

    app.add_handler(TypeHandler(Update, _guard), group=-1)
    logger.info("Group guard registered — only authorised groups allowed")


# ---------------------------------------------------------------------------
# Conversation handlers
# ---------------------------------------------------------------------------


def _register_conversation_handlers(app: Application) -> None:
    """Register ConversationHandler flows (broadcast, tickets)."""

    from bot.handlers.broadcast import (
        BROADCAST_CONTENT,
        broadcast_cancel,
        broadcast_command,
        broadcast_receive_content,
    )
    from bot.handlers.tickets import (
        TICKET_DESCRIPTION,
        support_command,
        ticket_cancel,
        ticket_receive_description,
    )

    # Broadcast conversation: /broadcast → receive content → confirm via callback
    broadcast_conv = ConversationHandler(
        entry_points=[CommandHandler("broadcast", broadcast_command)],
        states={
            BROADCAST_CONTENT: [
                MessageHandler(
                    filters.TEXT | filters.PHOTO | filters.VIDEO,
                    broadcast_receive_content,
                ),
            ],
        },
        fallbacks=[CommandHandler("cancel", broadcast_cancel)],
        allow_reentry=True,
    )
    app.add_handler(broadcast_conv)

    # Ticket conversation: /support → describe issue → create ticket
    ticket_conv = ConversationHandler(
        entry_points=[CommandHandler("support", support_command)],
        states={
            TICKET_DESCRIPTION: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    ticket_receive_description,
                ),
            ],
        },
        fallbacks=[CommandHandler("cancel", ticket_cancel)],
        allow_reentry=True,
    )
    app.add_handler(ticket_conv)

    logger.info("Conversation handlers registered (broadcast, tickets)")


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def _register_command_handlers(app: Application) -> None:
    """Register all /command handlers."""

    # M4: /start, broadcasts, polls, and campaign leaderboard
    from bot.handlers.broadcast import (
        leaderboard_command,
        poll_command,
        rank_command,
        start_command,
    )

    app.add_handler(CommandHandler("start", start_command))

    # /help command
    from bot.handlers.community import help_command

    app.add_handler(CommandHandler("help", help_command))

    # M1: Moderation commands
    from bot.handlers.moderation import (
        announce_command,
        ban_command,
        kick_command,
        mute_command,
        pin_command,
        rules_command,
        unban_command,
        unmute_command,
        warn_command,
    )

    app.add_handler(CommandHandler("warn", warn_command))
    app.add_handler(CommandHandler("mute", mute_command))
    app.add_handler(CommandHandler("unmute", unmute_command))
    app.add_handler(CommandHandler("ban", ban_command))
    app.add_handler(CommandHandler("unban", unban_command))
    app.add_handler(CommandHandler("kick", kick_command))
    app.add_handler(CommandHandler("pin", pin_command))
    app.add_handler(CommandHandler("rules", rules_command))
    app.add_handler(CommandHandler("announce", announce_command))

    # M2: AI assistant
    from bot.handlers.support import ask_command

    app.add_handler(CommandHandler("ask", ask_command))

    # M3: Market commands intentionally hidden/disabled

    # M4: Broadcast & engagement
    app.add_handler(CommandHandler("poll", poll_command))
    app.add_handler(CommandHandler("rank", rank_command))
    app.add_handler(CommandHandler("leaderboard", leaderboard_command))

    # Campaign XP
    from bot.handlers.campaign import (
        award_command,
        campaign_command,
        cycle_command,
        invite_command,
        invites_command,
        raid_command,
        raids_command,
        xlink_command,
        xpost_command,
        xp_router_command,
        xverify_command,
    )

    app.add_handler(CommandHandler("campaign", campaign_command))
    app.add_handler(CommandHandler("xp", xp_router_command))
    app.add_handler(CommandHandler("invite", invite_command))
    app.add_handler(CommandHandler("invites", invites_command))
    app.add_handler(CommandHandler("xlink", xlink_command))
    app.add_handler(CommandHandler("xverify", xverify_command))
    app.add_handler(CommandHandler("raids", raids_command))
    app.add_handler(CommandHandler("raid", raid_command))
    app.add_handler(CommandHandler("xpost", xpost_command))
    app.add_handler(CommandHandler("cycle", cycle_command))
    app.add_handler(CommandHandler("award", award_command))

    # M5: Ticket commands (admin)
    from bot.handlers.tickets import (
        close_command,
        reply_command,
        tickets_command,
    )

    app.add_handler(CommandHandler("tickets", tickets_command))
    app.add_handler(CommandHandler("reply", reply_command))
    app.add_handler(CommandHandler("close", close_command))

    # M6: Admin dashboard
    from bot.handlers.admin import (
        admin_command,
        adminhelp_command,
        lookup_command,
        reindex_command,
        stats_command,
    )

    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("lookup", lookup_command))
    app.add_handler(CommandHandler("reindex", reindex_command))
    app.add_handler(CommandHandler("adminhelp", adminhelp_command))

    logger.info("Command handlers registered")


# ---------------------------------------------------------------------------
# Callback query handlers
# ---------------------------------------------------------------------------


def _register_callback_handlers(app: Application) -> None:
    """Register all inline keyboard callback handlers."""

    from bot.handlers.admin import admin_callback_handler
    from bot.handlers.broadcast import broadcast_confirm_callback
    from bot.handlers.campaign import (
        campaign_callback_handler,
        raid_submit_info_callback,
        xpost_review_callback,
    )
    from bot.handlers.community import (
        help_callback,
        rules_callback,
        verification_callback,
    )
    # from bot.handlers.market import alert_cancel_callback
    from bot.handlers.tickets import rating_callback, ticket_cancel_callback, ticket_claim_callback

    # CAPTCHA verification buttons
    app.add_handler(
        CallbackQueryHandler(verification_callback, pattern=r"^captcha_")
    )

    # Welcome keyboard callbacks
    app.add_handler(
        CallbackQueryHandler(rules_callback, pattern=r"^show_rules$")
    )
    app.add_handler(
        CallbackQueryHandler(help_callback, pattern=r"^show_help$")
    )

    # Broadcast confirm/cancel
    app.add_handler(
        CallbackQueryHandler(
            broadcast_confirm_callback, pattern=r"^broadcast_(confirm|cancel)_"
        )
    )

    app.add_handler(
        CallbackQueryHandler(campaign_callback_handler, pattern=r"^campaign_")
    )

    app.add_handler(
        CallbackQueryHandler(xpost_review_callback, pattern=r"^xpost_(approve|reject)_")
    )

    app.add_handler(
        CallbackQueryHandler(admin_callback_handler, pattern=r"^admin_")
    )

    app.add_handler(
        CallbackQueryHandler(raid_submit_info_callback, pattern=r"^raid_submit_info_")
    )

    # Ticket claim
    app.add_handler(
        CallbackQueryHandler(ticket_claim_callback, pattern=r"^ticket_claim_")
    )

    # Ticket cancel (user inline button)
    app.add_handler(
        CallbackQueryHandler(ticket_cancel_callback, pattern=r"^ticket_cancel_")
    )

    # Ticket rating
    app.add_handler(
        CallbackQueryHandler(rating_callback, pattern=r"^rate_")
    )

    # Alert cancel (per-alert inline button)
    # app.add_handler(
    #     CallbackQueryHandler(alert_cancel_callback, pattern=r"^alert_cancel_")
    # )

    logger.info("Callback query handlers registered")


# ---------------------------------------------------------------------------
# Message handlers (filters)
# ---------------------------------------------------------------------------


def _register_message_handlers(app: Application) -> None:
    """Register message-level handlers (new member, left member, group filter)."""

    from bot.handlers.community import (
        group_message_filter,
        left_member_handler,
        new_member_handler,
    )
    from bot.handlers.campaign import campaign_guided_input_handler
    from bot.handlers.tickets import support_pending_dm_handler

    # New member join events
    app.add_handler(
        MessageHandler(
            filters.StatusUpdate.NEW_CHAT_MEMBERS, new_member_handler
        )
    )

    # Left member events (delete service message)
    app.add_handler(
        MessageHandler(
            filters.StatusUpdate.LEFT_CHAT_MEMBER, left_member_handler
        )
    )

    # Campaign button flows consume the user's next text message when pending.
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE
            & filters.TEXT
            & ~filters.COMMAND
            & ~filters.StatusUpdate.ALL,
            support_pending_dm_handler,
            block=False,
        ),
        group=1,
    )

    # Campaign button flows consume the user's next text message when pending.
    app.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND
            & ~filters.StatusUpdate.ALL,
            campaign_guided_input_handler,
        ),
        group=1,
    )

    # Group message filter (spam, scam, flood, +XP)
    # This must be last — it processes all non-command text messages
    app.add_handler(
        MessageHandler(
            filters.ALL
            & ~filters.COMMAND
            & ~filters.StatusUpdate.ALL,
            group_message_filter,
        ),
        group=2,
    )

    logger.info("Message handlers registered")
