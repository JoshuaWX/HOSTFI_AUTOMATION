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
                "Download App",
                url="https://hostfi.io",
            ),
        ],
        [
            InlineKeyboardButton(
                "Rules",
                callback_data="show_rules",
            ),
            InlineKeyboardButton(
                "Help",
                callback_data="show_help",
            ),
        ],
        [
            InlineKeyboardButton(
                "XP Campaign",
                callback_data="campaign_home",
            ),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


# ---------------------------------------------------------------------------
# Campaign XP
# ---------------------------------------------------------------------------


def campaign_home_keyboard() -> InlineKeyboardMarkup:
    """Build the private user dashboard keyboard."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("My XP", callback_data="campaign_xp"),
                InlineKeyboardButton("Earn XP", callback_data="campaign_earn"),
            ],
            [
                InlineKeyboardButton("Invite Friends", callback_data="campaign_invite"),
                InlineKeyboardButton("My Invites", callback_data="campaign_invites"),
            ],
            [
                InlineKeyboardButton("Active Raids", callback_data="campaign_raids"),
                InlineKeyboardButton("Submit X Post", callback_data="campaign_xpost_start"),
            ],
            [
                InlineKeyboardButton("Link X", callback_data="campaign_xlink_start"),
                InlineKeyboardButton("Support", callback_data="campaign_support"),
            ],
            [
                InlineKeyboardButton("Leaderboard", callback_data="campaign_leaderboard"),
                InlineKeyboardButton("Rules", callback_data="campaign_rules"),
            ],
        ]
    )


def campaign_group_keyboard() -> InlineKeyboardMarkup:
    """Build the public community campaign panel keyboard."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Leaderboard", callback_data="campaign_leaderboard"),
                InlineKeyboardButton("Active Raids", callback_data="campaign_raids"),
            ],
            [
                InlineKeyboardButton("My XP", callback_data="campaign_xp"),
                InlineKeyboardButton("Open DM Dashboard", callback_data="campaign_dm_dashboard"),
            ],
        ]
    )


def campaign_earn_keyboard() -> InlineKeyboardMarkup:
    """Build the guided earning-path keyboard."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Invite Friends", callback_data="campaign_invite"),
                InlineKeyboardButton("Active Raids", callback_data="campaign_raids"),
            ],
            [
                InlineKeyboardButton("Submit X Post", callback_data="campaign_xpost_start"),
                InlineKeyboardButton("Link X", callback_data="campaign_xlink_start"),
            ],
            [
                InlineKeyboardButton("My XP", callback_data="campaign_xp"),
                InlineKeyboardButton("Back", callback_data="campaign_home"),
            ],
        ]
    )


def admin_dashboard_keyboard(is_superadmin: bool = False) -> InlineKeyboardMarkup:
    """Build the main admin dashboard keyboard."""
    buttons = [
        [
            InlineKeyboardButton("Tickets", callback_data="admin_tickets"),
            InlineKeyboardButton("X Reviews", callback_data="admin_xposts"),
        ],
        [
            InlineKeyboardButton("Campaign", callback_data="admin_campaign"),
            InlineKeyboardButton("Raids", callback_data="admin_raids"),
        ],
        [
            InlineKeyboardButton("XP Tools", callback_data="admin_xp"),
            InlineKeyboardButton("Broadcasts", callback_data="admin_broadcasts"),
        ],
        [
            InlineKeyboardButton("Stats", callback_data="admin_stats"),
            InlineKeyboardButton("Moderation", callback_data="admin_moderation"),
        ],
    ]
    if is_superadmin:
        buttons.append([InlineKeyboardButton("System", callback_data="admin_system")])
    return InlineKeyboardMarkup(buttons)


def admin_back_keyboard() -> InlineKeyboardMarkup:
    """Build a simple back-to-admin-dashboard keyboard."""
    return InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="admin_home")]])


def admin_campaign_keyboard(is_superadmin: bool = False) -> InlineKeyboardMarkup:
    """Build campaign admin action buttons."""
    buttons = [
        [
            InlineKeyboardButton("Active Raids", callback_data="admin_raids"),
            InlineKeyboardButton("Leaderboard", callback_data="admin_campaign_leaderboard"),
        ],
        [InlineKeyboardButton("Back", callback_data="admin_home")],
    ]
    if is_superadmin:
        buttons.insert(
            0,
            [
                InlineKeyboardButton("Start Cycle", callback_data="admin_cycle_start_preview"),
                InlineKeyboardButton("Finish Cycle", callback_data="admin_cycle_finish_preview"),
            ],
        )
    return InlineKeyboardMarkup(buttons)


def admin_confirm_keyboard(confirm_data: str, cancel_data: str = "admin_home") -> InlineKeyboardMarkup:
    """Build a confirmation keyboard for dangerous admin actions."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Confirm", callback_data=confirm_data),
                InlineKeyboardButton("Cancel", callback_data=cancel_data),
            ],
        ]
    )


