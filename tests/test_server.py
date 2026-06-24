"""Tests for the FastAPI server.

Uses TestClient with a temp DB, a fake recorder, and a synchronous pipeline
stub -- no real audio devices, whisper, or Claude CLI required.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from alonarg import audio_capture
from alonarg.db import Database
from alonarg.server import create_app
from alonarg.types import RecordingResult


class FakeRecorder:
    """Stand-in for audio_capture.Recorder with no hardware.

    ``stop`` writes a real (silent) mixed wav into the given out_dir so the
    ``/audio`` route has a genuine file to serve.
    """

    def __init__(self):
        self._recording = False
        self.last_out_dir = None

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def elapsed_s(self) -> float:
        return 3.0 if self._recording else 0.0

    def start(self) -> None:
        if self._recording:
            raise RuntimeError("already recording")
        self._recording = True

    def stop(self, out_dir) -> RecordingResult:
        import numpy as np
        from pathlib import Path

        self._recording = False
        self.last_out_dir = out_dir
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        mixed = out / "mixed.wav"
        audio_capture.write_wav(mixed, np.zeros(1600, dtype="float32"), 16000)
        return RecordingResult(
            mixed_path=str(mixed),
            duration_s=0.1,
            sample_rate=16000,
            mic_path=None,
            system_path=None,
        )


@pytest.fixture()
def ctx(tmp_db_path, tmp_path, monkeypatch):
    # Keep recording folders inside the test tmp dir.
    from alonarg import config

    monkeypatch.setattr(config, "RECORDINGS_DIR", tmp_path / "recordings")

    db = Database(tmp_db_path)
    recorder = FakeRecorder()
    calls = []

    def run_pipeline(rec_id, result):
        calls.append((rec_id, result))

    app = create_app(db=db, recorder=recorder, run_pipeline=run_pipeline)
    client = TestClient(app)
    yield {
        "client": client,
        "db": db,
        "recorder": recorder,
        "calls": calls,
    }
    db.close()


def test_status_not_recording(ctx):
    r = ctx["client"].get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert body["recording"] is False
    assert body["current_id"] is None
    assert "elapsed_s" in body


def test_start_stop_flow(ctx):
    client = ctx["client"]

    # start
    r = client.post("/api/record/start")
    assert r.status_code == 200
    rec_id = r.json()["id"]
    assert isinstance(rec_id, int)

    # status reflects recording
    assert client.get("/api/status").json()["recording"] is True
    assert client.get("/api/status").json()["current_id"] == rec_id

    # second start -> 409
    assert client.post("/api/record/start").status_code == 409

    # stop
    r = client.post("/api/record/stop")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == rec_id
    assert body["status"] == "transcribing"

    # pipeline stub was called with (id, result)
    assert len(ctx["calls"]) == 1
    called_id, called_result = ctx["calls"][0]
    assert called_id == rec_id
    assert isinstance(called_result, RecordingResult)

    # no longer recording
    status = client.get("/api/status").json()
    assert status["recording"] is False
    assert status["current_id"] is None

    # stop again -> 409
    assert client.post("/api/record/stop").status_code == 409


def test_recordings_list_and_get(ctx):
    client = ctx["client"]
    rec_id = ctx["db"].create_recording(status="done", title="Demo")

    listing = client.get("/api/recordings")
    assert listing.status_code == 200
    ids = [row["id"] for row in listing.json()]
    assert rec_id in ids

    one = client.get(f"/api/recordings/{rec_id}")
    assert one.status_code == 200
    assert one.json()["id"] == rec_id

    assert client.get("/api/recordings/999999").status_code == 404


def test_audio_route(ctx, sine_wav):
    client = ctx["client"]
    wav = sine_wav("clip.wav")
    rec_id = ctx["db"].create_recording(status="done")
    ctx["db"].update_recording(rec_id, mixed_path=wav)

    r = client.get(f"/audio/{rec_id}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("audio")

    assert client.get("/audio/999999").status_code == 404


def test_audio_missing_file(ctx):
    client = ctx["client"]
    rec_id = ctx["db"].create_recording(status="done")
    ctx["db"].update_recording(rec_id, mixed_path="C:/nope/does-not-exist.wav")
    assert client.get(f"/audio/{rec_id}").status_code == 404


def test_delete_recording(ctx):
    client = ctx["client"]
    rec_id = ctx["db"].create_recording(status="done")

    r = client.delete(f"/api/recordings/{rec_id}")
    assert r.status_code == 200
    assert r.json() == {"deleted": True}

    assert client.get(f"/api/recordings/{rec_id}").status_code == 404
    # deleting again -> 404
    assert client.delete(f"/api/recordings/{rec_id}").status_code == 404


def test_dashboard_renders(ctx):
    client = ctx["client"]
    ctx["db"].create_recording(status="done", title="Rendered Meeting")
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Alonarg" in r.text
    assert "Rendered Meeting" in r.text
    # left-nav shell with the three views
    assert 'class="sidebar"' in r.text
    for view in ("recordings", "calendar", "settings"):
        assert f'data-view="{view}"' in r.text


def test_detail_page(ctx):
    client = ctx["client"]
    rec_id = ctx["db"].create_recording(status="done", title="Detail Meeting")
    r = client.get(f"/recording/{rec_id}")
    assert r.status_code == 200
    assert "Detail Meeting" in r.text

    assert client.get("/recording/999999").status_code == 404
