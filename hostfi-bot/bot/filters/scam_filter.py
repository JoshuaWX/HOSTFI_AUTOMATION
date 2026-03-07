"""
Module: scam_filter.py
Purpose: Phishing domain blocklist, fake wallet pattern regex, impersonation detection
Author: HOSTFI Bot Team
"""

import logging
import re
from typing import NamedTuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Known phishing / scam domains
# ---------------------------------------------------------------------------

PHISHING_DOMAINS: list[str] = [
    "hostfi-support.com",
    "hostfi-wallet.com",
    "hostfi-airdrop.com",
    "hostfiapp.xyz",
    "hostfi-exchange.net",
    "hostfi-bonus.com",
    "hostfi.com",
    "hostfii.com",
    "h0stfi.com",
    "hostfii.app",
    "metamask-verify.com",
    "trustwallet-verify.com",
    "binance-verify.com",
    "wallet-connect.org",
    "walletconnect-verify.com",
    "dapps-connect.com",
    "defi-swap.io",
    "crypto-verify.net",
    "token-airdrop.io",
    "nft-claim.xyz",
    "claim-rewards.io",
]

# ---------------------------------------------------------------------------
# Crypto wallet address patterns
# ---------------------------------------------------------------------------

# BTC: legacy (1…), P2SH (3…), bech32 (bc1…)
BTC_PATTERN: re.Pattern = re.compile(
    r"\b(1[a-km-zA-HJ-NP-Z1-9]{25,34}"
    r"|3[a-km-zA-HJ-NP-Z1-9]{25,34}"
    r"|bc1[a-zA-HJ-NP-Z0-9]{39,59})\b"
)

# ETH / EVM: 0x followed by 40 hex characters
ETH_PATTERN: re.Pattern = re.compile(r"\b0x[a-fA-F0-9]{40}\b")

# ---------------------------------------------------------------------------
# Impersonation patterns — usernames / display names that mimic staff
# ---------------------------------------------------------------------------

IMPERSONATION_KEYWORDS: list[str] = [
    "hostfi_admin",
    "hostfi_support",
    "hostfi_official",
    "hostfi_team",
    "hostfi_help",
    "hostfi_mod",
    "hostfisupport",
    "hostfiadmin",
    "hostfi_bot",
    "hostfi_ceo",
    "hostfi_founder",
]

# Regex catches letter substitutions (0 for o, 1/l for i)
IMPERSONATION_REGEX: re.Pattern = re.compile(
    r"h[o0]stf[i1l][\s_\-]?"
    r"(admin|support|official|team|help|mod|bot|ceo|founder)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


class ScamCheckResult(NamedTuple):
    """Result of a scam check."""

    is_scam: bool
    reason: str
    severity: str  # "low", "medium", "high"


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


async def check_phishing_domains(text: str) -> ScamCheckResult:
    """
    Scan message text for known phishing domains.

    Args:
        text: Raw message text

    Returns:
        ScamCheckResult with detection details
    """
    lower = text.lower()
    for domain in PHISHING_DOMAINS:
        if domain in lower:
            logger.warning("Phishing domain detected: %s", domain)
            return ScamCheckResult(
                is_scam=True,
                reason=f"Phishing domain: {domain}",
                severity="high",
            )
    return ScamCheckResult(is_scam=False, reason="", severity="low")


async def check_wallet_addresses(text: str) -> ScamCheckResult:
    """
    Detect crypto wallet addresses posted in group chat.

    Wallet addresses in group messages are almost always scam-related
    (fake investment schemes, "send BTC here" traps).

    Args:
        text: Raw message text

    Returns:
        ScamCheckResult with detection details
    """
    if BTC_PATTERN.search(text):
        logger.warning("BTC address pattern detected in message")
        return ScamCheckResult(
            is_scam=True,
            reason="Crypto wallet address posted (BTC pattern)",
            severity="high",
        )
    if ETH_PATTERN.search(text):
        logger.warning("ETH address pattern detected in message")
        return ScamCheckResult(
            is_scam=True,
            reason="Crypto wallet address posted (ETH pattern)",
            severity="high",
        )
    return ScamCheckResult(is_scam=False, reason="", severity="low")


async def check_impersonation(
    username: str | None,
    display_name: str | None,
    admin_ids: list[int],
    user_id: int,
) -> ScamCheckResult:
    """
    Detect users impersonating HOSTFI staff via username or display name.

    Registered admins are always skipped.

    Args:
        username: Telegram username (without @)
        display_name: User's display / first name
        admin_ids: List of legitimate admin Telegram IDs
        user_id: Telegram user ID of the sender

    Returns:
        ScamCheckResult with detection details
    """
    # Admins are never flagged as impersonators
    if user_id in admin_ids:
        return ScamCheckResult(is_scam=False, reason="", severity="low")

    targets = [
        s.lower() for s in [username, display_name] if s is not None
    ]

    for target in targets:
        # Exact keyword match
        for keyword in IMPERSONATION_KEYWORDS:
            if keyword in target:
                logger.warning(
                    "Impersonation detected: '%s' matches '%s'",
                    target,
                    keyword,
                )
                return ScamCheckResult(
                    is_scam=True,
                    reason=f"Impersonation: name contains '{keyword}'",
                    severity="high",
                )

        # Regex match (letter substitutions)
        if IMPERSONATION_REGEX.search(target):
            logger.warning(
                "Impersonation regex hit on '%s'", target
            )
            return ScamCheckResult(
                is_scam=True,
                reason="Impersonation: name matches staff pattern",
                severity="high",
            )

    return ScamCheckResult(is_scam=False, reason="", severity="low")


# ---------------------------------------------------------------------------
# Aggregate runner
# ---------------------------------------------------------------------------


async def run_scam_checks(
    text: str,
    username: str | None = None,
    display_name: str | None = None,
    admin_ids: list[int] | None = None,
    user_id: int = 0,
) -> ScamCheckResult:
    """
    Run all scam detection checks against a message in sequence.

    Exits early on the first positive hit.

    Args:
        text: Raw message text
        username: Sender's Telegram username
        display_name: Sender's display name
        admin_ids: List of legitimate admin IDs
        user_id: Sender's Telegram user ID

    Returns:
        ScamCheckResult — first positive detection or clean result
    """
    # 1. Phishing domains
    result = await check_phishing_domains(text)
    if result.is_scam:
        return result

    # 2. Wallet addresses
    result = await check_wallet_addresses(text)
    if result.is_scam:
        return result

    # 3. Impersonation
    result = await check_impersonation(
        username, display_name, admin_ids or [], user_id
    )
    if result.is_scam:
        return result

    return ScamCheckResult(is_scam=False, reason="", severity="low")
