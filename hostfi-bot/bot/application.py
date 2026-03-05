"""
Module: application.py
Purpose: Build the telegram.ext.Application with ALL handlers registered
         in the correct order
Author: HOSTFI Bot Team
"""

import logging

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET

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

    _register_conversation_handlers(app)
    _register_command_handlers(app)
    _register_callback_handlers(app)
    _register_message_handlers(app)

    logger.info("Application built with all handlers registered")
    return app


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

    # M4: /start with referral deep link
    from bot.handlers.broadcast import (
        leaderboard_command,
        poll_command,
        rank_command,
        start_command,
    )

    app.add_handler(CommandHandler("start", start_command))

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

    # M3: Market data
    from bot.handlers.market import (
        alert_command,
        fear_command,
        market_command,
        price_command,
        rates_command,
    )

    app.add_handler(CommandHandler("price", price_command))
    app.add_handler(CommandHandler("rates", rates_command))
    app.add_handler(CommandHandler("market", market_command))
    app.add_handler(CommandHandler("fear", fear_command))
    app.add_handler(CommandHandler("alert", alert_command))

    # M4: Broadcast & engagement
    app.add_handler(CommandHandler("poll", poll_command))
    app.add_handler(CommandHandler("rank", rank_command))
    app.add_handler(CommandHandler("leaderboard", leaderboard_command))

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
        adminhelp_command,
        lookup_command,
        reindex_command,
        stats_command,
    )

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

    from bot.handlers.broadcast import broadcast_confirm_callback
    from bot.handlers.community import (
        help_callback,
        rules_callback,
        verification_callback,
    )
    from bot.handlers.tickets import rating_callback, ticket_claim_callback

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

    # Ticket claim
    app.add_handler(
        CallbackQueryHandler(ticket_claim_callback, pattern=r"^ticket_claim_")
    )

    # Ticket rating
    app.add_handler(
        CallbackQueryHandler(rating_callback, pattern=r"^rate_")
    )

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

    # Group message filter (spam, scam, flood, +XP)
    # This must be last — it processes all non-command text messages
    app.add_handler(
        MessageHandler(
            filters.ALL
            & ~filters.COMMAND
            & ~filters.StatusUpdate.ALL,
            group_message_filter,
        )
    )

    logger.info("Message handlers registered")
