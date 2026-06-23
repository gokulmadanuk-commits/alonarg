"""Tests for ``alonarg.db.Database``.

These run entirely against a temp-file SQLite database; no hardware, model, or
network is required.
"""
from __future__ import annotations

import pytest

from alonarg.db import Database
from alonarg.types import Segment, SummaryResult, TranscriptResult


@pytest.fixture()
def db(tmp_db_path):
    """A fresh Database backed by a temp-file path; closed after the test."""
    database = Database(tmp_db_path)
    try:
        yield database
    finally:
        database.close()


def _sample_transcript() -> TranscriptResult:
    return TranscriptResult(
        segments=[
            Segment(speaker="You", start=0.0, end=1.5, text="Hello there."),
            Segment(speaker="Others", start=1.6, end=3.0, text="Hi, how are you?"),
        ],
        text="[00:00] You: Hello there.\n[00:01] Others: Hi, how are you?",
        language="en",
    )


def _sample_summary() -> SummaryResult:
    return SummaryResult(
        title="Quick sync",
        summary="A short greeting exchange.",
        action_items=["Follow up on greeting", "Schedule next chat"],
        next_steps=["Send recap email"],
    )


# --- schema / construction ------------------------------------------------


def test_init_creates_db_file(tmp_db_path):
    assert not tmp_db_path.exists()
    database = Database(tmp_db_path)
    try:
        assert tmp_db_path.exists()
        # Fresh DB has no recordings.
        assert database.list_recordings() == []
    finally:
        database.close()


def test_reopen_existing_db_keeps_rows(tmp_db_path):
    first = Database(tmp_db_path)
    rec_id = first.create_recording(title="Persisted")
    first.close()

    second = Database(tmp_db_path)
    try:
        row = second.get_recording(rec_id)
        assert row is not None
        assert row["title"] == "Persisted"
    finally:
        second.close()


# --- create / get round-trip ----------------------------------------------


def test_create_defaults(db):
    rec_id = db.create_recording()
    assert isinstance(rec_id, int)

    row = db.get_recording(rec_id)
    assert row is not None
    assert row["id"] == rec_id
    assert row["title"] == "Untitled recording"
    assert row["status"] == "recording"
    assert row["duration_s"] == 0.0
    assert row["error"] is None
    assert row["mic_path"] is None
    assert row["system_path"] is None
    assert row["mixed_path"] is None
    assert row["transcript"] is None
    assert row["summary"] is None
    # created_at default is an ISO-8601 UTC timestamp.
    assert isinstance(row["created_at"], str) and row["created_at"]


def test_create_with_fields_round_trip(db):
    rec_id = db.create_recording(
        title="Standup",
        status="transcribing",
        created_at="2026-01-01T00:00:00+00:00",
        mic_path="C:/rec/mic.wav",
        system_path="C:/rec/system.wav",
        mixed_path="C:/rec/mixed.wav",
        duration_s=42.5,
    )
    row = db.get_recording(rec_id)
    assert row["title"] == "Standup"
    assert row["status"] == "transcribing"
    assert row["created_at"] == "2026-01-01T00:00:00+00:00"
    assert row["mic_path"] == "C:/rec/mic.wav"
    assert row["system_path"] == "C:/rec/system.wav"
    assert row["mixed_path"] == "C:/rec/mixed.wav"
    assert row["duration_s"] == 42.5


def test_get_missing_returns_none(db):
    assert db.get_recording(99999) is None


# --- update_recording ------------------------------------------------------


def test_update_recording_changes_fields(db):
    rec_id = db.create_recording()
    db.update_recording(rec_id, title="Renamed", duration_s=10.0)
    row = db.get_recording(rec_id)
    assert row["title"] == "Renamed"
    assert row["duration_s"] == 10.0


def test_update_recording_rejects_unknown_column(db):
    rec_id = db.create_recording()
    with pytest.raises(ValueError):
        db.update_recording(rec_id, bogus_column="x")


def test_update_recording_noop_with_no_fields(db):
    rec_id = db.create_recording(title="Keep")
    db.update_recording(rec_id)  # should not raise
    assert db.get_recording(rec_id)["title"] == "Keep"


