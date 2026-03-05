"""
Module: ingestion.py
Purpose: Scrape URLs, clean text, chunk (500 tokens, 50 overlap), embed with
         sentence-transformers, and store in ChromaDB
Author: HOSTFI Bot Team
"""

import asyncio
import glob
import hashlib
import logging
import os
import re
from pathlib import Path

import chromadb
import httpx
from bs4 import BeautifulSoup
from sentence_transformers import SentenceTransformer

from config import CHROMA_PERSIST_PATH

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Singleton instances
# ---------------------------------------------------------------------------

_embedding_model: SentenceTransformer | None = None
_chroma_client: chromadb.ClientAPI | None = None

COLLECTION_NAME = "hostfi_knowledge"
EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"
CHUNK_SIZE = 500  # approx tokens (words as proxy)
CHUNK_OVERLAP = 50


def get_embedding_model() -> SentenceTransformer:
    """
    Return the sentence-transformer embedding model singleton.

    Returns:
        Loaded SentenceTransformer model
    """
    global _embedding_model
    if _embedding_model is None:
        logger.info("Loading embedding model: %s", EMBEDDING_MODEL_NAME)
        _embedding_model = SentenceTransformer(EMBEDDING_MODEL_NAME)
        logger.info("Embedding model loaded successfully")
    return _embedding_model


def get_chroma_client() -> chromadb.ClientAPI:
    """
    Return the persistent ChromaDB client singleton.

    Returns:
        ChromaDB client connected to the configured persist path
    """
    global _chroma_client
    if _chroma_client is None:
        persist_dir = os.path.abspath(CHROMA_PERSIST_PATH)
        os.makedirs(persist_dir, exist_ok=True)
        _chroma_client = chromadb.PersistentClient(path=persist_dir)
        logger.info("ChromaDB client initialised at: %s", persist_dir)
    return _chroma_client


def get_collection() -> chromadb.Collection:
    """
    Get or create the HOSTFI knowledge base collection.

    Returns:
        ChromaDB collection for the knowledge base
    """
    client = get_chroma_client()
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


# ---------------------------------------------------------------------------
# Text cleaning
# ---------------------------------------------------------------------------


