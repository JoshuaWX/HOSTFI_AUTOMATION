"""
Module: support.py
Purpose: /ask command handler — RAG pipeline: question → retrieve → guardrails → AI → respond
Author: HOSTFI Bot Team
"""

import html
import logging
import time

from telegram import Update
from telegram.ext import ContextTypes

from bot.utils.rate_limiter import check_rate_limit
from config import ADMIN_CHANNEL_ID
from database.logs import log_action
from rag.ai_engine import generate_answer
from rag.guardrails import (
    FEE_DISCLAIMER,
    run_guardrails,
    should_append_disclaimer,
)
from rag.retriever import build_context, retrieve

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# /ask command
# ---------------------------------------------------------------------------


async def ask_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle the /ask command — the main RAG AI support entry point.

    Pipeline:
    1. Parse and validate the user question
    2. Rate-limit check (5 queries per user per hour)
    3. Retrieve top-3 knowledge chunks from ChromaDB
    4. Run guardrails (emergency → off-topic → confidence)
    5. If guardrails pass: call Groq API with context + question
    6. Optionally append fee/rate disclaimer
    7. Log query to Supabase for quality monitoring

    Args:
        update: Incoming Telegram update
        context: Bot context
    """
    user = update.effective_user
    if not user or not update.message:
        return

    total_start = time.perf_counter()

    # --- 1. Extract question ------------------------------------------------
    question = " ".join(context.args) if context.args else ""

    if not question.strip():
        await update.message.reply_text(
            "💡 <b>Usage:</b> <code>/ask your question here</code>\n\n"
            "Example: <code>/ask How do I fund my virtual card?</code>",
            parse_mode="HTML",
        )
        return

    # --- 2. Rate limit (5 per hour) -----------------------------------------
    allowed = await check_rate_limit(
        user.id, action="ai_query", limit=5, window=3600
    )
    if not allowed:
        await update.message.reply_text(
            "⏳ You've reached the AI query limit (5 per hour). "
            "Please try again later."
        )
        return

    # Show typing indicator while processing
    chat_action_start = time.perf_counter()
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action="typing"
    )
    chat_action_ms = (time.perf_counter() - chat_action_start) * 1000

    try:
        # --- 3. Retrieve relevant chunks ------------------------------------
        retrieve_start = time.perf_counter()
        results = await retrieve(question, top_k=3)
        retrieve_ms = (time.perf_counter() - retrieve_start) * 1000

        # --- 4. Guardrail checks --------------------------------------------
        guardrail_start = time.perf_counter()
        guardrail = run_guardrails(question, results)
        guardrail_ms = (time.perf_counter() - guardrail_start) * 1000

        if guardrail is not None:
            if guardrail.is_emergency:
                # Alert admin channel about the emergency
                safe_question = html.escape(question[:300])
                alert_text = (
                    f"🚨 <b>Emergency Escalation</b>\n\n"
                    f"👤 User: {html.escape(user.first_name or str(user.id))}\n"
                    f"🆔 ID: <code>{user.id}</code>\n"
                    f"💬 Message: {safe_question}"
                )
                try:
                    await context.bot.send_message(
                        chat_id=ADMIN_CHANNEL_ID,
                        text=alert_text,
                        parse_mode="HTML",
                    )
                except Exception as exc:
                    logger.error(
                        "Failed to send emergency alert: %s", exc
                    )

            await update.message.reply_text(
                guardrail.message, parse_mode="HTML"
            )

            total_ms = (time.perf_counter() - total_start) * 1000
            logger.info(
                "AI latency breakdown (guardrail) user=%s total=%.1fms "
                "chat_action=%.1fms retrieve=%.1fms guardrails=%.1fms",
                user.id,
                total_ms,
                chat_action_ms,
                retrieve_ms,
                guardrail_ms,
            )

            # Log the query as handled by guardrails
            await log_action(
                action="ai_query",
                admin_telegram_id=0,
                target_telegram_id=user.id,
                reason="guardrail_intercepted",
                metadata={
                    "question": question[:500],
                    "emergency": guardrail.is_emergency,
                    "resolved_by_ai": False,
                },
            )
            return

        # --- 5. Generate AI answer -------------------------------------------
        context_start = time.perf_counter()
        kb_context = build_context(results)
        context_ms = (time.perf_counter() - context_start) * 1000

        generate_start = time.perf_counter()
        answer = await generate_answer(kb_context, question)
        generate_ms = (time.perf_counter() - generate_start) * 1000

        # --- 6. Append disclaimer if answer mentions fees/rates ---------------
        disclaimer_start = time.perf_counter()
        if should_append_disclaimer(question, answer):
            answer += FEE_DISCLAIMER
        disclaimer_ms = (time.perf_counter() - disclaimer_start) * 1000

        # Format final response
        response = (
            f"🤖 <b>HOSTFI Assistant</b>\n\n"
            f"{html.escape(answer)}"
        )

        # Handle potential Telegram message length limit (4096 chars)
        if len(response) > 4000:
            response = response[:3997] + "..."

        reply_start = time.perf_counter()
        await update.message.reply_text(response, parse_mode="HTML")
        reply_ms = (time.perf_counter() - reply_start) * 1000

        # --- 7. Log successful AI query --------------------------------------
        top_score = results[0].score if results else 0.0
        await log_action(
            action="ai_query",
            admin_telegram_id=0,
            target_telegram_id=user.id,
            reason="ai_resolved",
            metadata={
                "question": question[:500],
                "top_score": top_score,
                "resolved_by_ai": True,
                "sources": [r.source for r in results],
            },
        )

        logger.info(
            "AI query resolved for user %s (score=%.4f): %s",
            user.id,
            top_score,
            question[:80],
        )

        total_ms = (time.perf_counter() - total_start) * 1000
        logger.info(
            "AI latency breakdown user=%s total=%.1fms chat_action=%.1fms "
            "retrieve=%.1fms guardrails=%.1fms context=%.1fms "
            "generate=%.1fms disclaimer=%.1fms reply=%.1fms",
            user.id,
            total_ms,
            chat_action_ms,
            retrieve_ms,
            guardrail_ms,
            context_ms,
            generate_ms,
            disclaimer_ms,
            reply_ms,
        )

    except Exception as exc:
        logger.error(
            "AI support handler error for user %s: %s", user.id, exc
        )
        await update.message.reply_text(
            "❌ Sorry, I encountered an error processing your question. "
            "Please try again or contact HOSTFI support directly."
        )

        await log_action(
            action="ai_query",
            admin_telegram_id=0,
            target_telegram_id=user.id,
            reason="error",
            metadata={
                "question": question[:500],
                "error": str(exc)[:200],
                "resolved_by_ai": False,
            },
        )
