"""Tests for the calendar source facade (Graph vs Outlook selection)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from alonarg import calendars, msgraph, outlook


def test_find_event_for_uses_graph_when_signed_in(monkeypatch):
    monkeypatch.setattr(msgraph, "is_signed_in", lambda: True)
    now = datetime.now(timezone.utc)
    events = [{
        "subject": "G",
        "start": (now - timedelta(minutes=5)).isoformat(),
        "end": (now + timedelta(minutes=25)).isoformat(),
        "attendees": [],
    }]
    monkeypatch.setattr(msgraph, "read_events_window", lambda a, b: events)
    assert calendars.find_event_for(now.isoformat())["subject"] == "G"


def test_find_event_for_falls_back_to_outlook(monkeypatch):
    monkeypatch.setattr(msgraph, "is_signed_in", lambda: False)
    monkeypatch.setattr(outlook, "find_event_for", lambda c, window_minutes=90: {"subject": "O"})
    assert calendars.find_event_for("2026-06-24T10:00:00+00:00")["subject"] == "O"


def test_find_current_event_graph_filters(monkeypatch):
    monkeypatch.setattr(msgraph, "is_signed_in", lambda: True)
    now = datetime.now().astimezone()
    events = [
        {"subject": "Declined", "start": (now - timedelta(minutes=5)).isoformat(),
         "end": (now + timedelta(minutes=25)).isoformat(), "allDay": False, "response": 4},
        {"subject": "Live", "start": (now - timedelta(minutes=5)).isoformat(),
         "end": (now + timedelta(minutes=25)).isoformat(), "allDay": False, "response": 0},
    ]
    monkeypatch.setattr(msgraph, "read_events_window", lambda a, b: events)
    assert calendars.find_current_event()["subject"] == "Live"


def test_find_current_event_falls_back(monkeypatch):
    monkeypatch.setattr(msgraph, "is_signed_in", lambda: False)
    monkeypatch.setattr(outlook, "find_current_event", lambda window_minutes=2: {"subject": "OutlookLive"})
    assert calendars.find_current_event()["subject"] == "OutlookLive"
