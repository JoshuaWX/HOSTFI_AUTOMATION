"""
Module: main.py
Purpose: FastAPI entry point — webhook setup, bot initialization, graceful shutdown
Author: HOSTFI Bot Team
"""

import asyncio
import logging
import signal
from contextlib import asynccontextmanager

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

# Application instance (initialised on startup)
_bot_app = None


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown logic for the FastAPI application."""
    global _bot_app

    # ---- Startup ----
    logger.info("Starting HOSTFI Bot...")

    _bot_app = build_application()
    await _bot_app.initialize()

    webhook_url = f"{WEBHOOK_URL}/webhook"
    await _bot_app.bot.set_webhook(
        url=webhook_url,
        secret_token=TELEGRAM_WEBHOOK_SECRET,
        allowed_updates=Update.ALL_TYPES,
    )
    logger.info("Webhook set: %s", webhook_url)

    setup_scheduler(_bot_app)

    # Auto-index knowledge base if ChromaDB is empty (Railway wipes filesystem on redeploy)
    try:
        from rag.ingestion import get_collection, run_ingestion

        collection = get_collection()
        if collection.count() == 0:
            logger.info("Knowledge base is empty — auto-indexing from local files...")
            summary = await run_ingestion()
            logger.info("Auto-indexing complete: %s", summary)
        else:
            logger.info("Knowledge base has %d chunks — skipping auto-index", collection.count())
    except Exception as exc:
        logger.warning("Auto-indexing failed (non-fatal): %s", exc)

    logger.info("HOSTFI Bot started successfully")

    yield

    # ---- Shutdown ----
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
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="HOSTFI Bot",
    description="HOSTFI Telegram Community Management & AI Support Bot",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)


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
