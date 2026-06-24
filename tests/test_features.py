"""Tests for the search, ask, and draft-email endpoints (assistant mocked)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from alonarg import assistant
from alonarg.db import Database
from alonarg.server import create_app
from alonarg.types import Segment, SummaryResult, TranscriptResult


def _client(tmp_db_path, tmp_path, monkeypatch):
    from alonarg import config

    monkeypatch.setattr(config, "RECORDINGS_DIR", tmp_path / "recordings")
    db = Database(tmp_db_path)
    app = create_app(
        db=db,
        recorder=object(),
        run_pipeline=lambda *a, **k: None,
        run_ingest=lambda *a, **k: None,
    )
    return TestClient(app), db


def _seed(db):
    a = db.create_recording(status="done", title="Pricing discussion")
    db.set_summary(
        a,
        SummaryResult(
            title="Pricing discussion",
            summary="We set the price at $99.",
            action_items=["Email the client the quote"],
        ),
    )
    db.set_transcript(
        a,
        TranscriptResult(
            segments=[Segment("You", 0, 1, "Let's talk pricing")],
            text="Let's talk pricing and the budget",
            language="en",
        ),
    )
    b = db.create_recording(status="done", title="Design review")
    db.set_summary(b, SummaryResult(title="Design review", summary="Reviewed the new layout."))
    return a, b


def test_search_title(tmp_db_path, tmp_path, monkeypatch):
    client, db = _client(tmp_db_path, tmp_path, monkeypatch)
    a, b = _seed(db)
    ids = [x["id"] for x in client.get("/api/search", params={"q": "design"}).json()]
    assert b in ids and a not in ids
    db.close()


def test_search_transcript(tmp_db_path, tmp_path, monkeypatch):
    client, db = _client(tmp_db_path, tmp_path, monkeypatch)
    a, b = _seed(db)
    ids = [x["id"] for x in client.get("/api/search", params={"q": "budget"}).json()]
    assert a in ids and b not in ids
    db.close()


def test_search_empty_returns_all(tmp_db_path, tmp_path, monkeypatch):
    client, db = _client(tmp_db_path, tmp_path, monkeypatch)
    _seed(db)
    assert len(client.get("/api/search", params={"q": ""}).json()) == 2
    db.close()


def test_ask_per_recording(tmp_db_path, tmp_path, monkeypatch):
    client, db = _client(tmp_db_path, tmp_path, monkeypatch)
    a, b = _seed(db)
    captured = {}

    def fake_ask(question, context, **k):
        captured["ctx"] = context
        return "It was $99."

    monkeypatch.setattr(assistant, "ask", fake_ask)
    r = client.post("/api/ask", json={"question": "What price?", "recording_id": a})
    assert r.status_code == 200
    assert r.json()["answer"] == "It was $99."
    assert r.json()["used"] == [a]
    assert "99" in captured["ctx"]
    db.close()


def test_ask_global(tmp_db_path, tmp_path, monkeypatch):
    client, db = _client(tmp_db_path, tmp_path, monkeypatch)
    a, b = _seed(db)
    monkeypatch.setattr(assistant, "ask", lambda q, ctx, **k: "answer")
    r = client.post("/api/ask", json={"question": "anything?"})
    assert r.status_code == 200
    assert set(r.json()["used"]) == {a, b}
    db.close()


def test_ask_empty_question_400(tmp_db_path, tmp_path, monkeypatch):
    client, db = _client(tmp_db_path, tmp_path, monkeypatch)
    _seed(db)
    assert client.post("/api/ask", json={"question": "  "}).status_code == 400
    db.close()


def test_draft_email(tmp_db_path, tmp_path, monkeypatch):
    client, db = _client(tmp_db_path, tmp_path, monkeypatch)
    a, b = _seed(db)
    monkeypatch.setattr(
        assistant, "draft_email",
        lambda item, ctx="", **k: {"subject": "Quote", "body": "Here is the quote."},
    )
    r = client.post("/api/draft-email", json={"item": "Email the client the quote", "recording_id": a})
    assert r.status_code == 200
    assert r.json() == {"subject": "Quote", "body": "Here is the quote."}
    db.close()


def test_draft_email_empty_400(tmp_db_path, tmp_path, monkeypatch):
    client, db = _client(tmp_db_path, tmp_path, monkeypatch)
    assert client.post("/api/draft-email", json={"item": "   "}).status_code == 400
    db.close()
