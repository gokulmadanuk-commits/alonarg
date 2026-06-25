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


def test_ask_global_includes_computed_stats(tmp_db_path, tmp_path, monkeypatch):
    client, db = _client(tmp_db_path, tmp_path, monkeypatch)
    _seed(db)  # one meeting has 1 action item, the other has none; neither has next steps
    captured = {}
    monkeypatch.setattr(assistant, "ask", lambda q, ctx, **k: captured.update(ctx=ctx) or "ok")
    r = client.post("/api/ask", json={"question": "how many meetings have no action items?"})
    assert r.status_code == 200
    ctx = captured["ctx"]
    assert "Total meetings: 2" in ctx
    assert "Meetings WITH action items: 1" in ctx
    assert "Meetings WITH NO action items: 1" in ctx
    assert "Meetings WITH NO next steps: 2" in ctx
    # per-meeting metadata table carries the counts too
    assert "action_items: 1" in ctx and "action_items: 0" in ctx
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


def test_update_summary_full(tmp_db_path, tmp_path, monkeypatch):
    client, db = _client(tmp_db_path, tmp_path, monkeypatch)
    a, _ = _seed(db)
    r = client.put(f"/api/recordings/{a}/summary",
                   json={"summary": "Edited.", "action_items": ["A", " B "], "next_steps": ["C"]})
    assert r.status_code == 200
    body = r.json()
    assert body["summary"] == "Edited."
    assert body["action_items"] == ["A", "B"]
    assert body["next_steps"] == ["C"]
    assert body["title"] == "Pricing discussion"  # title preserved
    got = db.get_recording(a)["summary"]
    assert got["summary"] == "Edited." and got["action_items"] == ["A", "B"]
    db.close()


def test_update_summary_partial(tmp_db_path, tmp_path, monkeypatch):
    client, db = _client(tmp_db_path, tmp_path, monkeypatch)
    a, _ = _seed(db)
    r = client.put(f"/api/recordings/{a}/summary", json={"action_items": ["only"]})
    assert r.status_code == 200
    got = db.get_recording(a)["summary"]
    assert got["summary"] == "We set the price at $99."  # unchanged
    assert got["action_items"] == ["only"]
    db.close()


def test_update_summary_404(tmp_db_path, tmp_path, monkeypatch):
    client, db = _client(tmp_db_path, tmp_path, monkeypatch)
    assert client.put("/api/recordings/999999/summary", json={"summary": "x"}).status_code == 404
    db.close()


def test_update_meta_cleans(tmp_db_path, tmp_path, monkeypatch):
    client, db = _client(tmp_db_path, tmp_path, monkeypatch)
    a, _ = _seed(db)
    r = client.put(f"/api/recordings/{a}/meta", json={
        "people": ["Jai", "  ", "Graham"],
        "contacts": [{"name": "Jai", "email": "jai@x.com", "phone": ""},
                     {"name": "", "email": "", "phone": ""}],
    })
    assert r.status_code == 200
    body = r.json()
    assert body["people"] == ["Jai", "Graham"]
    assert body["contacts"] == [{"name": "Jai", "email": "jai@x.com", "phone": ""}]
    assert db.get_recording(a)["meta"]["people"] == ["Jai", "Graham"]
    db.close()


def test_update_meta_404(tmp_db_path, tmp_path, monkeypatch):
    client, db = _client(tmp_db_path, tmp_path, monkeypatch)
    assert client.put("/api/recordings/999999/meta", json={"people": ["x"]}).status_code == 404
    db.close()


def test_detect_merges_and_saves(tmp_db_path, tmp_path, monkeypatch):
    client, db = _client(tmp_db_path, tmp_path, monkeypatch)
    a, _ = _seed(db)
    db.set_meta(a, {"people": ["Existing"], "contacts": []})
    monkeypatch.setattr(
        assistant, "extract_details",
        lambda tr, summ="", **k: {"people": ["Detected"],
                                  "contacts": [{"name": "D", "email": "d@x.com", "phone": ""}]},
    )
    r = client.post(f"/api/recordings/{a}/detect")
    assert r.status_code == 200
    body = r.json()
    assert "Existing" in body["people"] and "Detected" in body["people"]
    assert body["contacts"] == [{"name": "D", "email": "d@x.com", "phone": ""}]
    assert "Detected" in db.get_recording(a)["meta"]["people"]
    db.close()