def clean_text(raw: str) -> str:
    """
    Clean raw text by removing excess whitespace, HTML fragments, and
    non-printable characters.

    Args:
        raw: Raw input text

    Returns:
        Cleaned text string
    """
    # Strip HTML tags if present
    if "<" in raw and ">" in raw:
        soup = BeautifulSoup(raw, "html.parser")
        text = soup.get_text(separator=" ")
    else:
        text = raw

    # Collapse multiple whitespace
    text = re.sub(r"\s+", " ", text)
    # Remove non-printable characters
    text = re.sub(r"[^\x20-\x7E\n\r\t]", "", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def chunk_text(
    text: str,
    source: str,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> list[dict[str, str]]:
    """
    Split text into overlapping chunks of approximately *chunk_size* words.

    Each chunk is returned as a dict with keys ``text``, ``source``,
    and ``chunk_id`` (deterministic SHA-256 hash).

    Args:
        text: Cleaned input text
        source: Human-readable source label (e.g. filename)
        chunk_size: Target words per chunk
        overlap: Number of overlapping words between consecutive chunks

    Returns:
        List of chunk dicts
    """
    words = text.split()
    if not words:
        return []

    chunks: list[dict[str, str]] = []
    start = 0

    while start < len(words):
        end = start + chunk_size
        chunk_words = words[start:end]
        chunk_text_str = " ".join(chunk_words)

        # Deterministic ID based on content
        chunk_id = hashlib.sha256(
            f"{source}:{start}:{chunk_text_str}".encode()
        ).hexdigest()[:16]

        chunks.append(
            {
                "text": chunk_text_str,
                "source": source,
                "chunk_id": f"{source}_{chunk_id}",
            }
        )

        step = max(1, chunk_size - overlap)
        start += step

    logger.info(
        "Chunked '%s' into %d chunks (size=%d, overlap=%d)",
        source,
        len(chunks),
        chunk_size,
        overlap,
    )
    return chunks


# ---------------------------------------------------------------------------
# URL scraping
# ---------------------------------------------------------------------------


async def scrape_url(url: str) -> str:
    """
    Fetch a URL and extract its visible text content.

    Args:
        url: HTTP(S) URL to scrape

    Returns:
        Cleaned text extracted from the page

    Raises:
        httpx.HTTPStatusError: If the request returns a non-2xx status
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    # Remove script / style tags
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    return clean_text(soup.get_text(separator=" "))


# ---------------------------------------------------------------------------
# Local file loading
# ---------------------------------------------------------------------------


def load_knowledge_base_files(
    directory: str | None = None,
) -> list[dict[str, str]]:
    """
    Read all ``.txt`` files from the knowledge base directory and
    return their content with source labels.

    Args:
        directory: Path to the knowledge base folder.
                   Defaults to ``rag/knowledge_base/`` relative to this file.

    Returns:
        List of dicts with ``text`` and ``source`` keys
    """
    if directory is None:
        directory = str(
            Path(__file__).resolve().parent / "knowledge_base"
        )

    documents: list[dict[str, str]] = []
    pattern = os.path.join(directory, "*.txt")

    for filepath in sorted(glob.glob(pattern)):
        filename = os.path.basename(filepath)
        try:
            with open(filepath, "r", encoding="utf-8") as fh:
                raw = fh.read()
            cleaned = clean_text(raw)
            if cleaned:
                documents.append({"text": cleaned, "source": filename})
                logger.info("Loaded knowledge file: %s (%d chars)", filename, len(cleaned))
        except Exception as exc:
            logger.error("Failed to read %s: %s", filepath, exc)

    logger.info(
        "Loaded %d knowledge base files from %s",
        len(documents),
        directory,
    )
    return documents


# ---------------------------------------------------------------------------
# Embedding + storage
# ---------------------------------------------------------------------------


def embed_and_store(chunks: list[dict[str, str]]) -> int:
    """
    Embed a list of text chunks and upsert them into ChromaDB.

    Args:
        chunks: List of chunk dicts with ``text``, ``source``, ``chunk_id``

    Returns:
        Number of chunks stored
    """
    if not chunks:
        logger.warning("No chunks to embed — skipping")
        return 0

    model = get_embedding_model()
    collection = get_collection()

    texts = [c["text"] for c in chunks]
    ids = [c["chunk_id"] for c in chunks]
    metadatas = [{"source": c["source"]} for c in chunks]

    # Embed in batches to manage memory
    batch_size = 64
    total_stored = 0

    for i in range(0, len(texts), batch_size):
        batch_texts = texts[i : i + batch_size]
        batch_ids = ids[i : i + batch_size]
        batch_meta = metadatas[i : i + batch_size]

        embeddings = model.encode(
            batch_texts, show_progress_bar=False
        ).tolist()

        collection.upsert(
            ids=batch_ids,
            documents=batch_texts,
            embeddings=embeddings,
            metadatas=batch_meta,
        )
        total_stored += len(batch_texts)

    logger.info("Stored %d chunks in ChromaDB", total_stored)
    return total_stored


# ---------------------------------------------------------------------------
# Full ingestion pipeline
# ---------------------------------------------------------------------------


async def run_ingestion(
    urls: list[str] | None = None,
    kb_directory: str | None = None,
    clear_existing: bool = False,
) -> dict[str, int]:
    """
    Run the complete ingestion pipeline:
    1. Optionally scrape supplied URLs
    2. Load local knowledge base files
    3. Clean and chunk all texts
    4. Embed and store in ChromaDB

    Args:
        urls: Optional list of URLs to scrape
        kb_directory: Override for the knowledge base file directory
        clear_existing: If True, delete existing collection before storing

    Returns:
        Summary dict with ``files_loaded``, ``urls_scraped``,
        ``total_chunks``, ``chunks_stored``
    """
    all_chunks: list[dict[str, str]] = []

    # --- 1. Scrape URLs (if any) ------------------------------------------
    urls_scraped = 0
    if urls:
        for url in urls:
            try:
                text = await scrape_url(url)
                source = url.split("//")[-1][:40]
                chunks = chunk_text(text, source)
                all_chunks.extend(chunks)
                urls_scraped += 1
                logger.info("Scraped and chunked URL: %s", url)
            except Exception as exc:
                logger.error("Failed to scrape %s: %s", url, exc)

    # --- 2. Load local files -----------------------------------------------
    documents = load_knowledge_base_files(kb_directory)
    files_loaded = len(documents)

    for doc in documents:
        chunks = chunk_text(doc["text"], doc["source"])
        all_chunks.extend(chunks)

    # --- 3. Clear existing if requested ------------------------------------
    if clear_existing:
        try:
            client = get_chroma_client()
            client.delete_collection(COLLECTION_NAME)
            logger.info("Deleted existing collection '%s'", COLLECTION_NAME)
        except Exception:
            pass  # Collection may not exist yet

    # --- 4. Embed and store ------------------------------------------------
    chunks_stored = await asyncio.to_thread(embed_and_store, all_chunks)

    summary = {
        "files_loaded": files_loaded,
        "urls_scraped": urls_scraped,
        "total_chunks": len(all_chunks),
        "chunks_stored": chunks_stored,
    }
    logger.info("Ingestion complete: %s", summary)
    return summary
