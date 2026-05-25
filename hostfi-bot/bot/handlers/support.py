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

from bot.utils.auto_delete import (
    schedule_any_delete,
    schedule_command_delete,
    send_dm_redirect_status,
)
from bot.utils.formatter import field, status_text, title
from bot.utils.rate_limiter import check_rate_limit
from config import ADMIN_CHANNEL_ID
from database.dm_conversations import (
    get_active_session_id,
    get_recent_dm_messages,
    save_dm_message,
)
from database.logs import log_action
from rag.ai_engine import generate_answer
from rag.guardrails import (
    FEE_DISCLAIMER,
    run_guardrails,
    should_append_disclaimer,
)
from rag.retriever import build_context, retrieve

logger = logging.getLogger(__name__)


def _format_conversation_history(messages: list[dict]) -> str:
    """
    Format a list of dm_conversation messages into a readable string.

    Args:
        messages: List of message dicts with keys: message_role, message_content

    Returns:
        Formatted conversation string (empty if no messages)
    """
    if not messages:
        return ""

    lines = ["[CONVERSATION HISTORY]"]
    for msg in messages:
        role = "You" if msg["message_role"] == "user" else "Assistant"
        lines.append(f"{role}: {msg['message_content']}")
    lines.append("[END HISTORY]\n")

    return "\n".join(lines)


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
    3. If DM: retrieve recent conversation history for session context
    4. Retrieve top-3 knowledge chunks from ChromaDB
    5. Run guardrails (emergency → off-topic → confidence)
    6. If guardrails pass: call Gemini API with context + question (+history if DM)
    7. Optionally append fee/rate disclaimer
    8. If DM: save question and response to conversation history
    9. Log query to Supabase for quality monitoring

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
    is_dm = update.effective_chat.type == "private"

    if not question.strip():
        msg = await update.message.reply_text(
            "<b>Usage</b>\n"
            "<code>/ask your question here</code>\n\n"
            "Example: <code>/ask How do I fund my virtual card?</code>",
            parse_mode="HTML",
        )
        await schedule_any_delete(msg, context, 15)
        await schedule_command_delete(update, context, 15)
        return

    response_chat_id = update.effective_chat.id
    if not is_dm:
        response_chat_id = user.id
        try:
            await context.bot.send_message(
                chat_id=user.id,
                text=status_text("info", "I’ll answer your question here in DM."),
            )
        except Exception:
            await send_dm_redirect_status(update, context, dm_sent=False)
            return
        await send_dm_redirect_status(update, context, dm_sent=True)

    async def send_response(text: str, parse_mode: str | None = None) -> None:
        await context.bot.send_message(
            chat_id=response_chat_id,
            text=text,
            parse_mode=parse_mode,
        )

    # --- 2. Rate limit (5 per hour) -----------------------------------------
    allowed = await check_rate_limit(
        user.id, action="ai_query", limit=5, window=3600
    )
    if not allowed:
        await send_response(
            status_text("warning", "You've reached the AI query limit of 5 per hour. Please try again later.")
        )
        return

    # Show typing indicator while processing
    chat_action_start = time.perf_counter()
    await context.bot.send_chat_action(
        chat_id=response_chat_id, action="typing"
    )
    chat_action_ms = (time.perf_counter() - chat_action_start) * 1000

    # --- 3. Check if DM and fetch conversation history ----------------------
    session_id = None
    conversation_history = ""

    if is_dm:
        session_id = await get_active_session_id(user.id)
        recent_messages = await get_recent_dm_messages(user.id, session_id, limit=4)
        conversation_history = _format_conversation_history(recent_messages)

    try:
        # --- 4. Retrieve relevant chunks ------------------------------------
        retrieve_start = time.perf_counter()
        results = await retrieve(question, top_k=3)
        retrieve_ms = (time.perf_counter() - retrieve_start) * 1000

        # --- 5. Guardrail checks --------------------------------------------
        guardrail_start = time.perf_counter()
        guardrail = run_guardrails(question, results)
        guardrail_ms = (time.perf_counter() - guardrail_start) * 1000

        if guardrail is not None:
            if guardrail.is_emergency:
                # Alert admin channel about the emergency
                safe_question = html.escape(question[:300])
                alert_text = "\n".join(
                    [
                        title("Emergency Escalation", "🚨"),
                        "",
                        field("User", html.escape(user.first_name or str(user.id))),
                        field("ID", f"<code>{user.id}</code>"),
                        field("Message", safe_question),
                    ]
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

            await send_response(guardrail.message, parse_mode="HTML")

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

        # --- 6. Generate AI answer -------------------------------------------
        context_start = time.perf_counter()
        kb_context = build_context(results)
        context_ms = (time.perf_counter() - context_start) * 1000

        generate_start = time.perf_counter()
        answer = await generate_answer(
            kb_context, question, conversation_history=conversation_history
        )
        generate_ms = (time.perf_counter() - generate_start) * 1000

        # --- 7. Append disclaimer if answer mentions fees/rates ---------------
        disclaimer_start = time.perf_counter()
        if should_append_disclaimer(question, answer):
            answer += FEE_DISCLAIMER
        disclaimer_ms = (time.perf_counter() - disclaimer_start) * 1000

        # Format final response
        response = f"{title('HOSTFI Assistant', '🤖')}\n\n{html.escape(answer)}"

        # Handle potential Telegram message length limit (4096 chars)
        if len(response) > 4000:
            response = response[:3997] + "..."

        reply_start = time.perf_counter()
        await send_response(response, parse_mode="HTML")
        reply_ms = (time.perf_counter() - reply_start) * 1000

        # --- 8. Save DM conversation history if in private chat ---------------
        if is_dm and session_id:
            await save_dm_message(user.id, session_id, "user", question)
            await save_dm_message(user.id, session_id, "assistant", answer)

        # --- 9. Log successful AI query --------------------------------------
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
        await send_response(
            status_text("error", "Sorry, I encountered an error processing your question. Please try again or contact HOSTFI support directly.")
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
