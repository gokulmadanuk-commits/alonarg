"""Tests for the rename (PATCH title) endpoint."""
from __future__ import annotations

from fastapi.testclient import TestClient

from alonarg.db import Database
from alonarg.server import create_app


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


def test_rename_updates_title(tmp_db_path, tmp_path, monkeypatch):
    client, db = _client(tmp_db_path, tmp_path, monkeypatch)
    rid = db.create_recording(status="done", title="Old title")
    r = client.patch(f"/api/recordings/{rid}", json={"title": "New title"})
    assert r.status_code == 200
    assert r.json() == {"id": rid, "title": "New title"}
    assert db.get_recording(rid)["title"] == "New title"
    db.close()


def test_rename_trims_whitespace(tmp_db_path, tmp_path, monkeypatch):
    client, db = _client(tmp_db_path, tmp_path, monkeypatch)
    rid = db.create_recording(status="done", title="x")
    r = client.patch(f"/api/recordings/{rid}", json={"title": "  Spaced  "})
    assert r.status_code == 200
    assert db.get_recording(rid)["title"] == "Spaced"
    db.close()


def test_rename_empty_title_400(tmp_db_path, tmp_path, monkeypatch):
    client, db = _client(tmp_db_path, tmp_path, monkeypatch)
    rid = db.create_recording(status="done", title="keep")
    r = client.patch(f"/api/recordings/{rid}", json={"title": "   "})
    assert r.status_code == 400
    assert db.get_recording(rid)["title"] == "keep"
    db.close()


def test_rename_missing_404(tmp_db_path, tmp_path, monkeypatch):
    client, db = _client(tmp_db_path, tmp_path, monkeypatch)
    r = client.patch("/api/recordings/999999", json={"title": "Nope"})
    assert r.status_code == 404
    db.close()
