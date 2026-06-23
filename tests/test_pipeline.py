"""Tests for the post-recording processing pipeline.

These use a real on-disk :class:`Database` but inject fake transcribe/summarize
functions so no whisper model or Claude CLI is required.
"""
from __future__ import annotations

import pytest

from alonarg.db import Database
from alonarg.pipeline import process_recording
from alonarg.types import RecordingResult, Segment, SummaryResult, TranscriptResult


def _recording() -> RecordingResult:
    return RecordingResult(
        mixed_path="dummy/mixed.wav",
        duration_s=12.5,
        sample_rate=16000,
        mic_path="dummy/mic.wav",
        system_path="dummy/system.wav",
    )


def test_process_recording_success(tmp_db_path):
    db = Database(tmp_db_path)
    rec_id = db.create_recording(status="recording")

    transcript = TranscriptResult(
        segments=[Segment(speaker="You", start=0.0, end=2.0, text="Hello team")],
        text="[00:00] You: Hello team",
        language="en",
    )
    summary = SummaryResult(
        title="Weekly Sync",
        summary="The team aligned on the launch.",
        action_items=["Email the client", "Draft the spec"],
        next_steps=["Schedule a follow-up"],
    )

    seen = {}

    def fake_transcribe(mic_path, system_path):
        seen["mic"] = mic_path
        seen["system"] = system_path
        return transcript

    def fake_summarize(text):
        seen["text"] = text
        return summary

    process_recording(
        db,
        rec_id,
        _recording(),
        transcribe_fn=fake_transcribe,
        summarize_fn=fake_summarize,
    )

    # transcribe_fn received the recording's track paths.
    assert seen["mic"] == "dummy/mic.wav"
    assert seen["system"] == "dummy/system.wav"
    # summarize_fn received the rendered transcript text.
    assert seen["text"] == transcript.text

    rec = db.get_recording(rec_id)
    assert rec is not None
    assert rec["status"] == "done"
    assert rec["error"] is None
    # Transcript stored and parsed back.
    assert rec["transcript"]["text"] == transcript.text
    assert rec["transcript"]["segments"][0]["speaker"] == "You"
    # Summary stored and parsed back.
    assert rec["summary"]["summary"] == summary.summary
    assert rec["summary"]["action_items"] == summary.action_items
    # Title updated from the summary title.
    assert rec["title"] == "Weekly Sync"

    db.close()


def test_process_recording_no_title_keeps_default(tmp_db_path):
    db = Database(tmp_db_path)
    rec_id = db.create_recording(status="recording", title="Untitled recording")

    transcript = TranscriptResult(segments=[], text="some text", language="en")
    summary = SummaryResult(title="", summary="No title here.")

    process_recording(
        db,
        rec_id,
        _recording(),
        transcribe_fn=lambda m, s: transcript,
        summarize_fn=lambda t: summary,
    )

    rec = db.get_recording(rec_id)
    assert rec["status"] == "done"
    assert rec["title"] == "Untitled recording"
    db.close()


def test_process_recording_transcribe_error(tmp_db_path):
    db = Database(tmp_db_path)
    rec_id = db.create_recording(status="recording")

    def boom(mic_path, system_path):
        raise RuntimeError("whisper exploded")

    def should_not_run(text):  # pragma: no cover - must not be called
        raise AssertionError("summarize should not run after transcribe fails")

    # Must NOT raise to the caller.
    process_recording(
        db,
        rec_id,
        _recording(),
        transcribe_fn=boom,
        summarize_fn=should_not_run,
    )

    rec = db.get_recording(rec_id)
    assert rec["status"] == "error"
    assert "whisper exploded" in rec["error"]
    db.close()


def test_process_recording_summarize_error(tmp_db_path):
    db = Database(tmp_db_path)
    rec_id = db.create_recording(status="recording")

    transcript = TranscriptResult(segments=[], text="text", language="en")

    def boom(text):
        raise ValueError("claude failed")

    process_recording(
        db,
        rec_id,
        _recording(),
        transcribe_fn=lambda m, s: transcript,
        summarize_fn=boom,
    )

    rec = db.get_recording(rec_id)
    assert rec["status"] == "error"
    assert "claude failed" in rec["error"]
    # Transcript was stored before summarization failed.
    assert rec["transcript"]["text"] == "text"
    db.close()
