"""
Module: ai_engine.py
Purpose: Build RAG prompt with retrieved context and call Gemini API for
         grounded answers using httpx (async)
Author: HOSTFI Bot Team
"""

import html
import logging

import httpx

from config import GEMINI_API_KEY, GEMINI_MODEL

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_TIMEOUT = 30.0
GEMINI_MAX_TOKENS = 1024

SYSTEM_PROMPT = """
You are the official support assistant for HOSTFI, a crypto-fintech platform.

STRICT RULES — YOU MUST FOLLOW THESE WITHOUT EXCEPTION:
1. ONLY answer questions using the context provided below. Do not use any other knowledge.
2. If the context does not contain enough information to answer confidently, respond EXACTLY with: \
"I don't have enough information to answer that accurately. Please contact HOSTFI support directly or visit the app."
3. NEVER provide investment advice, price predictions, or financial recommendations.
4. NEVER make up fees, rates, limits, or any numerical values not explicitly in the context.
5. If a user mentions being hacked, losing funds, or being scammed, respond EXACTLY with: \
"⚠️ This sounds urgent. Please contact HOSTFI support immediately via the app. Do not share your details in this chat."
6. Keep responses concise — maximum 3 paragraphs.
7. Respond only about HOSTFI. Politely decline all off-topic questions.
8. If conversation history is provided, use it for context to understand follow-ups, but always prioritize \
the knowledge base context provided.

{conversation_section}

CONTEXT FROM KNOWLEDGE BASE:
{context}

USER QUESTION: {question}
"""


# ---------------------------------------------------------------------------
# Core API call
# ---------------------------------------------------------------------------


async def generate_answer(
    context: str, question: str, conversation_history: str = ""
) -> str:
    """
    Call the Gemini API with the RAG system prompt, context, and user
    question. Optionally includes DM conversation history for follow-ups.

    All user input is HTML-escaped before insertion into the prompt
    to prevent injection.

    Args:
        context: Concatenated knowledge-base chunks from the retriever
        question: Sanitised user question text
        conversation_history: Optional previous messages in format "[CONVERSATION HISTORY]\nYou: ...\nAssistant: ...\n[END HISTORY]\n"

    Returns:
        AI-generated answer string (plain text)

    Raises:
        httpx.HTTPStatusError: On non-2xx response from Gemini
        httpx.TimeoutException: If the request times out
    """
    safe_question = html.escape(question)

    # Build conversation section if history is provided
    conversation_section = (
        conversation_history if conversation_history else ""
    )

    filled_prompt = SYSTEM_PROMPT.format(
        conversation_section=conversation_section,
        context=context,
        question=safe_question,
    )

    payload = {
        "systemInstruction": {
            "parts": [{"text": filled_prompt}],
        },
        "contents": [
            {
                "role": "user",
                "parts": [{"text": safe_question}],
            }
        ],
        "generationConfig": {
            "temperature": 0.3,
            "topP": 0.9,
            "maxOutputTokens": GEMINI_MAX_TOKENS,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=GEMINI_TIMEOUT) as client:
            response = await client.post(
                GEMINI_API_URL.format(model=GEMINI_MODEL),
                params={"key": GEMINI_API_KEY},
                json=payload,
            )
            response.raise_for_status()

        data = response.json()
        candidates = data.get("candidates") or []
        parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
        answer = "".join(part.get("text", "") for part in parts).strip()
        if not answer:
            raise RuntimeError("Gemini returned an empty response")

        logger.info(
            "Gemini API call successful — model=%s tokens=%s",
            GEMINI_MODEL,
            data.get("usageMetadata", {}).get("totalTokenCount", "?"),
        )
        return answer

    except httpx.HTTPStatusError as exc:
        logger.error(
            "Gemini API HTTP error %s: %s",
            exc.response.status_code,
            exc.response.text[:200],
        )
        raise
    except httpx.TimeoutException:
        logger.error("Gemini API request timed out after %ss", GEMINI_TIMEOUT)
        raise
    except Exception as exc:
        logger.error("Unexpected error calling Gemini API: %s", exc)
        raise
