"""Tests for power features: talk-time analytics, note templates, state storage."""
from __future__ import annotations

from alonarg import analytics, summarize
from alonarg.db import Database
from alonarg.types import Segment, SummaryResult, TranscriptResult


# ---- analytics.talk_time --------------------------------------------------
def test_talk_time_basic():
    segs = [
        {"speaker": "You", "start": 0, "end": 10, "text": "hi"},
        {"speaker": "Others", "start": 10, "end": 40, "text": "ok"},
        {"speaker": "You", "start": 40, "end": 50, "text": "bye"},
    ]
    tt = analytics.talk_time(segs)
    assert tt["total_s"] == 50.0
    assert tt["speakers"][0]["speaker"] == "Others"  # most talk-time first
    assert tt["speakers"][0]["seconds"] == 30.0 and tt["speakers"][0]["pct"] == 60
    you = next(s for s in tt["speakers"] if s["speaker"] == "You")
    assert you["seconds"] == 20.0 and you["pct"] == 40


def test_talk_time_empty_and_bad():
    assert analytics.talk_time(None) == {"total_s": 0.0, "speakers": []}
    assert analytics.talk_time([{"speaker": "You", "start": 5, "end": 1, "text": "x"}])["total_s"] == 0.0


# ---- summarize templates --------------------------------------------------
def test_template_choices_includes_known():
    keys = {c["key"] for c in summarize.template_choices()}
    assert {"general", "standup", "one_on_one", "sales", "interview"} <= keys


def test_build_prompt_template_changes_system():
    base_sys, _ = summarize.build_prompt("hello", "general")
    sales_sys, user = summarize.build_prompt("hello", "sales")
    assert "Context for this meeting" not in base_sys
    assert "sales call" in sales_sys.lower()
    assert "hello" in user
    # JSON contract (the four keys) is preserved across templates
    for key in ("title", "summary", "action_items", "next_steps"):
        assert key in sales_sys


def test_build_prompt_unknown_template_falls_back():
    sys, _ = summarize.build_prompt("x", "nope")
    assert "Context for this meeting" not in sys  # behaves like general


# ---- db state_json --------------------------------------------------------
def test_state_roundtrip(tmp_path):
    db = Database(tmp_path / "t.db")
    rid = db.create_recording(status="done", title="M")
    assert db.get_recording(rid)["state"] is None
    db.set_state(rid, {"pinned": True, "tags": ["sales"], "done_actions": ["A"]})
    got = db.get_recording(rid)["state"]
    assert got["pinned"] is True and got["tags"] == ["sales"] and got["done_actions"] == ["A"]
    # state also surfaces in list rows
    assert db.list_recordings()[0]["state"]["pinned"] is True
    db.close()


def test_state_migration_adds_column(tmp_path):
    # Simulate an older DB without state_json, then reopen (migration adds it).
    import sqlite3
    p = tmp_path / "old.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE recordings (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, created_at TEXT, duration_s REAL, status TEXT, error TEXT, mic_path TEXT, system_path TEXT, mixed_path TEXT, transcript_json TEXT, summary_json TEXT, meta_json TEXT)")
    con.execute("INSERT INTO recordings (title) VALUES ('old')")
    con.commit(); con.close()
    db = Database(p)  # should migrate without error
    rid = db.list_recordings()[0]["id"]
    db.set_state(rid, {"pinned": True})
    assert db.get_recording(rid)["state"]["pinned"] is True
    db.close()
