"""
Module: retriever.py
Purpose: Query ChromaDB for the top-K most similar knowledge chunks
Author: HOSTFI Bot Team
"""

import asyncio
import logging
import time
from typing import NamedTuple

from rag.ingestion import get_collection, get_embedding_model

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


class RetrievalResult(NamedTuple):
    """Single retrieval result with text, source, and similarity score."""

    text: str
    source: str
    score: float  # cosine similarity (higher = more similar)


# ---------------------------------------------------------------------------
# Core retriever
# ---------------------------------------------------------------------------


def _query_sync(
    query_text: str, top_k: int
) -> list[RetrievalResult]:
    """
    Synchronous embedding + ChromaDB query.

    Args:
        query_text: User question text
        top_k: Number of results to return

    Returns:
        List of RetrievalResult ordered by descending similarity
    """
    start = time.perf_counter()
    model = get_embedding_model()
    collection = get_collection()

    # Check that the collection has documents
    count = collection.count()
    if count == 0:
        logger.warning("Knowledge base is empty — no documents to search")
        return []

    # Embed the query
    embed_start = time.perf_counter()
    query_embedding = model.encode(query_text).tolist()
    embed_ms = (time.perf_counter() - embed_start) * 1000

    query_start = time.perf_counter()
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=min(top_k, count),
        include=["documents", "metadatas", "distances"],
    )
    query_ms = (time.perf_counter() - query_start) * 1000

    # ChromaDB returns distances; for cosine space, distance = 1 - similarity
    retrieval_results: list[RetrievalResult] = []

    if results and results["documents"] and results["documents"][0]:
        for doc, meta, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            similarity = 1.0 - dist  # convert distance to similarity
            retrieval_results.append(
                RetrievalResult(
                    text=doc,
                    source=meta.get("source", "unknown"),
                    score=round(similarity, 4),
                )
            )

    # Sort descending by score (should already be, but ensure)
    retrieval_results.sort(key=lambda r: r.score, reverse=True)

    total_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "Retriever timing total=%.1fms embed=%.1fms query=%.1fms count=%d top_k=%d",
        total_ms,
        embed_ms,
        query_ms,
        count,
        top_k,
    )
    return retrieval_results


async def retrieve(
    query: str, top_k: int = 3
) -> list[RetrievalResult]:
    """
    Retrieve the most semantically similar knowledge chunks for a query.

    Args:
        query: User question text
        top_k: Number of top results to return (default 3)

    Returns:
        List of RetrievalResult with text, source, and similarity score

    Raises:
        Exception: If ChromaDB query fails
    """
    try:
        start = time.perf_counter()
        results = await asyncio.to_thread(_query_sync, query, top_k)
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "Retrieved %d chunks in %.1fms for query (top score: %.4f): %s",
            len(results),
            elapsed_ms,
            results[0].score if results else 0.0,
            query[:80],
        )
        return results
    except Exception as exc:
        logger.error("Retrieval failed for query '%s': %s", query[:80], exc)
        raise


# ---------------------------------------------------------------------------
# Utility: Build context string from results
# ---------------------------------------------------------------------------


def build_context(results: list[RetrievalResult]) -> str:
    """
    Concatenate retrieval results into a single context string for the
    AI prompt.

    Each chunk is labelled with its source and similarity score.

    Args:
        results: List of RetrievalResult from the retriever

    Returns:
        Formatted context string
    """
    if not results:
        return ""

    parts: list[str] = []
    for i, r in enumerate(results, 1):
        parts.append(
            f"[Source: {r.source} | Relevance: {r.score:.2f}]\n{r.text}"
        )

    return "\n\n---\n\n".join(parts)
