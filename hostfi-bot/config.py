"""
Module: config.py
Purpose: Central configuration — loads and validates all environment variables
Author: HOSTFI Bot Team
"""

import json
import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_env(key: str) -> str:
    """
    Retrieve a required environment variable or terminate the process.

    Args:
        key: Name of the environment variable

    Returns:
        The value of the environment variable

    Raises:
        SystemExit: If the variable is not set or empty
    """
    value = os.getenv(key)
    if not value:
        print(
            f"FATAL: Missing required environment variable: {key}",
            file=sys.stderr,
        )
        sys.exit(1)
    return value


def _parse_int_list(raw: str) -> list[int]:
    """
    Parse a comma-separated string into a list of integers.

    Args:
        raw: Comma-separated integer string (e.g. "123,456")

    Returns:
        List of parsed integers

    Raises:
        SystemExit: If any element is not a valid integer
    """
    try:
        return [int(x.strip()) for x in raw.split(",") if x.strip()]
    except ValueError as exc:
        print(
            f"FATAL: Could not parse integer list '{raw}': {exc}",
            file=sys.stderr,
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN: str = _require_env("TELEGRAM_BOT_TOKEN")
TELEGRAM_WEBHOOK_SECRET: str = _require_env("TELEGRAM_WEBHOOK_SECRET")
WEBHOOK_URL: str = _require_env("WEBHOOK_URL")

# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------
_raw_admin_ids = os.getenv("ADMIN_IDS", "")
ADMIN_IDS: list[int] = _parse_int_list(_raw_admin_ids) if _raw_admin_ids.strip() else []
SUPERADMIN_ID: int = int(_require_env("SUPERADMIN_ID"))
_raw_admin_channel = os.getenv("ADMIN_CHANNEL_ID", "")
ADMIN_CHANNEL_ID: int = int(_raw_admin_channel) if _raw_admin_channel.strip() else 0
_raw_community_group = os.getenv("COMMUNITY_GROUP_ID", "")
COMMUNITY_GROUP_ID: int = int(_raw_community_group) if _raw_community_group.strip() else 0

# ---------------------------------------------------------------------------
# Groq AI
# ---------------------------------------------------------------------------
GROQ_API_KEY: str = _require_env("GROQ_API_KEY")

# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------
OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_EMBEDDING_MODEL: str = os.getenv(
    "OPENROUTER_EMBEDDING_MODEL",
    "openai/text-embedding-3-small",
)
OPENROUTER_EMBEDDING_URL: str = os.getenv(
    "OPENROUTER_EMBEDDING_URL",
    "https://openrouter.ai/api/v1/embeddings",
)
EMBEDDING_PROVIDER: str = os.getenv(
    "EMBEDDING_PROVIDER",
    "openrouter" if OPENROUTER_API_KEY else "local",
).lower()

# ---------------------------------------------------------------------------
# Supabase
# ---------------------------------------------------------------------------
SUPABASE_URL: str = _require_env("SUPABASE_URL")
SUPABASE_KEY: str = _require_env("SUPABASE_KEY")

# ---------------------------------------------------------------------------
# Upstash Redis (optional — bot works without it, rate limiting is skipped)
# ---------------------------------------------------------------------------
UPSTASH_REDIS_URL: str = os.getenv("UPSTASH_REDIS_URL", "")
UPSTASH_REDIS_TOKEN: str = os.getenv("UPSTASH_REDIS_TOKEN", "")

# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------
PORT: int = int(os.getenv("PORT", "8000"))
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
ENVIRONMENT: str = os.getenv("ENVIRONMENT", "production")
RAG_CONFIDENCE_THRESHOLD: float = float(
    os.getenv("RAG_CONFIDENCE_THRESHOLD", "0.72")
)
MAX_MESSAGES_PER_MINUTE: int = int(os.getenv("MAX_MESSAGES_PER_MINUTE", "10"))
CHROMA_PERSIST_PATH: str = os.getenv("CHROMA_PERSIST_PATH", "./chroma_db")


# ---------------------------------------------------------------------------
# Structured JSON Logging
# ---------------------------------------------------------------------------


class _JSONFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        """
        Format a log record as a JSON string.

        Args:
            record: The log record to format

        Returns:
            Single-line JSON string
        """
        log_entry: dict = {
            "timestamp": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "module": record.module,
            "function": record.funcName,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)


def setup_logging() -> None:
    """
    Configure the root logger with structured JSON output.

    Sets the log level from the LOG_LEVEL environment variable and
    attaches a JSON-formatting StreamHandler to stdout.
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JSONFormatter())

    # Remove pre-existing handlers to avoid duplicates on re-import
    root.handlers.clear()
    root.addHandler(handler)


# Auto-configure logging on import so every module gets structured output
setup_logging()
