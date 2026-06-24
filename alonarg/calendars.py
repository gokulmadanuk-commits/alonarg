"""Calendar source facade.

Prefers Microsoft Graph (cloud; works with "new Outlook") when signed in;
otherwise falls back to classic Outlook desktop COM (only if it's running, and
never launching it). Both sources return the same normalized event dict, so the
rest of the app doesn't care where calendar data comes from.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from alonarg import msgraph, outlook

_DECLINED = 4


def active_source() -> str:
    return "graph" if msgraph.is_signed_in() else "outlook"


def _current_from(events: list[dict], now: datetime) -> dict | None:
    for ev in events:
        if ev.get("allDay") or ev.get("response") == _DECLINED:
            continue
        start = outlook._parse_dt(ev.get("start", ""))
        end = outlook._parse_dt(ev.get("end", ""))
        if start is not None and end is not None and start <= now <= end:
            return ev
    return None


def find_current_event(window_minutes: int = 2) -> dict | None:
    """The meeting happening right now (or None), from whichever source is active."""
    if msgraph.is_signed_in():
        now = datetime.now().astimezone()
        try:
            events = msgraph.read_events_window(
                now - timedelta(minutes=window_minutes), now + timedelta(minutes=window_minutes)
            )
        except Exception:  # noqa: BLE001 - transient Graph/network error -> skip
            return None
        return _current_from(events, now)
    return outlook.find_current_event(window_minutes)


def find_event_for(created_at_iso: str, window_minutes: int = 90) -> dict | None:
    """The calendar event matching a recording's start time, from the active source."""
    if msgraph.is_signed_in():
        when = outlook._parse_dt(created_at_iso)
        if when is None:
            return None
        when = when.astimezone()
        events = msgraph.read_events_window(
            when - timedelta(minutes=window_minutes), when + timedelta(minutes=window_minutes)
        )
        return outlook.pick_event(events, when)
    return outlook.find_event_for(created_at_iso, window_minutes)
