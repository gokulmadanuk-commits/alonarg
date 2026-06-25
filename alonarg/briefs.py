"""Cache of generated pre-meeting briefs, keyed by calendar event key.

Briefs are pre-generated for meetings the user flagged for auto-recording so
they're ready to read (not produced in real time). Stored as a small JSON map in
the data dir.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from alonarg import config

_lock = threading.Lock()


def _path() -> Path:
    return Path(config.DATA_DIR) / "briefs.json"


def all() -> dict:
    p = _path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        return {}


def get(key: str) -> dict | None:
    return all().get(key)


def set(key: str, data: dict) -> None:
    if not key:
        return
    with _lock:
        store = all()
        store[key] = data
        _save(store)


def prune(valid_keys) -> None:
    """Drop cached briefs whose event key is no longer in ``valid_keys``."""
    valid = {k for k in (valid_keys or [])}
    with _lock:
        store = all()
        kept = {k: v for k, v in store.items() if k in valid}
        if len(kept) != len(store):
            _save(kept)


def _save(store: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(store), encoding="utf-8")
