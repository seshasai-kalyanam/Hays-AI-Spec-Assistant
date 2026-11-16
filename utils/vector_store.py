"""Neon pgvector store: specification ingestion and hybrid (vector + full-text) search."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Callable

import streamlit as st
from psycopg2.extras import execute_values

from utils.llm_prompts import OPENAI_EMBEDDING_DIMENSIONS, OPENAI_EMBEDDING_MODEL
from utils.neon_utils import _return_connection, _safe_rollback, get_neon_connection
from utils.openai_client import embed_texts, iter_embedding_batches
from utils.spec_chunking import build_spec_chunks, extract_pdf_pages

DEFAULT_VECTOR_TABLE_PREFIX = "hays_spec"
VECTOR_CANDIDATES = 40
KEYWORD_CANDIDATES = 40
RRF_K = 60
INSERT_PAGE_SIZE = 200

_KEYWORD_STOPWORDS = frozenset(
    {
        "regarding", "specification", "specifications", "specified", "specify", "spec",
        "what", "which", "where", "when", "does", "required", "requirement", "requirements",
        "there", "any", "other", "additional", "the", "for", "and", "are", "is", "of",
    }
)
_SECTION_QUERY_RE = re.compile(r"\b(\d{2})\s?(\d{2})\s?(\d{2})\b")

_schema_ready = False


class VectorStoreUnavailable(RuntimeError):
    """Raised when the Neon database cannot be reached for vector storage."""


@dataclass
class ChunkHit:
    document_id: str
    chunk_index: int
    score: float


@dataclass
class ChunkRow:
    document_id: str
    chunk_index: int
    section_number: str | None
    section_title: str | None
    page_start: int
    page_end: int
    content: str


def _table_prefix() -> str:
    try:
        cfg = st.secrets.get("neon_db") or {}
        prefix = cfg.get("VECTOR_TABLE_PREFIX") or DEFAULT_VECTOR_TABLE_PREFIX
    except Exception:
        prefix = DEFAULT_VECTOR_TABLE_PREFIX
    return re.sub(r"[^a-z0-9_]", "_", str(prefix).lower())


def _tables() -> dict[str, str]:
    prefix = _table_prefix()
    return {
        "documents": f"{prefix}_documents",
        "chunks": f"{prefix}_chunks",
        "queries": f"{prefix}_query_embeddings",
    }


def _connect():
    conn = get_neon_connection()
    if conn is None:
        raise VectorStoreUnavailable(
            "Could not connect to the Neon database. Check [neon_db] in .streamlit/secrets.toml."
        )
    return conn


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{x:.6g}" for x in vector) + "]"


def ensure_vector_schema() -> None:
    """Create pgvector extension and document/chunk/query tables once per process."""
    global _schema_ready
    if _schema_ready:
        return
    t = _tables()
    dims = OPENAI_EMBEDDING_DIMENSIONS
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f"""
                CREATE EXTENSION IF NOT EXISTS vector;

                CREATE TABLE IF NOT EXISTS "{t['documents']}" (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    file_hash TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    page_count INTEGER NOT NULL DEFAULT 0,
                    chunk_count INTEGER NOT NULL DEFAULT 0,
                    embedding_model TEXT NOT NULL,
                    embedding_dims INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'processing',
                    uploaded_by TEXT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (file_hash, embedding_model, embedding_dims)
                );

                CREATE TABLE IF NOT EXISTS "{t['chunks']}" (
                    id BIGSERIAL PRIMARY KEY,
                    document_id UUID NOT NULL REFERENCES "{t['documents']}"(id) ON DELETE CASCADE,
                    chunk_index INTEGER NOT NULL,
                    section_number TEXT NULL,
                    section_title TEXT NULL,
                    article TEXT NULL,
                    page_start INTEGER NOT NULL,
                    page_end INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    embedding halfvec({dims}) NOT NULL,
                    tsv tsvector GENERATED ALWAYS AS (
                        to_tsvector(
                            'english'::regconfig,
                            coalesce(section_number, '') || ' ' ||
                            coalesce(section_title, '') || ' ' ||
                            coalesce(article, '') || ' ' || content
                        )
                    ) STORED,
                    UNIQUE (document_id, chunk_index)
                );

                CREATE INDEX IF NOT EXISTS "{t['chunks']}_tsv_idx"
                    ON "{t['chunks']}" USING GIN (tsv);

                CREATE TABLE IF NOT EXISTS "{t['queries']}" (
                    text_hash TEXT PRIMARY KEY,
                    embedding halfvec({dims}) NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
            """)
        conn.commit()
        _schema_ready = True
    except Exception:
        _safe_rollback(conn)
        raise
    finally:
        _return_connection(conn)


def file_hash(pdf_bytes: bytes) -> str:
    return hashlib.sha256(pdf_bytes).hexdigest()


def find_ready_document(pdf_hash: str) -> dict[str, Any] | None:
    """Return a fully embedded document for this file hash and embedding config, if any."""
    ensure_vector_schema()
    t = _tables()
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT id, page_count, chunk_count FROM "{t['documents']}"
                    WHERE file_hash = %s AND embedding_model = %s AND embedding_dims = %s
                      AND status = 'ready'""",
                (pdf_hash, OPENAI_EMBEDDING_MODEL, OPENAI_EMBEDDING_DIMENSIONS),
            )
            row = cur.fetchone()
        if not row:
            return None
        return {"document_id": str(row[0]), "page_count": row[1], "chunk_count": row[2]}
    finally:
        _return_connection(conn)


