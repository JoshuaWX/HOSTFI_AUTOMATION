"""
Module: guardrails.py
Purpose: Confidence threshold checks, topic boundary validation, and
         emergency keyword detection for the RAG AI pipeline
Author: HOSTFI Bot Team
"""

import logging
import re
from typing import NamedTuple

from config import RAG_CONFIDENCE_THRESHOLD
from rag.retriever import RetrievalResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EMERGENCY_KEYWORDS: list[str] = [
    "hacked",
    "scammed",
    "lost funds",
    "stolen",
    "lost my money",
    "someone stole",
    "account hacked",
    "wallet hacked",
    "funds missing",
    "unauthorised transaction",
    "unauthorized transaction",
    "phished",
]

EMERGENCY_PATTERN: re.Pattern = re.compile(
    "|".join(re.escape(kw) for kw in EMERGENCY_KEYWORDS),
    re.IGNORECASE,
)

OFF_TOPIC_KEYWORDS: list[str] = [
    "invest in",
    "should i buy",
    "price prediction",
    "will it go up",
    "moon",
    "when lambo",
    "guaranteed profit",
    "best coin to buy",
    "financial advice",
    "stock tips",
]

FEE_RATE_KEYWORDS: list[str] = [
    "fee",
    "rate",
    "charge",
    "cost",
    "price",
    "percentage",
    "commission",
    "spread",
]

FALLBACK_MESSAGE: str = (
    "I don't have enough information to answer that accurately. "
    "Please contact HOSTFI support directly or visit the app."
)

EMERGENCY_MESSAGE: str = (
    "⚠️ This sounds urgent. Please contact HOSTFI support immediately "
    "via the app. Do not share your details in this chat."
)

FEE_DISCLAIMER: str = (
    "\n\n<i>(Please confirm current rates in the HOSTFI app "
    "as these may change)</i>"
)

FEE_DISCLAIMER_PLAIN: str = (
    "please confirm current rates in the hostfi app as these may change"
)

OFF_TOPIC_MESSAGE: str = (
    "I can only help with questions about HOSTFI and its services. "
    "For other topics, please check relevant resources elsewhere."
)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


class GuardrailResult(NamedTuple):
    """Result of a guardrail check."""

    proceed: bool  # True = safe to call AI; False = return message directly
    message: str  # Pre-built message to send if proceed is False
    is_emergency: bool  # If True, admin channel should be pinged


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_emergency(question: str) -> GuardrailResult | None:
    """
    Detect emergency keywords indicating the user may be in distress.

    Args:
        question: Raw user question text

    Returns:
        GuardrailResult if an emergency is detected, None otherwise
    """
    if EMERGENCY_PATTERN.search(question):
        logger.warning("Emergency keyword detected in question: %s", question[:80])
        return GuardrailResult(
            proceed=False,
            message=EMERGENCY_MESSAGE,
            is_emergency=True,
        )
    return None


def check_off_topic(question: str) -> GuardrailResult | None:
    """
    Detect questions seeking financial advice or unrelated topics.

    Args:
        question: Raw user question text

    Returns:
        GuardrailResult if off-topic detected, None otherwise
    """
    lower = question.lower()
    for keyword in OFF_TOPIC_KEYWORDS:
        if keyword in lower:
            logger.info("Off-topic keyword detected: '%s'", keyword)
            return GuardrailResult(
                proceed=False,
                message=OFF_TOPIC_MESSAGE,
                is_emergency=False,
            )
    return None


def check_confidence(
    results: list[RetrievalResult],
    threshold: float | None = None,
) -> GuardrailResult | None:
    """
    Verify that the top retrieval result exceeds the confidence threshold.

    If no results are returned or the best score is below the threshold,
    a fallback message is returned and the AI should NOT be called.

    Args:
        results: Retrieval results from ChromaDB
        threshold: Minimum similarity score (default from config)

    Returns:
        GuardrailResult if confidence is too low, None if OK to proceed
    """
    if threshold is None:
        threshold = RAG_CONFIDENCE_THRESHOLD

    if not results:
        logger.info("No retrieval results — confidence check failed")
        return GuardrailResult(
            proceed=False,
            message=FALLBACK_MESSAGE,
            is_emergency=False,
        )

    best_score = results[0].score
    if best_score < threshold:
        logger.info(
            "Low confidence: best_score=%.4f < threshold=%.4f",
            best_score,
            threshold,
        )
        return GuardrailResult(
            proceed=False,
            message=FALLBACK_MESSAGE,
            is_emergency=False,
        )

    return None


def should_append_disclaimer(question: str, answer: str) -> bool:
    """
    Determine whether the fee/rate disclaimer should be appended to
    the AI response.

    Args:
        question: Original user question
        answer: AI-generated answer

    Returns:
        True if the answer or question references fees/rates
    """
    answer_lower = answer.lower()
    if FEE_DISCLAIMER_PLAIN in answer_lower:
        return False

    combined = (question + " " + answer).lower()
    return any(kw in combined for kw in FEE_RATE_KEYWORDS)


# ---------------------------------------------------------------------------
# Aggregate runner
# ---------------------------------------------------------------------------


def run_guardrails(
    question: str,
    results: list[RetrievalResult],
) -> GuardrailResult | None:
    """
    Run all guardrail checks in sequence.

    Returns the first failing GuardrailResult, or None if all checks pass
    (meaning it is safe to proceed with the AI call).

    Check order:
    1. Emergency keywords (highest priority — skip everything, ping admin)
    2. Off-topic detection
    3. Confidence threshold

    Args:
        question: Raw user question
        results: Retrieval results from ChromaDB

    Returns:
        GuardrailResult if a check fails, None if safe to proceed
    """
    # 1. Emergency check
    emergency = check_emergency(question)
    if emergency is not None:
        return emergency

    # 2. Off-topic check
    off_topic = check_off_topic(question)
    if off_topic is not None:
        return off_topic

    # 3. Confidence check
    confidence = check_confidence(results)
    if confidence is not None:
        return confidence

    return None
