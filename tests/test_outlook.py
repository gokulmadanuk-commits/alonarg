"""Tests for the Outlook calendar reader (PowerShell COM mocked)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from alonarg import outlook


def test_pick_event_contains():
    events = [
        {"subject": "A", "start": "2026-06-24T09:00:00+00:00", "end": "2026-06-24T09:30:00+00:00"},
        {"subject": "B", "start": "2026-06-24T10:00:00+00:00", "end": "2026-06-24T11:00:00+00:00"},
    ]
    when = datetime(2026, 6, 24, 10, 15, tzinfo=timezone.utc)
    assert outlook.pick_event(events, when)["subject"] == "B"


def test_pick_event_nearest_when_no_overlap():
    events = [
        {"subject": "A", "start": "2026-06-24T09:00:00+00:00", "end": "2026-06-24T09:30:00+00:00"},
        {"subject": "B", "start": "2026-06-24T12:00:00+00:00", "end": "2026-06-24T13:00:00+00:00"},
    ]
    when = datetime(2026, 6, 24, 9, 40, tzinfo=timezone.utc)
    assert outlook.pick_event(events, when)["subject"] == "A"


def test_pick_event_empty():
    assert outlook.pick_event([], datetime.now(timezone.utc)) is None


def test_read_events_window_parses_array(monkeypatch):
    monkeypatch.setattr(
        outlook, "_run_ps",
        lambda s, timeout=60: '[{"subject":"X","start":"2026-06-24T10:00:00+00:00",'
        '"end":"2026-06-24T10:30:00+00:00","attendees":[{"name":"Jai","email":"jai@x.com"}]}]',
    )
    evs = outlook.read_events_window("a", "b")
    assert len(evs) == 1 and evs[0]["subject"] == "X"
    assert evs[0]["attendees"][0]["email"] == "jai@x.com"


def test_read_events_window_single_object(monkeypatch):
    monkeypatch.setattr(
        outlook, "_run_ps",
        lambda s, timeout=60: '{"subject":"Solo","start":"2026-06-24T10:00:00+00:00",'
        '"end":"2026-06-24T10:30:00+00:00","attendees":[]}',
    )
    evs = outlook.read_events_window("a", "b")
    assert len(evs) == 1 and evs[0]["subject"] == "Solo"


def test_read_events_window_outlook_unavailable(monkeypatch):
    monkeypatch.setattr(outlook, "_run_ps", lambda s, timeout=60: '{"error":"outlook_unavailable"}')
    with pytest.raises(RuntimeError):
        outlook.read_events_window("a", "b")


def test_read_events_window_empty(monkeypatch):
    monkeypatch.setattr(outlook, "_run_ps", lambda s, timeout=60: "")
    assert outlook.read_events_window("a", "b") == []
