"""Hybrid retrieval and excerpt assembly for chat and checklist auto-fill."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from utils.vector_store import (
    ChunkHit,
    ChunkRow,
    close_search_connection,
    embed_queries,
    fetch_chunks,
    hybrid_search,
    open_search_connection,
)

CHAT_TOP_K = 8
CHAT_CONTEXT_CHARS = 36_000
CHECKLIST_TOP_K = 4
CHECKLIST_CONTEXT_CHARS = 80_000
NEIGHBOR_WINDOW = 1


@dataclass
class Excerpt:
    excerpt_id: str
    file_name: str
    section_number: str | None
    section_title: str | None
    page_start: int
    page_end: int
    text: str
    keys: set[tuple[str, int]]


def _with_neighbors(hit: ChunkHit) -> list[tuple[str, int]]:
    return [
        (hit.document_id, hit.chunk_index + offset)
        for offset in range(-NEIGHBOR_WINDOW, NEIGHBOR_WINDOW + 1)
        if hit.chunk_index + offset >= 0
    ]


def _build_excerpts(
    rows: dict[tuple[str, int], ChunkRow],
    names_by_doc: dict[str, str],
) -> list[Excerpt]:
    """Merge contiguous chunks of the same section into excerpts, ordered by document and page."""
    excerpts: list[Excerpt] = []
    run: list[ChunkRow] = []

    def flush() -> None:
        if not run:
            return
        first = run[0]
        excerpts.append(
            Excerpt(
                excerpt_id=f"E{len(excerpts) + 1}",
                file_name=names_by_doc.get(first.document_id, "uploaded PDF"),
                section_number=first.section_number,
                section_title=first.section_title,
                page_start=min(r.page_start for r in run),
                page_end=max(r.page_end for r in run),
                text="\n".join(r.content for r in run),
                keys={(r.document_id, r.chunk_index) for r in run},
            )
        )
        run.clear()

    for key in sorted(rows):
        row = rows[key]
        if run and (
            row.document_id != run[-1].document_id
            or row.chunk_index != run[-1].chunk_index + 1
            or row.section_number != run[-1].section_number
        ):
            flush()
        run.append(row)
    flush()
    return excerpts


def format_excerpts(excerpts: list[Excerpt]) -> str:
    """Render excerpts as a tagged block with file, section, and PDF page headers."""
    if not excerpts:
        return "<excerpts>\nNo matching passages were found in the uploaded specification PDF(s).\n</excerpts>"
    blocks: list[str] = []
    for ex in excerpts:
        section = f"Section {ex.section_number} {ex.section_title or ''}".strip() if ex.section_number else "Section not labeled"
        pages = f"PDF page {ex.page_start}" if ex.page_start == ex.page_end else f"PDF pages {ex.page_start}-{ex.page_end}"
        blocks.append(f"[{ex.excerpt_id}] File: {ex.file_name} | {section} | {pages}\n{ex.text}")
    return "<excerpts>\n" + "\n\n".join(blocks) + "\n</excerpts>"


def _select_keys(
    ranked_hits: list[list[ChunkHit]],
    rows_budget_chars: int,
    conn,
) -> dict[tuple[str, int], ChunkRow]:
    """Round-robin over each query's ranked hits, adding hits plus neighbors until the budget is full."""
    wanted: list[tuple[str, int]] = []
    for hits in ranked_hits:
        for hit in hits:
            wanted.extend(_with_neighbors(hit))
    rows = fetch_chunks(conn, set(wanted))
    selected: dict[tuple[str, int], ChunkRow] = {}
    used = 0
    depth = max((len(h) for h in ranked_hits), default=0)
    for rank in range(depth):
        for hits in ranked_hits:
            if rank >= len(hits):
                continue
            hit = hits[rank]
            hit_row = rows.get((hit.document_id, hit.chunk_index))
            if hit_row is None:
                continue
            for key in _with_neighbors(hit):
                row = rows.get(key)
                if row is None or key in selected or row.section_number != hit_row.section_number:
                    continue
                if used + len(row.content) > rows_budget_chars and selected:
                    return selected
                selected[key] = row
                used += len(row.content)
    return selected


def retrieve_for_chat(
    client,
    documents: list[dict[str, Any]],
    query: str,
) -> list[Excerpt]:
    """Top hybrid hits for one question, expanded with neighboring passages."""
    doc_ids = [d["document_id"] for d in documents]
    names = {d["document_id"]: d["file_name"] for d in documents}
    [query_vector] = embed_queries(client, [query])
    conn = open_search_connection()
    try:
        hits = hybrid_search(conn, doc_ids, query_vector, query, limit=CHAT_TOP_K)
        rows = _select_keys([hits], CHAT_CONTEXT_CHARS, conn)
    finally:
        close_search_connection(conn)
    return _build_excerpts(rows, names)


def retrieve_for_checklist(
    client,
    documents: list[dict[str, Any]],
    queries: list[tuple[int, str, str]],
) -> tuple[list[Excerpt], dict[int, list[str]]]:
    """Retrieve excerpts for a checklist batch.

    ``queries`` holds (row_index, semantic_query, keyword_query). Returns the shared
    excerpt list and the excerpt ids most relevant to each row.
    """
    doc_ids = [d["document_id"] for d in documents]
    names = {d["document_id"]: d["file_name"] for d in documents}
    vectors = embed_queries(client, [q[1] for q in queries])
    conn = open_search_connection()
    try:
        ranked = [
            hybrid_search(conn, doc_ids, vector, keyword, limit=CHECKLIST_TOP_K)
            for (_, _, keyword), vector in zip(queries, vectors)
        ]
        rows = _select_keys(ranked, CHECKLIST_CONTEXT_CHARS, conn)
    finally:
        close_search_connection(conn)
    excerpts = _build_excerpts(rows, names)
    refs: dict[int, list[str]] = {}
    for (row_index, _, _), hits in zip(queries, ranked):
        hit_keys = {(h.document_id, h.chunk_index) for h in hits}
        refs[row_index] = [ex.excerpt_id for ex in excerpts if ex.keys & hit_keys]
    return excerpts, refs