def admin_system_keyboard() -> InlineKeyboardMarkup:
    """Build superadmin system action buttons."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Config Health", callback_data="admin_config_health"),
                InlineKeyboardButton("API Status", callback_data="admin_api_status"),
            ],
            [
                InlineKeyboardButton("Reindex", callback_data="admin_reindex_preview"),
                InlineKeyboardButton("Back", callback_data="admin_home"),
            ],
        ]
    )


def campaign_cancel_keyboard() -> InlineKeyboardMarkup:
    """Build a cancel button for guided campaign replies."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Cancel", callback_data="campaign_cancel")]]
    )


def campaign_xverify_keyboard() -> InlineKeyboardMarkup:
    """Build the button shown after an X verification code is generated."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Posted Code", callback_data="campaign_xverify_start")],
            [InlineKeyboardButton("Cancel", callback_data="campaign_cancel")],
        ]
    )


def campaign_raid_keyboard(raid_id: int, target_url: str) -> InlineKeyboardMarkup:
    """Build action buttons for one active raid."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Open X Post", url=target_url)],
            [
                InlineKeyboardButton(
                    "Submit Proof",
                    callback_data=f"campaign_raid_submit_{raid_id}",
                ),
                InlineKeyboardButton(
                    "How It Works",
                    callback_data=f"campaign_raid_help_{raid_id}",
                ),
            ],
        ]
    )


def xpost_review_keyboard(submission_id: int, post_url: str) -> InlineKeyboardMarkup:
    """Build admin review buttons for a personal X post submission."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Open Post", url=post_url)],
            [
                InlineKeyboardButton(
                    "Approve",
                    callback_data=f"xpost_approve_{submission_id}",
                ),
                InlineKeyboardButton(
                    "Reject",
                    callback_data=f"xpost_reject_{submission_id}",
                ),
            ],
        ]
    )


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


def ticket_keyboard(ticket_id: str, claimed: bool = False) -> InlineKeyboardMarkup:
    """
    Build the admin ticket-claim keyboard.

    Args:
        ticket_id: Formatted ticket ID (e.g. "HSTF-0001")
        claimed: If True, shows claimed status without action buttons; if False, shows claim button

    Returns:
        InlineKeyboardMarkup with claim button or empty if claimed
    """
    if claimed:
        # No buttons for claimed tickets (message will be edited to remove buttons)
        return InlineKeyboardMarkup([])
    
    buttons = [
        [
            InlineKeyboardButton(
                "Claim Ticket",
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
                "I Understand",
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
                "Send Now",
                callback_data=f"broadcast_confirm_{broadcast_id}",
            ),
            InlineKeyboardButton(
                "Cancel",
                callback_data=f"broadcast_cancel_{broadcast_id}",
            ),
        ],
    ]
    return InlineKeyboardMarkup(buttons)
