"""The "second brain": index recordings into embeddings and search them.

Builds on :mod:`alonarg.embeddings` (embedding + cosine) and the ``chunks`` table
in :mod:`alonarg.db`. The vector matrix is cached in memory and rebuilt lazily
when chunks change. Everything degrades gracefully when the embedding model
isn't installed (``available()`` is False → callers fall back to keyword search).
"""
from __future__ import annotations

import logging
import threading

import numpy as np

from alonarg import config, embeddings

log = logging.getLogger(__name__)

_lock = threading.Lock()
_matrix: np.ndarray | None = None
_meta: list[dict] | None = None
_dirty = True


def available() -> bool:
    return embeddings.available()


def _invalidate() -> None:
    global _dirty
    with _lock:
        _dirty = True


def index_recording(db, rec_id: int) -> int:
    """(Re)embed one recording's transcript + summary into the chunks table.

    Returns the number of chunks stored. Raises RuntimeError if embedding fails.
    """
    rec = db.get_recording(rec_id)
    if rec is None:
        return 0
    segments = (rec.get("transcript") or {}).get("segments") or []
    chunks = embeddings.chunk_segments(segments)
    summary = (rec.get("summary") or {}).get("summary", "")
    if summary.strip():
        chunks.append({
            "source_type": "summary", "chunk_index": len(chunks),
            "start_s": None, "end_s": None, "speaker": "", "text": summary.strip(),
        })
    if not chunks:
        db.replace_chunks(rec_id, [])
        _invalidate()
        return 0
    vecs = embeddings.embed_texts([c["text"] for c in chunks])
    rows = [
        {**c, "embedding": embeddings.pack(v), "embed_model": config.OLLAMA_EMBED_MODEL}
        for c, v in zip(chunks, vecs)
    ]
    db.replace_chunks(rec_id, rows)
    _invalidate()
    return len(rows)


def _ensure_matrix(db) -> None:
    global _matrix, _meta, _dirty
    with _lock:
        if not _dirty and _matrix is not None:
            return
        rows = db.all_chunks()
        if rows:
            _matrix = np.vstack([embeddings.unpack(r["embedding"]) for r in rows]).astype(np.float32)
        else:
            _matrix = np.zeros((0, embeddings.DIMS), dtype=np.float32)
        _meta = [
            {"chunk_id": r["id"], "recording_id": r["recording_id"], "source_type": r["source_type"],
             "start_s": r["start_s"], "end_s": r["end_s"], "text": r["text"]}
            for r in rows
        ]
        _dirty = False


def search(db, query: str, k: int = 10) -> list[dict]:
    """Top-K semantic chunk hits for a query (empty if unavailable/no index)."""
    if not available() or not (query or "").strip():
        return []
    try:
        q = embeddings.embed_query(query)
    except RuntimeError:
        return []
    if q is None:
        return []
    _ensure_matrix(db)
    if _matrix is None or _matrix.shape[0] == 0:
        return []
    hits = embeddings.top_k(q, _matrix, k)
    return [{**_meta[i], "score": score} for i, score in hits]


def reindex_all(db, only_missing: bool = True) -> int:
    """Embed all 'done' recordings (or only those lacking chunks). Returns count done."""
    done_ids = db.recordings_with_chunks() if only_missing else set()
    n = 0
    for row in db.list_recordings():
        if row.get("status") != "done":
            continue
        if only_missing and row["id"] in done_ids:
            continue
        try:
            index_recording(db, row["id"])
            n += 1
        except Exception:  # noqa: BLE001 - keep going; model may be down
            log.warning("indexing failed for recording %s", row["id"], exc_info=True)
    return n


def status(db) -> dict:
    return {
        "available": available(),
        "model": config.OLLAMA_EMBED_MODEL,
        "indexed": len(db.recordings_with_chunks()),
        "chunks": db.count_chunks(),
        "total": len(db.list_recordings()),
    }