def test_detect_404(tmp_db_path, tmp_path, monkeypatch):
    client, db = _client(tmp_db_path, tmp_path, monkeypatch)
    assert client.post("/api/recordings/999999/detect").status_code == 404
    db.close()


def test_sync_calendar(tmp_db_path, tmp_path, monkeypatch):
    from alonarg import calendars
    client, db = _client(tmp_db_path, tmp_path, monkeypatch)
    rid = db.create_recording(status="done", title="Untitled recording")
    monkeypatch.setattr(calendars, "find_event_for", lambda created_at, **k: {
        "subject": "Q3 Planning",
        "attendees": [{"name": "Jai", "email": "jai@acme.com"}, {"name": "Graham", "email": ""}],
    })
    r = client.post(f"/api/recordings/{rid}/sync-calendar")
    assert r.status_code == 200
    body = r.json()
    assert body["matched"] is True and body["title_updated"] is True
    got = db.get_recording(rid)
    assert got["title"] == "Q3 Planning"
    assert "Jai" in got["meta"]["people"] and "Graham" in got["meta"]["people"]
    assert {"name": "Jai", "email": "jai@acme.com", "phone": ""} in got["meta"]["contacts"]
    db.close()


def test_sync_calendar_no_match_keeps_title(tmp_db_path, tmp_path, monkeypatch):
    from alonarg import calendars
    client, db = _client(tmp_db_path, tmp_path, monkeypatch)
    rid = db.create_recording(status="done", title="Keep me")
    monkeypatch.setattr(calendars, "find_event_for", lambda created_at, **k: None)
    r = client.post(f"/api/recordings/{rid}/sync-calendar")
    assert r.status_code == 200 and r.json()["matched"] is False
    assert db.get_recording(rid)["title"] == "Keep me"
    db.close()


def test_sync_calendar_404(tmp_db_path, tmp_path, monkeypatch):
    client, db = _client(tmp_db_path, tmp_path, monkeypatch)
    assert client.post("/api/recordings/999999/sync-calendar").status_code == 404
    db.close()


def test_nudge_status(tmp_db_path, tmp_path, monkeypatch):
    client, db = _client(tmp_db_path, tmp_path, monkeypatch)
    r = client.get("/api/nudge/status")
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"recording", "live_meeting", "nudgeable"}
    assert body["live_meeting"] is None and body["nudgeable"] is False
    db.close()


def test_push_key(tmp_db_path, tmp_path, monkeypatch):
    from alonarg import push
    monkeypatch.setattr(push, "public_key", lambda: "PUBKEY")
    client, db = _client(tmp_db_path, tmp_path, monkeypatch)
    assert client.get("/api/push/key").json() == {"key": "PUBKEY"}
    db.close()


def test_push_subscribe_and_test(tmp_db_path, tmp_path, monkeypatch):
    from alonarg import push
    added = {}
    monkeypatch.setattr(push, "add_subscription", lambda s: added.update(s))
    monkeypatch.setattr(push, "send_to_all", lambda *a, **k: {"sent": 1, "removed": 0})
    client, db = _client(tmp_db_path, tmp_path, monkeypatch)
    r = client.post("/api/push/subscribe", json={"endpoint": "https://x/1", "keys": {"p256dh": "a", "auth": "b"}})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert added["endpoint"] == "https://x/1"
    assert client.post("/api/push/test").json() == {"sent": 1, "removed": 0}
    db.close()


def test_graph_status(tmp_db_path, tmp_path, monkeypatch):
    from alonarg import msgraph
    monkeypatch.setattr(msgraph, "is_signed_in", lambda: False)
    monkeypatch.setattr(msgraph, "account_name", lambda: None)
    monkeypatch.setattr(msgraph, "login_pending", lambda: False)
    client, db = _client(tmp_db_path, tmp_path, monkeypatch)
    r = client.get("/api/graph/status")
    assert r.status_code == 200 and r.json()["signed_in"] is False
    assert r.json()["mail"] is False  # not signed in -> no mail
    db.close()


