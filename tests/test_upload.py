"""Tests for the phone-PWA upload endpoint, CORS, and token auth.

Uses TestClient with a temp DB, a fake recorder, and synchronous pipeline/ingest
stubs -- no real audio devices, whisper, or Claude CLI required.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alonarg import audio_capture, config
from alonarg.db import Database
from alonarg.server import create_app
from alonarg.types import RecordingResult


class FakeRecorder:
    """Stand-in for audio_capture.Recorder with no hardware."""

    def __init__(self):
        self._recording = False

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def elapsed_s(self) -> float:
        return 0.0

    def start(self) -> None:
        self._recording = True

    def stop(self, out_dir) -> RecordingResult:  # pragma: no cover - unused here
        self._recording = False
        return RecordingResult(mixed_path="", duration_s=0.0, sample_rate=16000)


@pytest.fixture()
def token_reset():
    """Save/restore ``config.ALONARG_TOKEN`` so tests don't leak global state."""
    original = config.ALONARG_TOKEN
    try:
        yield
    finally:
        config.ALONARG_TOKEN = original


@pytest.fixture()
def ctx(tmp_db_path, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RECORDINGS_DIR", tmp_path / "recordings")

    db = Database(tmp_db_path)
    recorder = FakeRecorder()
    ingest_calls = []

    def run_ingest(rec_id, wav_path):
        ingest_calls.append((rec_id, wav_path))

    app = create_app(
        db=db,
        recorder=recorder,
        run_pipeline=lambda *a, **k: None,
        run_ingest=run_ingest,
    )
    client = TestClient(app)
    yield {"client": client, "db": db, "ingest_calls": ingest_calls}
    db.close()


def test_upload_happy_path(ctx, sine_wav):
    client = ctx["client"]
    wav_path = sine_wav("clip.wav")
    audio_bytes = Path(wav_path).read_bytes()

    r = client.post(
        "/api/upload",
        files={"file": ("clip.wav", audio_bytes, "audio/wav")},
        data={"title": "Test"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    rec_id = body["id"]
    assert isinstance(rec_id, int)
    assert body["status"] == "transcribing"

    # Row exists via the recordings list.
    listing = client.get("/api/recordings")
    assert listing.status_code == 200
    rows = {row["id"]: row for row in listing.json()}
    assert rec_id in rows
    assert rows[rec_id]["title"] == "Test"
    assert rows[rec_id]["status"] == "transcribing"

    # run_ingest stub was called with (id, wav_path) and the wav really exists.
    assert len(ctx["ingest_calls"]) == 1
    called_id, called_wav = ctx["ingest_calls"][0]
    assert called_id == rec_id
    assert Path(called_wav).exists()
    samples, rate = audio_capture.read_wav(called_wav)
    assert rate == 16000
    assert len(samples) > 0


def test_cors_header_present(ctx):
    client = ctx["client"]
    # A request carrying an Origin triggers the CORS middleware to echo a header.
    r = client.get("/api/recordings", headers={"Origin": "http://example.com"})
    assert r.status_code == 200
    assert "access-control-allow-origin" in {k.lower() for k in r.headers.keys()}


def test_token_auth(tmp_db_path, tmp_path, monkeypatch, token_reset):
    monkeypatch.setattr(config, "RECORDINGS_DIR", tmp_path / "recordings")
    # Set the token BEFORE create_app so the dependency sees it.
    config.ALONARG_TOKEN = "secret"

    db = Database(tmp_db_path)
    app = create_app(
        db=db,
        recorder=FakeRecorder(),
        run_pipeline=lambda *a, **k: None,
        run_ingest=lambda *a, **k: None,
    )
    client = TestClient(app)
    try:
        # No token -> 401.
        assert client.get("/api/recordings").status_code == 401

        # Correct Bearer header -> 200.
        ok = client.get(
            "/api/recordings",
            headers={"Authorization": "Bearer secret"},
        )
        assert ok.status_code == 200

        # /audio with ?token= works; wrong token -> 401.
        wav = (tmp_path / "rec.wav")
        # Reuse the sine factory output via a created recording row + real file.
        import numpy as np
        audio_capture.write_wav(wav, np.zeros(1600, dtype="float32"), 16000)
        rec_id = db.create_recording(status="done")
        db.update_recording(rec_id, mixed_path=str(wav))

        good = client.get(f"/audio/{rec_id}?token=secret")
        assert good.status_code == 200
        assert good.headers["content-type"].startswith("audio")

        assert client.get(f"/audio/{rec_id}?token=wrong").status_code == 401
    finally:
        db.close()
