"""Keyword trackers: terms the user watches across all meetings.

A tiny JSON list in the data dir (like push subscriptions). Counts are computed
on demand against the recordings store, so nothing extra needs indexing.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from alonarg import config

_lock = threading.Lock()


def _path() -> Path:
    return Path(config.DATA_DIR) / "trackers.json"


def list_trackers() -> list[str]:
    p = _path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return [str(t) for t in data] if isinstance(data, list) else []
    except (ValueError, OSError):
        return []


def _save(terms: list[str]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(terms), encoding="utf-8")


def add_tracker(term: str) -> list[str]:
    term = (term or "").strip()
    if not term:
        return list_trackers()
    with _lock:
        terms = list_trackers()
        if not any(t.lower() == term.lower() for t in terms):
            terms.append(term)
            _save(terms)
        return terms


def remove_tracker(term: str) -> list[str]:
    with _lock:
        terms = [t for t in list_trackers() if t.lower() != (term or "").lower()]
        _save(terms)
        return terms