def list_unfinished_documents() -> list[dict[str, Any]]:
    """Documents left unfinished after a failed upload, with how many passages were stored."""
    ensure_vector_schema()
    t = _tables()
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT d.file_name, count(c.id)
                    FROM "{t['documents']}" d
                    LEFT JOIN "{t['chunks']}" c ON c.document_id = d.id
                    WHERE d.status <> 'ready'
                    GROUP BY d.id, d.file_name, d.created_at
                    ORDER BY d.created_at"""
            )
            rows = cur.fetchall()
        return [{"file_name": row[0], "chunk_count": row[1]} for row in rows]
    finally:
        _return_connection(conn)


def delete_unfinished_documents() -> int:
    """Delete documents that never finished indexing. Their passages are removed with them."""
    ensure_vector_schema()
    t = _tables()
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f"""DELETE FROM "{t['documents']}" WHERE status <> 'ready'""")
            deleted = cur.rowcount
        conn.commit()
        return deleted
    except Exception:
        _safe_rollback(conn)
        raise
    finally:
        _return_connection(conn)


def _start_document(conn, pdf_hash: str, file_name: str, uploaded_by: str | None) -> str:
    """Replace any unfinished row for this hash and insert a fresh 'processing' row."""
    t = _tables()
    with conn.cursor() as cur:
        cur.execute(
            f"""DELETE FROM "{t['documents']}"
                WHERE file_hash = %s AND embedding_model = %s AND embedding_dims = %s""",
            (pdf_hash, OPENAI_EMBEDDING_MODEL, OPENAI_EMBEDDING_DIMENSIONS),
        )
        cur.execute(
            f"""INSERT INTO "{t['documents']}"
                (file_hash, file_name, embedding_model, embedding_dims, uploaded_by)
                VALUES (%s, %s, %s, %s, %s) RETURNING id""",
            (pdf_hash, file_name, OPENAI_EMBEDDING_MODEL, OPENAI_EMBEDDING_DIMENSIONS, uploaded_by),
        )
        document_id = str(cur.fetchone()[0])
    conn.commit()
    return document_id


def _insert_chunk_rows(conn, document_id: str, chunks: list, vectors: list[list[float]]) -> None:
    t = _tables()
    rows = [
        (
            document_id,
            chunk.chunk_index,
            chunk.section_number,
            chunk.section_title,
            chunk.article,
            chunk.page_start,
            chunk.page_end,
            chunk.content,
            _vector_literal(vector),
        )
        for chunk, vector in zip(chunks, vectors)
    ]
    with conn.cursor() as cur:
        execute_values(
            cur,
            f"""INSERT INTO "{t['chunks']}"
                (document_id, chunk_index, section_number, section_title, article,
                 page_start, page_end, content, embedding) VALUES %s""",
            rows,
            template="(%s, %s, %s, %s, %s, %s, %s, %s, %s::halfvec)",
            page_size=INSERT_PAGE_SIZE,
        )
    conn.commit()


def ingest_pdf(
    client,
    pdf_bytes: bytes,
    file_name: str,
    *,
    uploaded_by: str | None = None,
    on_progress: Callable[[str, float], None] | None = None,
) -> dict[str, Any]:
    """Extract, chunk, embed, and store a PDF; reuse stored vectors for identical files."""
    def report(message: str, fraction: float) -> None:
        if on_progress:
            on_progress(message, min(max(fraction, 0.0), 1.0))

    pdf_hash = file_hash(pdf_bytes)
    existing = find_ready_document(pdf_hash)
    if existing:
        report("Already indexed — reusing stored vectors.", 1.0)
        return {**existing, "file_name": file_name, "reused": True}

    pages = extract_pdf_pages(
        pdf_bytes,
        on_progress=lambda done, total: report(f"Reading page {done} of {total}…", 0.2 * done / total),
    )
    chunks = build_spec_chunks(pages, file_name)
    if not chunks:
        raise ValueError("No text could be extracted from this PDF.")
    report(f"Embedding {len(chunks)} passages from {len(pages)} pages…", 0.2)

    conn = _connect()
    try:
        document_id = _start_document(conn, pdf_hash, file_name, uploaded_by)
        stored = 0
        for start, vectors in iter_embedding_batches(
            client,
            [c.embed_text for c in chunks],
            on_wait=lambda message: report(message, 0.2 + 0.8 * stored / max(len(chunks), 1)),
        ):
            _insert_chunk_rows(conn, document_id, chunks[start : start + len(vectors)], vectors)
            stored += len(vectors)
            report(f"Embedded {stored} of {len(chunks)} passages…", 0.2 + 0.8 * stored / len(chunks))
        t = _tables()
        with conn.cursor() as cur:
            cur.execute(
                f"""UPDATE "{t['documents']}"
                    SET status = 'ready', page_count = %s, chunk_count = %s, updated_at = now()
                    WHERE id = %s""",
                (len(pages), len(chunks), document_id),
            )
        conn.commit()
    except Exception:
        _safe_rollback(conn)
        raise
    finally:
        _return_connection(conn)
    report("Indexed.", 1.0)
    return {
        "document_id": document_id,
        "page_count": len(pages),
        "chunk_count": len(chunks),
        "file_name": file_name,
        "reused": False,
    }


def _query_hash(text: str) -> str:
    key = f"{OPENAI_EMBEDDING_MODEL}|{OPENAI_EMBEDDING_DIMENSIONS}|{text.strip()}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def embed_queries(client, texts: list[str]) -> list[str]:
    """Return halfvec literals for queries; cached in Neon so standard questions embed once."""
    ensure_vector_schema()
    t = _tables()
    hashes = [_query_hash(text) for text in texts]
    literals: dict[str, str] = {}
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""SELECT text_hash, embedding::text FROM "{t['queries']}"
                    WHERE text_hash = ANY(%s)""",
                (hashes,),
            )
            literals.update({row[0]: row[1] for row in cur.fetchall()})
        missing = [(h, text) for h, text in zip(hashes, texts) if h not in literals]
        if missing:
            unique = list(dict(missing).items())
            vectors = embed_texts(client, [text for _, text in unique])
            new_rows = [(h, _vector_literal(v)) for (h, _), v in zip(unique, vectors)]
            literals.update(dict(new_rows))
            with conn.cursor() as cur:
                execute_values(
                    cur,
                    f"""INSERT INTO "{t['queries']}" (text_hash, embedding) VALUES %s
                        ON CONFLICT (text_hash) DO NOTHING""",
                    new_rows,
                    template="(%s, %s::halfvec)",
                )
            conn.commit()
    except Exception:
        _safe_rollback(conn)
        raise
    finally:
        _return_connection(conn)
    return [literals[h] for h in hashes]