def test_graph_login(tmp_db_path, tmp_path, monkeypatch):
    from alonarg import msgraph
    monkeypatch.setattr(msgraph, "start_device_login", lambda: {
        "user_code": "ABCD-EFGH", "verification_uri": "https://microsoft.com/devicelogin",
        "message": "go", "expires_in": 900,
    })
    client, db = _client(tmp_db_path, tmp_path, monkeypatch)
    r = client.post("/api/graph/login")
    assert r.status_code == 200 and r.json()["user_code"] == "ABCD-EFGH"
    db.close()


def test_graph_logout(tmp_db_path, tmp_path, monkeypatch):
    from alonarg import msgraph
    cleared = {}
    monkeypatch.setattr(msgraph, "sign_out", lambda: cleared.setdefault("x", True))
    client, db = _client(tmp_db_path, tmp_path, monkeypatch)
    r = client.post("/api/graph/logout")
    assert r.status_code == 200 and r.json() == {"signed_in": False}
    assert cleared.get("x")
    db.close()


def test_calendar_events_connected(tmp_db_path, tmp_path, monkeypatch):
    from alonarg import autorecord, calendars, config, msgraph
    client, db = _client(tmp_db_path, tmp_path, monkeypatch)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(msgraph, "is_signed_in", lambda: True)
    evs = [{"id": "e1", "subject": "Sync",
            "start": "2026-06-24T10:00:00+00:00", "end": "2026-06-24T10:30:00+00:00"}]
    monkeypatch.setattr(calendars, "upcoming_events", lambda days=7: [dict(e) for e in evs])
    autorecord.approve({"id": "e1"})
    r = client.get("/api/calendar/events")
    assert r.status_code == 200
    data = r.json()
    assert data["connected"] is True
    assert data["events"][0]["key"] == "e1"
    assert data["events"][0]["auto_record"] is True
    db.close()


def test_calendar_events_not_connected(tmp_db_path, tmp_path, monkeypatch):
    from alonarg import calendars, msgraph
    client, db = _client(tmp_db_path, tmp_path, monkeypatch)
    monkeypatch.setattr(msgraph, "is_signed_in", lambda: False)

    def boom(days=7):
        raise RuntimeError("Classic Outlook isn't running")

    monkeypatch.setattr(calendars, "upcoming_events", boom)
    r = client.get("/api/calendar/events")
    assert r.status_code == 200 and r.json()["connected"] is False
    db.close()


def test_calendar_autorecord_toggle(tmp_db_path, tmp_path, monkeypatch):
    from alonarg import autorecord, config
    client, db = _client(tmp_db_path, tmp_path, monkeypatch)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    ev = {"id": "e5", "subject": "Demo", "start": "s", "end": "e"}
    r = client.post("/api/calendar/autorecord", json={"event": ev, "enabled": True})
    assert r.status_code == 200 and r.json()["enabled"] is True
    assert autorecord.approved_keys() == {"e5"}
    r2 = client.post("/api/calendar/autorecord", json={"event": ev, "enabled": False})
    assert r2.status_code == 200 and r2.json()["enabled"] is False
    assert autorecord.approved_keys() == set()
    db.close()


def test_system_info(tmp_db_path, tmp_path, monkeypatch):
    from alonarg import config, msgraph
    client, db = _client(tmp_db_path, tmp_path, monkeypatch)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(msgraph, "is_signed_in", lambda: False)
    db.create_recording(status="done", title="One")
    r = client.get("/api/system/info")
    assert r.status_code == 200
    body = r.json()
    assert body["recordings_count"] == 1
    assert "data_dir" in body and "model" in body
    assert body["calendar_connected"] is False
    db.close()


