"""Per-event approvals for automatic recording.

The user pre-approves specific calendar events (from the dashboard's Calendar
view); the engine's scheduler then auto-starts a recording when an approved
meeting is in progress and auto-stops it when the meeting ends.

Approvals are stored as a small JSON list in the data dir (like push
subscriptions). Each entry keeps enough of the event to match it later and to
show it back in the UI.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from alonarg import config

_lock = threading.Lock()


def _path() -> Path:
    return Path(config.DATA_DIR) / "autorecord.json"


def event_key(event: dict) -> str:
    """A stable identifier for a calendar event.

    Prefers the source's own id (Graph event id / Outlook GlobalAppointmentID);
    falls back to ``subject|start`` so events without an id still work.
    """
    if not event:
        return ""
    eid = str(event.get("id") or "").strip()
    if eid:
        return eid
    return f"{event.get('subject', '')}|{event.get('start', '')}"


def list_approved() -> list[dict]:
    p = _path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (ValueError, OSError):
        return []


def approved_keys() -> set[str]:
    return {str(e.get("key", "")) for e in list_approved() if e.get("key")}


def is_approved(event: dict) -> bool:
    return event_key(event) in approved_keys()


def _save(items: list[dict]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(items), encoding="utf-8")


def approve(event: dict) -> dict:
    """Mark an event for auto-recording (idempotent). Returns the stored entry."""
    key = event_key(event)
    if not key:
        return {}
    entry = {
        "key": key,
        "subject": event.get("subject", ""),
        "start": event.get("start", ""),
        "end": event.get("end", ""),
    }
    with _lock:
        items = [e for e in list_approved() if e.get("key") != key]
        items.append(entry)
        _save(items)
    return entry


def unapprove(key: str) -> None:
    with _lock:
        _save([e for e in list_approved() if e.get("key") != key])


def decide(
    event: dict | None,
    recording: bool,
    active_key: str | None,
    recorded_keys: set[str] | None = None,
) -> str:
    """Pure decision for the scheduler: ``"start"``, ``"stop"`` or ``"none"``.

    - ``start`` when not recording and an approved event is in progress that we
      have not already recorded this session. ``recorded_keys`` holds the keys
      of events we've already auto-started; once an event is in there we never
      auto-restart it, so stopping a recording early (while the calendar invite
      is still "in progress") doesn't make us re-record it every poll.
    - ``stop`` when we previously auto-started (``active_key`` set) and the event
      we started for is no longer the current one (it ended, or a different
      meeting is now live). Manual recordings (``active_key`` is None) are never
      auto-stopped.
    """
    recorded_keys = recorded_keys or set()
    cur_key = event_key(event) if event else None
    if (
        not recording
        and event is not None
        and is_approved(event)
        and cur_key not in recorded_keys
    ):
        return "start"
    if recording and active_key is not None and cur_key != active_key:
        return "stop"
    return "none"
