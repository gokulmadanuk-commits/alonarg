"""Tests for auto-record approvals storage + the scheduler decision."""
from __future__ import annotations

import pytest

from alonarg import autorecord


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    from alonarg import config

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    return tmp_path


def test_event_key_prefers_id():
    assert autorecord.event_key({"id": "abc", "subject": "X", "start": "S"}) == "abc"
    assert autorecord.event_key({"subject": "X", "start": "S"}) == "X|S"
    assert autorecord.event_key({}) == ""


def test_approve_list_unapprove(data_dir):
    assert autorecord.list_approved() == []
    autorecord.approve({"id": "e1", "subject": "Standup", "start": "s", "end": "e"})
    autorecord.approve({"id": "e1", "subject": "Standup", "start": "s", "end": "e"})  # idempotent
    assert autorecord.approved_keys() == {"e1"}
    assert autorecord.is_approved({"id": "e1"})
    assert not autorecord.is_approved({"id": "e2"})
    autorecord.unapprove("e1")
    assert autorecord.list_approved() == []


def test_approve_without_id_uses_subject_start(data_dir):
    autorecord.approve({"subject": "Sync", "start": "2026-06-24T10:00:00", "end": "x"})
    assert autorecord.approved_keys() == {"Sync|2026-06-24T10:00:00"}


def test_decide_start_for_approved(data_dir):
    autorecord.approve({"id": "e1", "subject": "S", "start": "x", "end": "y"})
    ev = {"id": "e1", "subject": "S", "start": "x", "end": "y"}
    assert autorecord.decide(ev, recording=False, active_key=None) == "start"


def test_decide_no_start_when_not_approved(data_dir):
    assert autorecord.decide({"id": "e9"}, recording=False, active_key=None) == "none"


def test_decide_stop_when_meeting_changes(data_dir):
    # we auto-started e1; a different meeting is now live -> stop
    assert autorecord.decide({"id": "e2"}, recording=True, active_key="e1") == "stop"
    # nothing live now, but we were recording e1 -> stop
    assert autorecord.decide(None, recording=True, active_key="e1") == "stop"
    # still in e1 -> keep going
    assert autorecord.decide({"id": "e1"}, recording=True, active_key="e1") == "none"


def test_decide_never_stops_manual_recording(data_dir):
    # active_key None means the user started it manually -> never auto-stop
    assert autorecord.decide(None, recording=True, active_key=None) == "none"
    assert autorecord.decide({"id": "e1"}, recording=True, active_key=None) == "none"
