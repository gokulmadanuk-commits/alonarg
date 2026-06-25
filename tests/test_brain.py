"""Tests for the second-brain: chunking, cosine, and index/search (embedder mocked)."""
from __future__ import annotations

import numpy as np

from alonarg import brain, embeddings
from alonarg.db import Database
from alonarg.types import Segment, SummaryResult, TranscriptResult


def test_chunk_segments_packs_with_timestamps():
    segs = [{"speaker": "You", "start": i * 5.0, "end": i * 5.0 + 5, "text": "word " * 40} for i in range(10)]
    chunks = embeddings.chunk_segments(segs, max_chars=400)
    assert len(chunks) >= 2
    assert chunks[0]["start_s"] == 0.0
    assert all(c["source_type"] == "transcript" and c["text"] for c in chunks)


def test_chunk_segments_empty():
    assert embeddings.chunk_segments([]) == []
    assert embeddings.chunk_segments(None) == []


def test_top_k_orders_by_cosine():
    m = np.array([[1, 0, 0], [0, 1, 0], [0.7, 0.7, 0]], dtype=np.float32)
    q = np.array([1, 0, 0], dtype=np.float32)
    res = embeddings.top_k(q, m, 2)
    assert res[0][0] == 0 and len(res) == 2


def test_pack_unpack_roundtrip():
    v = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    assert np.allclose(embeddings.unpack(embeddings.pack(v)), v)


def test_index_and_search(tmp_path, monkeypatch):
    db = Database(tmp_path / "b.db")
    rid = db.create_recording(status="done", title="Pricing")
    db.set_transcript(rid, TranscriptResult(
        segments=[Segment("You", 0, 5, "we discussed pricing and the budget")],
        text="we discussed pricing and the budget", language="en"))
    db.set_summary(rid, SummaryResult(title="Pricing", summary="Agreed on $99 pricing."))

    def fake_embed(texts, prefix=""):
        out = []
        for t in texts:
            v = np.zeros(8, dtype=np.float32)
            v[0 if "pricing" in t.lower() else 1] = 1.0
            out.append(v)
        return out

    monkeypatch.setattr(embeddings, "available", lambda: True)
    monkeypatch.setattr(embeddings, "embed_texts", fake_embed)
    monkeypatch.setattr(embeddings, "embed_query", lambda q: np.array([1, 0, 0, 0, 0, 0, 0, 0], dtype=np.float32))
    monkeypatch.setattr(brain, "available", lambda: True)

    n = brain.index_recording(db, rid)
    assert n >= 1 and db.count_chunks() == n
    hits = brain.search(db, "what was the pricing?", k=3)
    assert hits and hits[0]["recording_id"] == rid
    db.close()


def test_reindex_all_only_missing(tmp_path, monkeypatch):
    db = Database(tmp_path / "r.db")
    rid = db.create_recording(status="done", title="M")
    db.set_transcript(rid, TranscriptResult(segments=[Segment("You", 0, 2, "hello there")], text="hello there", language="en"))
    monkeypatch.setattr(embeddings, "available", lambda: True)
    monkeypatch.setattr(embeddings, "embed_texts", lambda texts, prefix="": [np.ones(8, dtype=np.float32) for _ in texts])
    monkeypatch.setattr(brain, "available", lambda: True)
    assert brain.reindex_all(db, only_missing=True) == 1
    assert brain.reindex_all(db, only_missing=True) == 0  # already indexed
    db.close()