def keyword_tsquery(text: str) -> str:
    """OR-of-terms tsquery text plus an exact phrase for any section number in the query."""
    words = re.findall(r"[A-Za-z0-9]+", (text or "").lower())
    terms = [w for w in dict.fromkeys(words) if w not in _KEYWORD_STOPWORDS and (len(w) > 1 or w.isdigit())]
    parts = [f"'{w}'" for w in terms]
    for a, b, c in _SECTION_QUERY_RE.findall(text or ""):
        parts.append(f"('{a}' <-> '{b}' <-> '{c}')")
    return " | ".join(parts)


def hybrid_search(
    conn,
    document_ids: list[str],
    query_vector: str,
    keyword_text: str,
    *,
    limit: int,
) -> list[ChunkHit]:
    """Exact vector search + full-text search fused with reciprocal rank fusion."""
    t = _tables()
    scores: dict[tuple[str, int], float] = {}
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT document_id, chunk_index FROM "{t['chunks']}"
                WHERE document_id = ANY(%s::uuid[])
                ORDER BY embedding <=> %s::halfvec
                LIMIT %s""",
            (document_ids, query_vector, VECTOR_CANDIDATES),
        )
        for rank, (doc_id, idx) in enumerate(cur.fetchall()):
            key = (str(doc_id), idx)
            scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank + 1)
        tsquery = keyword_tsquery(keyword_text)
        if tsquery:
            cur.execute(
                f"""SELECT document_id, chunk_index
                    FROM "{t['chunks']}", to_tsquery('english', %s) q
                    WHERE document_id = ANY(%s::uuid[]) AND tsv @@ q
                    ORDER BY ts_rank_cd(tsv, q) DESC
                    LIMIT %s""",
                (tsquery, document_ids, KEYWORD_CANDIDATES),
            )
            for rank, (doc_id, idx) in enumerate(cur.fetchall()):
                key = (str(doc_id), idx)
                scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank + 1)
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)[:limit]
    return [ChunkHit(doc_id, idx, score) for (doc_id, idx), score in ranked]


def fetch_chunks(conn, keys: set[tuple[str, int]]) -> dict[tuple[str, int], ChunkRow]:
    """Load chunk rows for (document_id, chunk_index) keys."""
    if not keys:
        return {}
    t = _tables()
    doc_ids = [k[0] for k in keys]
    indexes = [k[1] for k in keys]
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT c.document_id, c.chunk_index, c.section_number, c.section_title,
                       c.page_start, c.page_end, c.content
                FROM "{t['chunks']}" c
                JOIN unnest(%s::uuid[], %s::int[]) AS k(document_id, chunk_index)
                  ON c.document_id = k.document_id AND c.chunk_index = k.chunk_index""",
            (doc_ids, indexes),
        )
        rows = cur.fetchall()
    return {
        (str(r[0]), r[1]): ChunkRow(str(r[0]), r[1], r[2], r[3], r[4], r[5], r[6])
        for r in rows
    }


def open_search_connection():
    """Connection for a group of searches; caller must pass it to close_search_connection."""
    ensure_vector_schema()
    return _connect()


def close_search_connection(conn) -> None:
    _safe_rollback(conn)
    _return_connection(conn)
