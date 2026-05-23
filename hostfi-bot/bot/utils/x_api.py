"""
Module: x_api.py
Purpose: Official X API helpers for campaign proof verification
Author: HOSTFI Bot Team
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from config import X_API_BASE_URL, X_BEARER_TOKEN

logger = logging.getLogger(__name__)

X_POST_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:x\.com|twitter\.com)/(?P<username>[A-Za-z0-9_]+)/status/(?P<id>\d+)",
    re.IGNORECASE,
)

LOW_EFFORT_PATTERNS = [
    "gm",
    "nice",
    "good",
    "done",
    "raid",
    "lfg",
    "great",
    "wow",
]

HOSTFI_TERMS = [
    "hostfi",
    "host finance",
    "$hostfi",
    "@hostfi_app",
]


@dataclass
class XPost:
    """Normalized X post payload."""

    post_id: str
    text: str
    author_id: str
    username: str
    created_at: datetime | None
    conversation_id: str | None
    referenced_ids: list[str]
    referenced_types: list[str]
    url: str


class XApiNotConfigured(RuntimeError):
    """Raised when X API commands are used without a bearer token."""


def parse_x_post_url(url: str) -> tuple[str, str] | None:
    """Extract username and post ID from an X/Twitter status URL."""
    match = X_POST_RE.search(url.strip())
    if not match:
        return None
    return match.group("username").lower(), match.group("id")


def is_x_api_configured() -> bool:
    """Return True when the bot has credentials for official X API access."""
    return bool(X_BEARER_TOKEN)


async def _x_get(path: str, params: dict[str, str] | None = None) -> dict:
    """Perform an authenticated GET request against the X API."""
    if not X_BEARER_TOKEN:
        raise XApiNotConfigured("X_BEARER_TOKEN is not configured")

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(
            f"{X_API_BASE_URL}{path}",
            params=params,
            headers={"Authorization": f"Bearer {X_BEARER_TOKEN}"},
        )
        response.raise_for_status()
        return response.json()


def _parse_datetime(value: str | None) -> datetime | None:
    """Parse X API ISO timestamps."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


async def fetch_post(post_id: str, original_url: str = "") -> XPost:
    """Fetch and normalize one X post by ID."""
    data = await _x_get(
        f"/tweets/{post_id}",
        params={
            "tweet.fields": "author_id,created_at,conversation_id,referenced_tweets,text",
            "expansions": "author_id",
            "user.fields": "username",
        },
    )

    tweet = data.get("data") or {}
    users = data.get("includes", {}).get("users", [])
    user_map = {user.get("id"): user for user in users}
    author = user_map.get(tweet.get("author_id"), {})
    referenced = tweet.get("referenced_tweets") or []

    return XPost(
        post_id=str(tweet.get("id", post_id)),
        text=tweet.get("text", ""),
        author_id=str(tweet.get("author_id", "")),
        username=str(author.get("username", "")).lower(),
        created_at=_parse_datetime(tweet.get("created_at")),
        conversation_id=str(tweet.get("conversation_id")) if tweet.get("conversation_id") else None,
        referenced_ids=[str(item.get("id")) for item in referenced if item.get("id")],
        referenced_types=[str(item.get("type")) for item in referenced if item.get("type")],
        url=original_url,
    )


def is_meaningful_x_text(text: str) -> bool:
    """
    Reject very short or low-effort X text.

    This is intentionally conservative because reward money is involved.
    """
    clean = re.sub(r"https?://\S+", "", text).strip()
    words = re.findall(r"[A-Za-z0-9_@#]+", clean)
    if len(words) < 6:
        return False
    lower = clean.lower()
    if lower in LOW_EFFORT_PATTERNS:
        return False
    if len(set(word.lower() for word in words)) < 4:
        return False
    return True


def mentions_hostfi(text: str) -> bool:
    """Return True when text references HostFi."""
    lower = text.lower()
    return any(term in lower for term in HOSTFI_TERMS)


def is_reply_or_quote_to(post: XPost, target_post_id: str, target_url: str) -> bool:
    """Return True if proof post engages the approved raid target."""
    if target_post_id in post.referenced_ids:
        return True
    if post.conversation_id == target_post_id:
        return True
    return target_url.lower() in post.text.lower()


def canonical_x_url(username: str, post_id: str) -> str:
    """Build a canonical X post URL."""
    safe_user = username.lstrip("@") or "i"
    return f"https://x.com/{safe_user}/status/{post_id}"
