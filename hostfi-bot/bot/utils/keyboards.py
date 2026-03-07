"""
Module: keyboards.py
Purpose: InlineKeyboardMarkup builders for all bot interaction flows
Author: HOSTFI Bot Team
"""

import logging
import random

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Welcome / onboarding
# ---------------------------------------------------------------------------


def welcome_keyboard() -> InlineKeyboardMarkup:
    """
    Build the post-verification welcome CTA keyboard.

    Returns:
        InlineKeyboardMarkup with HOSTFI app link, rules, and help buttons
    """
    buttons = [
        [
            InlineKeyboardButton(
                "📲 Download HOSTFI App",
                url="https://hostfi.io",
            ),
        ],
        [
            InlineKeyboardButton(
                "📖 Community Rules",
                callback_data="show_rules",
            ),
            InlineKeyboardButton(
                "❓ Get Help",
                callback_data="show_help",
            ),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


# ---------------------------------------------------------------------------
# Verification CAPTCHA
# ---------------------------------------------------------------------------


def generate_captcha_options(correct: int) -> list[int]:
    """
    Generate four unique numeric options including the correct answer.

    Args:
        correct: The correct answer value

    Returns:
        List of four unique positive integers containing the correct answer
    """
    options: set[int] = {correct}
    attempts = 0
    while len(options) < 4 and attempts < 50:
        offset = random.choice([-3, -2, -1, 1, 2, 3, 4, 5])
        candidate = correct + offset
        if candidate > 0:
            options.add(candidate)
        attempts += 1
    return list(options)


def verification_keyboard(
    user_id: int, correct_answer: int, options: list[int]
) -> InlineKeyboardMarkup:
    """
    Build a CAPTCHA-style inline keyboard with shuffled answer options.

    Each button's callback_data encodes the user ID and selected answer
    in the format ``captcha_{user_id}_{answer}``.

    Args:
        user_id: Telegram user ID the CAPTCHA belongs to
        correct_answer: The correct numeric answer (unused directly,
                        present in *options*)
        options: List of numeric options (must include correct_answer)

    Returns:
        InlineKeyboardMarkup with answer buttons in one row
    """
    shuffled = list(options)
    random.shuffle(shuffled)
    buttons = [
        InlineKeyboardButton(
            str(opt),
            callback_data=f"captcha_{user_id}_{opt}",
        )
        for opt in shuffled
    ]
    return InlineKeyboardMarkup([buttons])


# ---------------------------------------------------------------------------
# Support tickets
# ---------------------------------------------------------------------------


def ticket_keyboard(ticket_id: str) -> InlineKeyboardMarkup:
    """
    Build the admin ticket-claim keyboard.

    Args:
        ticket_id: Formatted ticket ID (e.g. "HSTF-0001")

    Returns:
        InlineKeyboardMarkup with a Claim Ticket button
    """
    buttons = [
        [
            InlineKeyboardButton(
                "🎫 Claim Ticket",
                callback_data=f"ticket_claim_{ticket_id}",
            ),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


def rating_keyboard(ticket_id: str) -> InlineKeyboardMarkup:
    """
    Build a 1–5 star rating keyboard for resolved tickets.

    Args:
        ticket_id: Formatted ticket ID

    Returns:
        InlineKeyboardMarkup with five star-rating buttons
    """
    buttons = [
        InlineKeyboardButton(
            f"{'⭐' * i}",
            callback_data=f"rate_{ticket_id}_{i}",
        )
        for i in range(1, 6)
    ]
    return InlineKeyboardMarkup([buttons])


# ---------------------------------------------------------------------------
# Rules / community
# ---------------------------------------------------------------------------


def rules_keyboard() -> InlineKeyboardMarkup:
    """
    Build a keyboard with a community-rules acknowledgement button.

    Returns:
        InlineKeyboardMarkup with an "I understand" button
    """
    buttons = [
        [
            InlineKeyboardButton(
                "✅ I understand",
                callback_data="rules_acknowledged",
            ),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


# ---------------------------------------------------------------------------
# Broadcast confirmation
# ---------------------------------------------------------------------------


def confirm_broadcast_keyboard(broadcast_id: str) -> InlineKeyboardMarkup:
    """
    Build a confirmation keyboard for pending broadcast messages.

    Args:
        broadcast_id: Unique identifier for the pending broadcast

    Returns:
        InlineKeyboardMarkup with confirm/cancel buttons
    """
    buttons = [
        [
            InlineKeyboardButton(
                "✅ Send Now",
                callback_data=f"broadcast_confirm_{broadcast_id}",
            ),
            InlineKeyboardButton(
                "❌ Cancel",
                callback_data=f"broadcast_cancel_{broadcast_id}",
            ),
        ],
    ]
    return InlineKeyboardMarkup(buttons)
