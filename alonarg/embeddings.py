"""Local text embeddings (Ollama nomic-embed-text) + chunking + cosine top-K.

Low-level, DB-free helpers: HTTP embed calls (with nomic's task prefixes),
transcript chunking with per-chunk timestamps (for citations), float32
(de)packing, and numpy cosine search. Orchestration lives in :mod:`alonarg.brain`.
"""
from __future__ import annotations

import httpx
import numpy as np

from alonarg import config

DIMS = 768
_DOC_PREFIX = "search_document: "
_QUERY_PREFIX = "search_query: "
_available: bool | None = None


def reset_cache() -> None:
    global _available
    _available = None


def available() -> bool:
    """True if the embedding model is installed in the local Ollama (cached)."""
    global _available
    if _available is not None:
        return _available
    try:
        host = config.OLLAMA_HOST.rstrip("/")
        resp = httpx.get(host + "/api/tags", timeout=5)
        names = [m.get("name", "") for m in (resp.json().get("models") or [])]
        target = config.OLLAMA_EMBED_MODEL
        _available = any(n == target or n.split(":")[0] == target for n in names)
    except Exception:  # noqa: BLE001
        _available = False
    return _available


def _normalize(vec) -> np.ndarray:
    a = np.asarray(vec, dtype=np.float32)
    n = float(np.linalg.norm(a))
    return a / n if n > 0 else a


def embed_texts(texts: list[str], prefix: str = _DOC_PREFIX) -> list[np.ndarray]:
    """Embed a batch of texts (unit-normalized). Raises RuntimeError on failure."""
    texts = [t for t in texts]
    if not texts:
        return []
    host = config.OLLAMA_HOST.rstrip("/")
    payload = {"model": config.OLLAMA_EMBED_MODEL, "input": [prefix + (t or "") for t in texts]}
    try:
        resp = httpx.post(host + "/api/embed", json=payload, timeout=config.OLLAMA_TIMEOUT)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Cannot reach Ollama embeddings at {host}: {exc}") from exc
    if resp.status_code != 200:
        raise RuntimeError(f"Ollama embed error {resp.status_code}: {resp.text[:200]}")
    return [_normalize(e) for e in (resp.json().get("embeddings") or [])]


def embed_query(text: str) -> np.ndarray | None:
    vecs = embed_texts([text], prefix=_QUERY_PREFIX)
    return vecs[0] if vecs else None


def pack(vec) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def unpack(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


def top_k(query_vec: np.ndarray, matrix: np.ndarray, k: int) -> list[tuple[int, float]]:
    """Indices + scores of the top-K rows of ``matrix`` by cosine (both unit-norm)."""
    if matrix.shape[0] == 0:
        return []
    scores = matrix @ query_vec
    k = min(k, scores.shape[0])
    idx = np.argpartition(-scores, k - 1)[:k]
    idx = idx[np.argsort(-scores[idx])]
    return [(int(i), float(scores[i])) for i in idx]


def _mk_chunk(group: list[dict], idx: int) -> dict:
    text = " ".join((s.get("text", "") or "").strip() for s in group).strip()
    speakers: list[str] = []
    for s in group:
        sp = s.get("speaker", "")
        if sp and sp not in speakers:
            speakers.append(sp)
    return {
        "source_type": "transcript",
        "chunk_index": idx,
        "start_s": float(group[0].get("start", 0) or 0),
        "end_s": float(group[-1].get("end", 0) or 0),
        "speaker": ", ".join(speakers),
        "text": text,
    }


def chunk_segments(segments: list[dict] | None, max_chars: int = 1800) -> list[dict]:
    """Pack consecutive transcript segments into ~max_chars chunks (1-segment
    overlap), each carrying the first segment's start time for citations."""
    segs = [s for s in (segments or []) if isinstance(s, dict) and (s.get("text") or "").strip()]
    chunks: list[dict] = []
    i = idx = 0
    while i < len(segs):
        group: list[dict] = []
        length = 0
        j = i
        while j < len(segs) and (length < max_chars or not group):
            group.append(segs[j])
            length += len((segs[j].get("text", "") or "")) + 1
            j += 1
        chunks.append(_mk_chunk(group, idx))
        idx += 1
        if j >= len(segs):
            break
        i = max(i + 1, j - 1)  # step back one segment for overlap (always progress)
    # Merge a tiny trailing chunk into the previous one.
    if len(chunks) >= 2 and len(chunks[-1]["text"]) < 200:
        last = chunks.pop()
        chunks[-1]["text"] += " " + last["text"]
        chunks[-1]["end_s"] = last["end_s"]
    return chunks
