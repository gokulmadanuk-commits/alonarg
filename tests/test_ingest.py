"""Tests for the uploaded-audio ingest module.

Exercises ``process_upload`` with injected fake transcribe/summarize functions
(no whisper / Claude CLI) and ``convert_to_wav`` against real ffmpeg (installed).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from alonarg import audio_capture, ingest
from alonarg.db import Database
from alonarg.types import Segment, SummaryResult, TranscriptResult


@pytest.fixture()
def db(tmp_db_path):
    database = Database(tmp_db_path)
    yield database
    database.close()


def test_process_upload_happy_path(db):
    rec_id = db.create_recording(title="Phone recording", status="recording")

    transcript = TranscriptResult(
        segments=[Segment(speaker="Speaker", start=0.0, end=1.0, text="hello world")],
        text="[00:00] Speaker: hello world",
        language="en",
    )
    summary = SummaryResult(
        title="Catch-up",
        summary="A short meeting.",
        action_items=["email the client"],
        next_steps=["ship on Friday"],
    )

    seen = {}

    def fake_transcribe(wav_path):
        seen["wav_path"] = wav_path
        return transcript

    def fake_summarize(text):
        seen["text"] = text
        return summary

    ingest.process_upload(
        db, rec_id, "C:/fake/mixed.wav",
        transcribe_fn=fake_transcribe, summarize_fn=fake_summarize,
    )

    rec = db.get_recording(rec_id)
    assert rec["status"] == "done"
    assert rec["error"] is None
    assert rec["transcript"]["text"] == transcript.text
    assert rec["transcript"]["segments"][0]["speaker"] == "Speaker"
    assert rec["summary"]["summary"] == "A short meeting."
    assert rec["summary"]["action_items"] == ["email the client"]
    # Title updated from the summary.
    assert rec["title"] == "Catch-up"
    # Injected functions received the expected inputs.
    assert seen["wav_path"] == "C:/fake/mixed.wav"
    assert seen["text"] == transcript.text


def test_process_upload_transcribe_error(db):
    rec_id = db.create_recording(title="Phone recording", status="recording")

    def boom(wav_path):
        raise RuntimeError("whisper exploded")

    def fake_summarize(text):  # pragma: no cover - should never be reached
        raise AssertionError("summarize should not run after a transcribe error")

    # Must NOT raise.
    ingest.process_upload(
        db, rec_id, "C:/fake/mixed.wav",
        transcribe_fn=boom, summarize_fn=fake_summarize,
    )

    rec = db.get_recording(rec_id)
    assert rec["status"] == "error"
    assert "whisper exploded" in (rec["error"] or "")
    assert rec["summary"] is None


def test_convert_to_wav(sine_wav, tmp_path):
    src = sine_wav("source.wav", seconds=1.0, freq=440.0, rate=44100)
    dst = tmp_path / "converted.wav"

    ingest.convert_to_wav(src, str(dst))

    assert dst.exists()
    samples, rate = audio_capture.read_wav(str(dst))
    assert rate == 16000
    assert len(samples) > 0
