"""
Module: formatter.py
Purpose: HTML message formatters for all Telegram bot responses
Author: HOSTFI Bot Team
"""

import html
import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Welcome / onboarding
# ---------------------------------------------------------------------------


def format_welcome(name: str) -> str:
    """
    Build the welcome message shown to a new community member.

    Args:
        name: User's first name (will be HTML-escaped)

    Returns:
        HTML-formatted welcome string
    """
    safe_name = html.escape(name)
    return (
        f"🎉 <b>Welcome to HOSTFI Community, {safe_name}!</b>\n\n"
        "The home of seamless crypto-fintech in Africa. 🌍\n\n"
        "<b>What you can do on HOSTFI:</b>\n"
        "• 💰 Buy &amp; sell crypto instantly\n"
        "• 💳 Spend with virtual cards\n"
        "• 🔄 Swap digital assets seamlessly\n"
        "• 🏦 Deposit &amp; withdraw NGN\n\n"
        "<b>Quick Commands:</b>\n"
        "/help — See all commands\n"
        "/price BTC — Check live crypto prices\n"
        "/support — Get help from our team"
    )


def format_verification_prompt(num1: int, num2: int) -> str:
    """
    Build the CAPTCHA verification prompt appended to the welcome message.

    Args:
        num1: First operand for the addition
        num2: Second operand for the addition

    Returns:
        HTML-formatted verification prompt
    """
    return (
        f"\n\n🔒 <b>Verification Required</b>\n"
        f"To prove you're human, solve this:\n\n"
        f"<b>What is {num1} + {num2}?</b>\n\n"
        "<i>Select the correct answer below. You have 5 minutes.</i>"
    )


# ---------------------------------------------------------------------------
# Moderation
# ---------------------------------------------------------------------------


def format_warn(username: str, reason: str, warn_count: int) -> str:
    """
    Build a warning notification message.

    Args:
        username: Display name of the warned user
        reason: Reason for the warning
        warn_count: New total warning count

    Returns:
        HTML-formatted warning string
    """
    safe = html.escape(username)
    safe_reason = html.escape(reason)
    next_ban = (
        "\n🚨 <i>Next warning will result in a ban.</i>" if warn_count == 2 else ""
    )
    return (
        f"⚠️ <b>Warning Issued</b>\n\n"
        f"👤 User: {safe}\n"
        f"📝 Reason: {safe_reason}\n"
        f"⚡ Warnings: <b>{warn_count}/3</b>"
        f"{next_ban}"
    )


def format_ban(username: str, reason: str) -> str:
    """
    Build a ban notification message.

    Args:
        username: Display name of the banned user
        reason: Reason for the ban

    Returns:
        HTML-formatted ban string
    """
    safe = html.escape(username)
    safe_reason = html.escape(reason)
    return (
        f"🔨 <b>User Banned</b>\n\n"
        f"👤 User: {safe}\n"
        f"📝 Reason: {safe_reason}\n\n"
        "This action has been logged."
    )


def format_mute(username: str, duration: str, reason: str) -> str:
    """
    Build a mute notification message.

    Args:
        username: Display name of the muted user
        duration: Human-readable duration string (e.g. "30 minutes")
        reason: Reason for the mute

    Returns:
        HTML-formatted mute string
    """
    safe = html.escape(username)
    safe_reason = html.escape(reason)
    safe_duration = html.escape(duration)
    return (
        f"🔇 <b>User Muted</b>\n\n"
        f"👤 User: {safe}\n"
        f"⏱️ Duration: {safe_duration}\n"
        f"📝 Reason: {safe_reason}\n\n"
        "The user cannot send messages until the mute expires."
    )


def format_unmute(username: str) -> str:
    """
    Build an unmute notification message.

    Args:
        username: Display name of the unmuted user

    Returns:
        HTML-formatted unmute string
    """
    safe = html.escape(username)
    return (
        f"🔊 <b>User Unmuted</b>\n\n"
        f"👤 User: {safe}\n\n"
        "The user can now send messages again."
    )


def format_unban(username: str) -> str:
    """
    Build an unban notification message.

    Args:
        username: Display name of the unbanned user

    Returns:
        HTML-formatted unban string
    """
    safe = html.escape(username)
    return (
        f"✅ <b>User Unbanned</b>\n\n"
        f"👤 User: {safe}\n\n"
        "The user may now rejoin the group."
    )


def format_kick(username: str, reason: str) -> str:
    """
    Build a kick notification message.

    Args:
        username: Display name of the kicked user
        reason: Reason for the kick

    Returns:
        HTML-formatted kick string
    """
    safe = html.escape(username)
    safe_reason = html.escape(reason)
    return (
        f"👢 <b>User Kicked</b>\n\n"
        f"👤 User: {safe}\n"
        f"📝 Reason: {safe_reason}\n\n"
        "The user can rejoin the group via invite link."
    )


def format_flood_mute(username: str) -> str:
    """
    Build the automatic flood-control mute notification.

    Args:
        username: Display name of the user who triggered flood control

    Returns:
        HTML-formatted flood mute string
    """
    safe = html.escape(username)
    return (
        f"🚿 <b>Flood Control</b>\n\n"
        f"👤 {safe} has been muted for <b>5 minutes</b> "
        "for sending too many messages.\n\n"
        "Please slow down."
    )


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def format_rules() -> str:
    """
    Build the community rules message.

    Returns:
        HTML-formatted community rules string
    """
    return (
        "📜 <b>HOSTFI Community Rules</b>\n\n"
        "1️⃣ <b>Be Respectful</b> — No harassment, hate speech, or personal attacks.\n"
        "2️⃣ <b>No Spam</b> — No promotional messages, repetitive content, or unsolicited links.\n"
        "3️⃣ <b>No Scams</b> — Do not share wallet addresses, phishing links, or impersonate staff.\n"
        "4️⃣ <b>Stay On Topic</b> — Keep conversations relevant to HOSTFI and crypto-fintech.\n"
        "5️⃣ <b>No Financial Advice</b> — Do not give investment recommendations to other members.\n"
        "6️⃣ <b>English Only</b> — Use English in the main group chat.\n"
        "7️⃣ <b>Report Issues</b> — Use /support to report problems. Do not post personal issues publicly.\n"
        "8️⃣ <b>Follow Admin Instructions</b> — Admins have the final say on disputes.\n\n"
        "⚠️ <b>Violations lead to:</b> Warning → Mute → Ban\n\n"
        "Thank you for being part of the HOSTFI community! 🌍"
    )
