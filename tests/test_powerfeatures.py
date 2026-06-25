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


def test_related_recordings_by_title(tmp_path):
    db = Database(tmp_path / "r.db")
    a = db.create_recording(status="done", title="Weekly sync")
    b = db.create_recording(status="done", title="weekly sync ")  # case/space differ
    c = db.create_recording(status="done", title="Something else")
    ids = [r["id"] for r in db.related_recordings(a)]
    assert b in ids and c not in ids and a not in ids
    assert db.related_recordings(c) == []  # unique title -> no series
    db.close()


# ---- analytics.coaching_metrics ------------------------------------------
def test_coaching_metrics():
    segs = [
        {"speaker": "You", "start": 0, "end": 10, "text": "Um, so what do you think? Like, basically."},
        {"speaker": "You", "start": 10, "end": 40, "text": "I will follow up."},  # consecutive -> monologue
        {"speaker": "Others", "start": 40, "end": 50, "text": "Sounds good."},
    ]
    m = analytics.coaching_metrics(segs)
    assert m["questions_total"] == 1 and m["questions_you"] == 1
    assert m["longest_monologue_speaker"] == "You" and m["longest_monologue_s"] == 40.0
    assert m["filler_count"] >= 3  # um, so, like, basically
    assert m["filler_per_min"] is not None and m["your_wpm"] is not None
    assert analytics.coaching_metrics([]) == {}


# ---- msgraph mail reads ---------------------------------------------------
def test_mail_available(monkeypatch):
    from alonarg import msgraph
    monkeypatch.setattr(msgraph, "get_token", lambda scopes=None: "tok")
    assert msgraph.mail_available() is True
    monkeypatch.setattr(msgraph, "get_token", lambda scopes=None: None)
    assert msgraph.mail_available() is False


def test_read_messages(monkeypatch):
    import httpx
    from alonarg import msgraph
    monkeypatch.setattr(msgraph, "get_token", lambda scopes=None: "tok")

    class FakeResp:
        status_code = 200
        def json(self):
            return {"value": [
                {"subject": "Re: Quote", "from": {"emailAddress": {"address": "jai@x.com"}},
                 "receivedDateTime": "2026-06-20T10:00:00Z", "bodyPreview": "older"},
                {"subject": "Hi", "from": {"emailAddress": {"address": "jai@x.com"}},
                 "receivedDateTime": "2026-06-25T10:00:00Z", "bodyPreview": "newer"},
            ]}

    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResp())
    msgs = msgraph.read_messages("jai@x.com")
    assert len(msgs) == 2
    assert msgs[0]["received"].startswith("2026-06-25") and msgs[0]["subject"] == "Hi"  # newest first


def test_read_messages_no_token(monkeypatch):
    from alonarg import msgraph
    monkeypatch.setattr(msgraph, "get_token", lambda scopes=None: None)
    assert msgraph.read_messages("x@y.com") == []
    assert msgraph.read_messages("") == []


# ---- trackers module ------------------------------------------------------
def test_trackers_module(tmp_path, monkeypatch):
    from alonarg import config, trackers
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    assert trackers.list_trackers() == []
    trackers.add_tracker("pricing")
    trackers.add_tracker("Pricing")  # case-insensitive dedupe
    assert trackers.list_trackers() == ["pricing"]
    trackers.add_tracker("budget")
    trackers.remove_tracker("PRICING")
    assert trackers.list_trackers() == ["budget"]


# ---- auto-enrich ----------------------------------------------------------
def test_enrich_recording(tmp_path, monkeypatch):
    from alonarg import calendars, server
    db = Database(tmp_path / "e.db")
    rid = db.create_recording(status="summarizing", title="Untitled recording")
    monkeypatch.setattr(calendars, "find_event_for", lambda c, **k: {
        "subject": "Q3 Planning",
        "attendees": [{"name": "Jai", "email": "jai@x.com"}, {"name": "Sam", "email": ""}],
    })
    server._enrich_recording(db, rid)
    got = db.get_recording(rid)
    assert got["title"] == "Q3 Planning"
    assert "Jai" in got["meta"]["people"] and "Sam" in got["meta"]["people"]
    assert {"name": "Jai", "email": "jai@x.com", "phone": ""} in got["meta"]["contacts"]
    db.close()


def test_pipeline_runs_enrich(tmp_path):
    from alonarg import pipeline
    from alonarg.types import RecordingResult, SummaryResult, TranscriptResult
    db = Database(tmp_path / "p.db")
    rid = db.create_recording(status="recording")
    called = {}
    pipeline.process_recording(
        db, rid, RecordingResult(mixed_path="x", duration_s=1.0, sample_rate=16000),
        transcribe_fn=lambda mic, sysp: TranscriptResult(text="hi", segments=[], language="en"),
        summarize_fn=lambda text, **k: SummaryResult(title="T", summary="s"),
        enrich_fn=lambda r: called.setdefault("rid", r),
    )
    assert called.get("rid") == rid
    assert db.get_recording(rid)["status"] == "done"
    db.close()


def test_briefs_store(tmp_path, monkeypatch):
    from alonarg import briefs, config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    assert briefs.all() == {}
    briefs.set("k1", {"brief": "hi", "generated_at": "t"})
    assert briefs.get("k1")["brief"] == "hi"
    briefs.set("k2", {"brief": "yo"})
    briefs.prune(["k2"])
    assert briefs.get("k1") is None and briefs.get("k2")["brief"] == "yo"


def test_build_brief_from_invite_only(tmp_path, monkeypatch):
    from alonarg import assistant, msgraph, server
    db = Database(tmp_path / "b.db")
    monkeypatch.setattr(msgraph, "mail_available", lambda: False)
    captured = {}
    monkeypatch.setattr(assistant, "brief", lambda subject, names, ctx, **k: captured.update(ctx=ctx) or "BRIEF")
    ev = {
        "subject": "Woodland sync", "organizer": "Gokul", "body": "Agenda: pricing review",
        "attendees": [{"name": "Jai", "email": "jai@x.com"}],
        "start": "2026-06-29T13:00:00+00:00",
    }
    res = server.build_brief(db, ev)  # no prior recordings at all
    assert res["brief"] == "BRIEF" and res["based_on"] == 0 and res["emails_used"] == 0
    assert "MEETING INVITE" in captured["ctx"]
    assert "Agenda: pricing review" in captured["ctx"] and "Jai" in captured["ctx"]
    db.close()


def test_build_brief_with_emails(tmp_path, monkeypatch):
    from alonarg import assistant, msgraph, server
    db = Database(tmp_path / "b2.db")
    monkeypatch.setattr(msgraph, "mail_available", lambda: True)
    monkeypatch.setattr(msgraph, "read_messages", lambda addr, top=5: [
        {"subject": "Re: pricing", "from": addr, "received": "2026-06-25T10:00:00Z", "preview": "the latest numbers"},
    ])
    captured = {}
    monkeypatch.setattr(assistant, "brief", lambda s, n, ctx, **k: captured.update(ctx=ctx) or "OK")
    ev = {"subject": "Catch up", "attendees": [{"name": "Jai", "email": "jai@x.com"}]}
    res = server.build_brief(db, ev)
    assert res["emails_used"] == 1 and res["mail_available"] is True
    assert "RECENT EMAILS" in captured["ctx"] and "the latest numbers" in captured["ctx"]
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
