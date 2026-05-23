"""
Module: formatter.py
Purpose: HTML message formatters for all Telegram bot responses
Author: HOSTFI Bot Team
"""

import html
import logging

logger = logging.getLogger(__name__)

DIVIDER = "────────────"


def title(text: str, icon: str | None = None) -> str:
    """Return a consistent short HTML title line."""
    prefix = f"{icon} " if icon else ""
    return f"{prefix}<b>{html.escape(text)}</b>"


def field(label: str, value: object) -> str:
    """Return a compact label/value line."""
    return f"<b>{html.escape(label)}</b>: {value}"


def bullet(text: str) -> str:
    """Return a clean user-facing bullet line."""
    return f"• {text}"


def status_text(kind: str, message: str) -> str:
    """Return a consistent short status message."""
    icons = {
        "success": "✅",
        "error": "❌",
        "warning": "⚠️",
        "info": "ℹ️",
    }
    icon = icons.get(kind, "ℹ️")
    return f"{icon} {html.escape(message)}"


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
    return "\n".join(
        [
            title(f"Welcome to HOSTFI, {name}", "👋"),
            "",
            "Your home for seamless crypto-fintech in Africa.",
            "",
            title("What you can do"),
            bullet("Buy and sell crypto instantly"),
            bullet("Spend with virtual cards"),
            bullet("Swap digital assets"),
            bullet("Deposit and withdraw NGN"),
            "",
            title("Need help?"),
            "Use the buttons below or send <code>/support</code>.",
        ]
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
        "\n\n"
        + title("Verification", "🔒")
        + "\n"
        + f"Select the answer to <b>{num1} + {num2}</b>.\n"
        + "<i>You have 5 minutes.</i>"
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
    lines = [
        title("Warning Issued", "⚠️"),
        "",
        field("User", safe),
        field("Reason", safe_reason),
        field("Warnings", f"<b>{warn_count}/3</b>"),
    ]
    if warn_count == 2:
        lines.append("")
        lines.append("<i>Next warning results in a ban.</i>")
    return "\n".join(lines)


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
    return "\n".join(
        [
            title("User Banned", "⛔"),
            "",
            field("User", safe),
            field("Reason", safe_reason),
            "",
            "This action has been logged.",
        ]
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
    return "\n".join(
        [
            title("User Muted", "🔇"),
            "",
            field("User", safe),
            field("Duration", safe_duration),
            field("Reason", safe_reason),
            "",
            "The user cannot send messages until the mute expires.",
        ]
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
    return "\n".join([title("User Unmuted", "✅"), "", field("User", safe)])


def format_unban(username: str) -> str:
    """
    Build an unban notification message.

    Args:
        username: Display name of the unbanned user

    Returns:
        HTML-formatted unban string
    """
    safe = html.escape(username)
    return "\n".join([title("User Unbanned", "✅"), "", field("User", safe)])


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
    return "\n".join(
        [
            title("User Kicked", "⛔"),
            "",
            field("User", safe),
            field("Reason", safe_reason),
            "",
            "The user can rejoin with a valid invite link.",
        ]
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
    return "\n".join(
        [
            title("Flood Control", "⚠️"),
            "",
            f"{safe} has been muted for <b>5 minutes</b>.",
            "Please slow down.",
        ]
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
    return "\n".join(
        [
            title("Community Rules", "📜"),
            "",
            bullet("<b>Be respectful</b> — no harassment or personal attacks."),
            bullet("<b>No spam</b> — avoid promos, repetition, and unsolicited links."),
            bullet("<b>No scams</b> — no phishing, wallet requests, or impersonation."),
            bullet("<b>Stay on topic</b> — keep discussion relevant to HOSTFI."),
            bullet("<b>No financial advice</b> — avoid investment recommendations."),
            bullet("<b>English only</b> — use English in the main group chat."),
            bullet("<b>Use support</b> — send private issues through <code>/support</code>."),
            bullet("<b>Respect admins</b> — moderator decisions are final."),
            "",
            field("Violations", "Warning → Mute → Ban"),
        ]
    )
