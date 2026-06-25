"""Tests for the Microsoft Graph calendar module (httpx + token mocked)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from alonarg import msgraph


def test_to_event_normalizes():
    item = {
        "subject": "Q3 Planning",
        "start": {"dateTime": "2026-06-24T20:00:00.0000000", "timeZone": "UTC"},
        "end": {"dateTime": "2026-06-24T20:30:00.0000000", "timeZone": "UTC"},
        "isAllDay": False,
        "responseStatus": {"response": "accepted"},
        "attendees": [
            {"emailAddress": {"name": "Jai", "address": "jai@acme.com"}},
            {"emailAddress": {"name": "Graham", "address": "graham@acme.com"}},
        ],
    }
    ev = msgraph._to_event(item)
    assert ev["subject"] == "Q3 Planning"
    assert ev["start"] == "2026-06-24T20:00:00+00:00"
    assert ev["end"] == "2026-06-24T20:30:00+00:00"
    assert ev["allDay"] is False and ev["response"] == 0
    assert {"name": "Jai", "email": "jai@acme.com"} in ev["attendees"]


def test_to_event_declined_and_z_suffix():
    item = {
        "subject": "x",
        "start": {"dateTime": "2026-06-24T20:00:00Z"},
        "end": {"dateTime": "2026-06-24T20:30:00Z"},
        "isAllDay": True,
        "responseStatus": {"response": "declined"},
    }
    ev = msgraph._to_event(item)
    assert ev["allDay"] is True and ev["response"] == 4
    assert ev["start"] == "2026-06-24T20:00:00+00:00"


def test_read_events_window_not_signed_in(monkeypatch):
    monkeypatch.setattr(msgraph, "get_token", lambda scopes=None: None)
    assert msgraph.read_events_window(datetime.now(timezone.utc), datetime.now(timezone.utc)) == []


def test_read_events_window_parses(monkeypatch):
    monkeypatch.setattr(msgraph, "get_token", lambda scopes=None: "tok")

    class _R:
        status_code = 200

        def json(self):
            return {"value": [{
                "subject": "M",
                "start": {"dateTime": "2026-06-24T10:00:00Z"},
                "end": {"dateTime": "2026-06-24T11:00:00Z"},
                "attendees": [],
            }]}

    monkeypatch.setattr(msgraph.httpx, "get", lambda *a, **k: _R())
    evs = msgraph.read_events_window(datetime.now(timezone.utc), datetime.now(timezone.utc))
    assert len(evs) == 1 and evs[0]["subject"] == "M"


def test_read_events_window_error(monkeypatch):
    monkeypatch.setattr(msgraph, "get_token", lambda scopes=None: "tok")

    class _R:
        status_code = 403
        text = "Forbidden"

        def json(self):
            return {}

    monkeypatch.setattr(msgraph.httpx, "get", lambda *a, **k: _R())
    with pytest.raises(RuntimeError):
        msgraph.read_events_window(datetime.now(timezone.utc), datetime.now(timezone.utc))