def test_resummarize_with_template(tmp_db_path, tmp_path, monkeypatch):
    from alonarg import summarize
    client, db = _client(tmp_db_path, tmp_path, monkeypatch)
    a, _ = _seed(db)
    captured = {}

    def fake_sum(text, template="general", **k):
        captured["template"] = template
        return SummaryResult(title="model title", summary="Standup notes", action_items=["x"])

    monkeypatch.setattr(summarize, "summarize", fake_sum)
    r = client.post(f"/api/recordings/{a}/resummarize", json={"template": "standup"})
    assert r.status_code == 200
    assert captured["template"] == "standup"
    body = r.json()
    assert body["template"] == "standup" and body["summary"]["summary"] == "Standup notes"
    got = db.get_recording(a)
    assert got["summary"]["summary"] == "Standup notes"
    assert got["summary"]["title"] == "Pricing discussion"  # user title preserved
    assert got["state"]["template"] == "standup"
    db.close()


def test_resummarize_no_transcript_400(tmp_db_path, tmp_path, monkeypatch):
    client, db = _client(tmp_db_path, tmp_path, monkeypatch)
    rid = db.create_recording(status="done", title="No transcript")
    assert client.post(f"/api/recordings/{rid}/resummarize", json={"template": "general"}).status_code == 400
    db.close()


def test_update_state_tags_pin(tmp_db_path, tmp_path, monkeypatch):
    client, db = _client(tmp_db_path, tmp_path, monkeypatch)
    a, _ = _seed(db)
    r = client.put(f"/api/recordings/{a}/state", json={"tags": ["Sales", "  "], "pinned": True})
    assert r.status_code == 200
    body = r.json()
    assert body["tags"] == ["Sales"] and body["pinned"] is True
    # partial update preserves the other key
    r2 = client.put(f"/api/recordings/{a}/state", json={"pinned": False})
    assert r2.json()["pinned"] is False and r2.json()["tags"] == ["Sales"]
    db.close()


def test_trackers_endpoints(tmp_db_path, tmp_path, monkeypatch):
    from alonarg import config
    client, db = _client(tmp_db_path, tmp_path, monkeypatch)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    _seed(db)  # "Pricing discussion" with summary mentioning the price
    data = client.post("/api/trackers", json={"term": "pricing"}).json()
    pricing = next(t for t in data if t["term"] == "pricing")
    assert pricing["count"] >= 1
    assert any(t["term"] == "pricing" for t in client.get("/api/trackers").json())
    after = client.post("/api/trackers/delete", json={"term": "pricing"}).json()
    assert all(t["term"] != "pricing" for t in after)
    db.close()


def test_brief_with_history(tmp_db_path, tmp_path, monkeypatch):
    from alonarg import assistant
    client, db = _client(tmp_db_path, tmp_path, monkeypatch)
    _seed(db)
    captured = {}

    def fake_brief(subject, attendees, context, **k):
        captured["ctx"] = context
        return "Last time you set pricing at $99."

    monkeypatch.setattr(assistant, "brief", fake_brief)
    r = client.post("/api/calendar/brief", json={"subject": "Pricing discussion", "attendees": ["Jai"]})
    assert r.status_code == 200
    body = r.json()
    assert body["based_on"] >= 1 and "99" in captured["ctx"]
    assert body["brief"] == "Last time you set pricing at $99."
    db.close()


def test_brief_no_history(tmp_db_path, tmp_path, monkeypatch):
    client, db = _client(tmp_db_path, tmp_path, monkeypatch)
    r = client.post("/api/calendar/brief", json={"subject": "Brand new meeting zzz", "attendees": []})
    assert r.status_code == 200 and r.json()["based_on"] == 0
    db.close()


