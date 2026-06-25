"""Lightweight, local meeting analytics computed from transcript segments.

Pure functions over the stored transcript (no network, no model). Used to show
talk-time (e.g. You vs Others) on the meeting detail page.
"""
from __future__ import annotations


def talk_time(segments: list[dict] | None) -> dict:
    """Sum speaking time per speaker from transcript segments.

    Each segment is ``{"speaker", "start", "end", "text"}`` (seconds). Returns
    ``{"total_s": float, "speakers": [{"speaker", "seconds", "pct"}, ...]}``
    sorted by most talk-time first. Empty/missing input yields zero totals.
    """
    by: dict[str, float] = {}
    for s in segments or []:
        if not isinstance(s, dict):
            continue
        speaker = (s.get("speaker") or "Unknown").strip() or "Unknown"
        try:
            dur = float(s.get("end", 0)) - float(s.get("start", 0))
        except (TypeError, ValueError):
            dur = 0.0
        if dur > 0:
            by[speaker] = by.get(speaker, 0.0) + dur
    total = sum(by.values())
    speakers = [
        {
            "speaker": name,
            "seconds": round(secs, 1),
            "pct": round(secs / total * 100) if total else 0,
        }
        for name, secs in sorted(by.items(), key=lambda kv: -kv[1])
    ]
    return {"total_s": round(total, 1), "speakers": speakers}
