"""Tests for the People aggregation (relationship memory)."""
from __future__ import annotations

from alonarg import people
from alonarg.db import Database
from alonarg.types import SummaryResult


def _rec(db, title, *, contacts=None, names=None, actions=None, done=None, created=None):
    rid = db.create_recording(status="done", title=title, created_at=created)
    if actions is not None:
        db.set_summary(rid, SummaryResult(title=title, summary="s", action_items=actions))
    meta = {}
    if contacts is not None:
        meta["contacts"] = contacts
    if names is not None:
        meta["people"] = names
    if meta:
        db.set_meta(rid, meta)
    if done is not None:
        db.set_state(rid, {"done_actions": done})
    return rid


def test_aggregate_merges_by_email(tmp_path):
    db = Database(tmp_path / "p.db")
    _rec(db, "M1", contacts=[{"name": "Jai Paragreen", "email": "jai@x.com", "phone": ""}],
         actions=["Send deck"], created="2026-06-01T10:00:00+00:00")
    _rec(db, "M2", contacts=[{"name": "Jai", "email": "JAI@x.com", "phone": "123"}],
         created="2026-06-10T10:00:00+00:00")  # same email, different case/name
    jai = [p for p in people.aggregate(db) if p["email"] == "jai@x.com"]
    assert len(jai) == 1
    assert jai[0]["meeting_count"] == 2
    assert jai[0]["name"] == "Jai Paragreen"   # longer name kept
    assert jai[0]["open_actions"] == 1
    assert jai[0]["last_met"].startswith("2026-06-10")
    db.close()


def test_aggregate_excludes_self_and_generic(tmp_path):
    db = Database(tmp_path / "p.db")
    _rec(db, "M", contacts=[{"name": "Me", "email": "me@x.com", "phone": ""},
                            {"name": "Bob", "email": "bob@x.com", "phone": ""}],
         names=["You", "Others", "Carol"])
    ppl = people.aggregate(db, self_emails=["me@x.com"])
    names = {p["name"] for p in ppl}
    assert "Bob" in names and "Carol" in names
    assert not any(p["email"] == "me@x.com" for p in ppl)
    assert "You" not in names and "Others" not in names
    db.close()


def test_aggregate_action_first_sort(tmp_path):
    db = Database(tmp_path / "p.db")
    # Anna: no open items, met most recently. Bea: an open item, met earlier.
    _rec(db, "Anna mtg", contacts=[{"name": "Anna", "email": "anna@x.com", "phone": ""}],
         created="2026-06-20T10:00:00+00:00")
    _rec(db, "Bea mtg", contacts=[{"name": "Bea", "email": "bea@x.com", "phone": ""}],
         actions=["Follow up"], created="2026-06-10T10:00:00+00:00")
    order = [p["name"] for p in people.aggregate(db)]
    assert order[0] == "Bea"  # open items float to the top regardless of recency
    db.close()


def test_person_detail_open_actions(tmp_path):
    db = Database(tmp_path / "p.db")
    _rec(db, "M1", contacts=[{"name": "Jai", "email": "jai@x.com", "phone": "555"}],
         actions=["A", "B"], done=["A"])
    p = people.person_detail(db, "jai@x.com")
    assert p["meeting_count"] == 1 and p["phone"] == "555"
    assert [a["item"] for a in p["open_actions"]] == ["B"]  # A is done
    db.close()


def test_person_detail_missing(tmp_path):
    db = Database(tmp_path / "p.db")
    assert people.person_detail(db, "nobody@x.com") is None
    db.close()


def test_name_only_person(tmp_path):
    db = Database(tmp_path / "p.db")
    _rec(db, "M", names=["Carol (CFO)"])
    ppl = people.aggregate(db)
    carol = [p for p in ppl if p["name"] == "Carol"]
    assert len(carol) == 1 and carol[0]["id"] == "name:carol" and carol[0]["email"] == ""
    db.close()