# --- set_status ------------------------------------------------------------


def test_set_status_transitions(db):
    rec_id = db.create_recording()
    for status in ("transcribing", "summarizing", "done"):
        db.set_status(rec_id, status)
        assert db.get_recording(rec_id)["status"] == status
    # No error set along the happy path.
    assert db.get_recording(rec_id)["error"] is None


def test_set_status_with_error(db):
    rec_id = db.create_recording()
    db.set_status(rec_id, "error", "boom: device unavailable")
    row = db.get_recording(rec_id)
    assert row["status"] == "error"
    assert row["error"] == "boom: device unavailable"


def test_set_status_clears_error_when_omitted(db):
    rec_id = db.create_recording()
    db.set_status(rec_id, "error", "first failure")
    assert db.get_recording(rec_id)["error"] == "first failure"
    # A subsequent status without an error resets the error column.
    db.set_status(rec_id, "transcribing")
    assert db.get_recording(rec_id)["error"] is None


# --- transcript / summary --------------------------------------------------


def test_set_transcript_round_trip(db):
    rec_id = db.create_recording()
    transcript = _sample_transcript()
    db.set_transcript(rec_id, transcript)

    row = db.get_recording(rec_id)
    assert row["transcript"] == transcript.to_dict()
    # Reconstructable into the dataclass equal to the original.
    assert TranscriptResult.from_dict(row["transcript"]) == transcript


def test_set_summary_round_trip(db):
    rec_id = db.create_recording()
    summary = _sample_summary()
    db.set_summary(rec_id, summary)

    row = db.get_recording(rec_id)
    assert row["summary"] == summary.to_dict()
    assert SummaryResult.from_dict(row["summary"]) == summary


def test_transcript_and_summary_independent(db):
    rec_id = db.create_recording()
    db.set_transcript(rec_id, _sample_transcript())
    # summary still absent
    assert db.get_recording(rec_id)["summary"] is None
    db.set_summary(rec_id, _sample_summary())
    row = db.get_recording(rec_id)
    assert row["transcript"] is not None
    assert row["summary"] is not None


# --- list ordering ---------------------------------------------------------


def test_list_recordings_newest_first(db):
    a = db.create_recording(title="A", created_at="2026-01-01T00:00:00+00:00")
    b = db.create_recording(title="B", created_at="2026-02-01T00:00:00+00:00")
    c = db.create_recording(title="C", created_at="2026-03-01T00:00:00+00:00")

    rows = db.list_recordings()
    titles = [r["title"] for r in rows]
    assert titles == ["C", "B", "A"]
    ids = [r["id"] for r in rows]
    assert ids == [c, b, a]


def test_list_recordings_ties_break_by_id_desc(db):
    same = "2026-01-01T00:00:00+00:00"
    first = db.create_recording(title="first", created_at=same)
    second = db.create_recording(title="second", created_at=same)
    rows = db.list_recordings()
    assert [r["id"] for r in rows[:2]] == [second, first]


def test_list_rows_include_required_fields_and_summary(db):
    rec_id = db.create_recording(title="With summary", duration_s=12.0)
    db.set_summary(rec_id, _sample_summary())
    # transcript present but should not break list (and may be omitted from row)
    db.set_transcript(rec_id, _sample_transcript())

    rows = db.list_recordings()
    assert len(rows) == 1
    row = rows[0]
    for key in ("id", "status", "title", "created_at", "duration_s"):
        assert key in row
    assert row["summary"] == _sample_summary().to_dict()
    # Large transcript body omitted from list rows.
    assert "transcript_json" not in row


def test_list_summary_none_when_absent(db):
    db.create_recording(title="No summary")
    rows = db.list_recordings()
    assert rows[0]["summary"] is None


# --- delete ----------------------------------------------------------------


def test_delete_recording_returns_true_then_false(db):
    rec_id = db.create_recording()
    assert db.delete_recording(rec_id) is True
    assert db.get_recording(rec_id) is None
    # Deleting again reports no row existed.
    assert db.delete_recording(rec_id) is False


def test_delete_missing_returns_false(db):
    assert db.delete_recording(424242) is False
