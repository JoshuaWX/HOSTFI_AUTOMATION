"""
Module: main.py
Purpose: FastAPI entry point — webhook setup, bot initialization, graceful shutdown
Author: HOSTFI Bot Team
"""

import asyncio
import logging
import signal

import uvicorn
from fastapi import FastAPI, Request, Response
from telegram import Update

from bot.application import build_application
from config import (
    PORT,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_WEBHOOK_SECRET,
    WEBHOOK_URL,
    setup_logging,
)
from scheduler.tasks import setup_scheduler, shutdown_scheduler

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
setup_logging()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="HOSTFI Bot",
    description="HOSTFI Telegram Community Management & AI Support Bot",
    docs_url=None,
    redoc_url=None,
)

# Application instance (initialised on startup)
_bot_app = None


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@app.get("/health")
async def health_check():
    """Health check endpoint for Railway / monitoring."""
    return {"status": "ok", "bot": "running"}


# ---------------------------------------------------------------------------
# Webhook endpoint
# ---------------------------------------------------------------------------


@app.post("/webhook")
async def webhook_handler(request: Request) -> Response:
    """
    Receive Telegram webhook updates.

    Validates the secret token header before processing the update.
    """
    # Verify webhook secret
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if secret != TELEGRAM_WEBHOOK_SECRET:
        logger.warning("Webhook request with invalid secret token")
        return Response(status_code=403)

    try:
        data = await request.json()
        update = Update.de_json(data, _bot_app.bot)
        await _bot_app.process_update(update)
    except Exception as exc:
        logger.error("Error processing webhook update: %s", exc)

    return Response(status_code=200)


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------


@app.on_event("startup")
async def on_startup():
    """
    Initialize the bot application, set the webhook, and start the scheduler.
    """
    global _bot_app

    logger.info("Starting HOSTFI Bot...")

    # Build the bot application with all handlers
    _bot_app = build_application()
    await _bot_app.initialize()

    # Set the webhook
    webhook_url = f"{WEBHOOK_URL}/webhook"
    await _bot_app.bot.set_webhook(
        url=webhook_url,
        secret_token=TELEGRAM_WEBHOOK_SECRET,
        allowed_updates=Update.ALL_TYPES,
    )
    logger.info("Webhook set: %s", webhook_url)

    # Start the scheduler
    setup_scheduler(_bot_app)

    logger.info("HOSTFI Bot started successfully")


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------


@app.on_event("shutdown")
async def on_shutdown():
    """Gracefully shut down the bot, scheduler, and webhook."""
    global _bot_app

    logger.info("Shutting down HOSTFI Bot...")

    shutdown_scheduler()

    if _bot_app:
        try:
            await _bot_app.bot.delete_webhook()
            logger.info("Webhook deleted")
        except Exception as exc:
            logger.error("Error deleting webhook: %s", exc)

        try:
            await _bot_app.shutdown()
            logger.info("Bot application shut down")
        except Exception as exc:
            logger.error("Error shutting down bot: %s", exc)

    logger.info("HOSTFI Bot shutdown complete")


# ---------------------------------------------------------------------------
# Signal handlers for graceful SIGTERM (Railway / Docker)
# ---------------------------------------------------------------------------


def _handle_sigterm(signum, frame):
    """Handle SIGTERM for graceful shutdown."""
    logger.info("Received SIGTERM — initiating graceful shutdown")
    raise SystemExit(0)


signal.signal(signal.SIGTERM, _handle_sigterm)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=PORT,
        log_level="info",
    )
