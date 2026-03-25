"""
Module: ai_engine.py
Purpose: Build RAG prompt with retrieved context and call Groq API for
         grounded answers using httpx (async)
Author: HOSTFI Bot Team
"""

import html
import logging

import httpx

from config import GROQ_API_KEY

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.1-8b-instant"
GROQ_TIMEOUT = 30.0
GROQ_MAX_TOKENS = 1024

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

CONTEXT FROM KNOWLEDGE BASE:
{context}

USER QUESTION: {question}
"""


# ---------------------------------------------------------------------------
# Core API call
# ---------------------------------------------------------------------------


async def generate_answer(context: str, question: str) -> str:
    """
    Call the Groq API with the RAG system prompt, context, and user
    question.

    All user input is HTML-escaped before insertion into the prompt
    to prevent injection.

    Args:
        context: Concatenated knowledge-base chunks from the retriever
        question: Sanitised user question text

    Returns:
        AI-generated answer string (plain text)

    Raises:
        httpx.HTTPStatusError: On non-2xx response from Groq
        httpx.TimeoutException: If the request times out
    """
    safe_question = html.escape(question)

    filled_prompt = SYSTEM_PROMPT.format(
        context=context,
        question=safe_question,
    )

    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": filled_prompt},
            {"role": "user", "content": safe_question},
        ],
        "temperature": 0.3,
        "max_tokens": GROQ_MAX_TOKENS,
        "top_p": 0.9,
    }

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=GROQ_TIMEOUT) as client:
            response = await client.post(
                GROQ_API_URL,
                json=payload,
                headers=headers,
            )
            response.raise_for_status()

        data = response.json()
        answer = data["choices"][0]["message"]["content"].strip()

        logger.info(
            "Groq API call successful — model=%s tokens=%s",
            GROQ_MODEL,
            data.get("usage", {}).get("total_tokens", "?"),
        )
        return answer

    except httpx.HTTPStatusError as exc:
        logger.error(
            "Groq API HTTP error %s: %s",
            exc.response.status_code,
            exc.response.text[:200],
        )
        raise
    except httpx.TimeoutException:
        logger.error("Groq API request timed out after %ss", GROQ_TIMEOUT)
        raise
    except Exception as exc:
        logger.error("Unexpected error calling Groq API: %s", exc)
        raise
