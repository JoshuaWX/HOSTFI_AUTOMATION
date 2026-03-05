"""
Module: client.py
Purpose: Supabase client singleton for all database operations
Author: HOSTFI Bot Team
"""

import logging

from supabase import Client, create_client

from config import SUPABASE_KEY, SUPABASE_URL

logger = logging.getLogger(__name__)

_client: Client | None = None


def get_supabase_client() -> Client:
    """
    Return the Supabase client singleton. Creates it on first call.

    Returns:
        Initialised Supabase Client instance

    Raises:
        RuntimeError: If client creation fails
    """
    global _client
    if _client is None:
        try:
            _client = create_client(SUPABASE_URL, SUPABASE_KEY)
            logger.info("Supabase client initialised successfully")
        except Exception as exc:
            logger.critical("Failed to initialise Supabase client: %s", exc)
            raise RuntimeError("Supabase initialisation failed") from exc
    return _client