def test_briefs_list_and_generate(tmp_db_path, tmp_path, monkeypatch):
    from alonarg import assistant, autorecord, calendars, config, msgraph
    client, db = _client(tmp_db_path, tmp_path, monkeypatch)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(msgraph, "is_signed_in", lambda: True)
    monkeypatch.setattr(msgraph, "mail_available", lambda: False)
    ev = {
        "id": "e1", "subject": "Woodland sync",
        "start": "2026-12-01T13:00:00+00:00", "end": "2026-12-01T13:30:00+00:00",
        "attendees": [{"name": "Jai", "email": "jai@x.com"}], "organizer": "Gokul", "body": "agenda",
    }
    monkeypatch.setattr(calendars, "upcoming_events", lambda days=14: [dict(ev)])
    autorecord.approve(ev)  # flag this meeting for recording

    data = client.get("/api/briefs").json()
    assert data["connected"] is True
    assert len(data["briefs"]) == 1 and data["briefs"][0]["has_brief"] is False

    monkeypatch.setattr(assistant, "brief", lambda *a, **k: "Generated brief")
    g = client.post("/api/briefs/generate", json={"key": "e1"}).json()
    assert g["brief"] == "Generated brief" and g["key"] == "e1"

    data2 = client.get("/api/briefs").json()
    assert data2["briefs"][0]["has_brief"] is True
    assert data2["briefs"][0]["brief"] == "Generated brief"
    db.close()


def test_briefs_generate_unknown_key_404(tmp_db_path, tmp_path, monkeypatch):
    from alonarg import calendars
    client, db = _client(tmp_db_path, tmp_path, monkeypatch)
    monkeypatch.setattr(calendars, "upcoming_events", lambda days=14: [])
    assert client.post("/api/briefs/generate", json={"key": "nope"}).status_code == 404
    db.close()


def test_brief_includes_emails(tmp_db_path, tmp_path, monkeypatch):
    from alonarg import assistant, msgraph
    client, db = _client(tmp_db_path, tmp_path, monkeypatch)
    _seed(db)
    monkeypatch.setattr(msgraph, "mail_available", lambda: True)
    monkeypatch.setattr(msgraph, "read_messages", lambda addr, top=5: [
        {"subject": "Quote", "from": addr, "received": "2026-06-25T10:00:00Z", "preview": "the quote is ready"},
    ])
    captured = {}
    monkeypatch.setattr(assistant, "brief", lambda subject, attendees, ctx, **k: captured.update(ctx=ctx) or "ok")
    r = client.post("/api/calendar/brief", json={
        "subject": "Pricing discussion", "attendees": ["Jai"], "emails": ["jai@x.com"],
    })
    assert r.status_code == 200
    assert r.json()["emails_used"] >= 1
    assert "RECENT EMAILS" in captured["ctx"] and "the quote is ready" in captured["ctx"]
    db.close()


def test_brief_skips_email_when_not_granted(tmp_db_path, tmp_path, monkeypatch):
    from alonarg import assistant, msgraph
    client, db = _client(tmp_db_path, tmp_path, monkeypatch)
    _seed(db)
    called = {"read": False}
    monkeypatch.setattr(msgraph, "mail_available", lambda: False)
    monkeypatch.setattr(msgraph, "read_messages", lambda *a, **k: called.update(read=True) or [])
    monkeypatch.setattr(assistant, "brief", lambda *a, **k: "ok")
    r = client.post("/api/calendar/brief", json={
        "subject": "Pricing discussion", "attendees": ["Jai"], "emails": ["jai@x.com"],
    })
    assert r.status_code == 200 and r.json()["emails_used"] == 0
    assert called["read"] is False  # never reads mail without consent
    db.close()


def test_action_items_hub_and_toggle(tmp_db_path, tmp_path, monkeypatch):
    client, db = _client(tmp_db_path, tmp_path, monkeypatch)
    a, b = _seed(db)  # a has one action item, b has none
    item = "Email the client the quote"
    items = client.get("/api/action-items").json()
    assert any(i["recording_id"] == a and i["item"] == item and i["done"] is False for i in items)
    assert all(i["recording_id"] != b for i in items)  # b has no action items

    r = client.post("/api/action-items/toggle", json={"recording_id": a, "item": item, "done": True})
    assert r.status_code == 200 and r.json()["done"] is True
    by_key = {(i["recording_id"], i["item"]): i for i in client.get("/api/action-items").json()}
    assert by_key[(a, item)]["done"] is True

    open_items = client.get("/api/action-items", params={"open_only": True}).json()
    assert all(not (i["recording_id"] == a and i["item"] == item) for i in open_items)
    db.close()
