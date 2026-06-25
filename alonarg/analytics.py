"""Lightweight, local meeting analytics computed from transcript segments.

Pure functions over the stored transcript (no network, no model). Used to show
talk-time (e.g. You vs Others) and self-coaching metrics on the detail page.
"""
from __future__ import annotations

import re

_FILLER = re.compile(
    r"\b(um+|uh+|erm+|hmm+|like|you know|sort of|kind of|basically|actually|literally|i mean)\b",
    re.IGNORECASE,
)


def _dur(s: dict) -> float:
    try:
        return max(0.0, float(s.get("end", 0)) - float(s.get("start", 0)))
    except (TypeError, ValueError):
        return 0.0


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


def coaching_metrics(segments: list[dict] | None) -> dict:
    """Self-coaching stats from transcript segments (pure, no model).

    Returns ``{}`` when there are no segments. Otherwise:
    ``questions_total``, ``questions_you``, ``longest_monologue_s`` +
    ``longest_monologue_speaker``, ``filler_count``, ``filler_per_min`` (None if
    the meeting is too short to be meaningful), and ``your_wpm`` (your speaking
    pace, None if you barely spoke).
    """
    segs = [s for s in (segments or []) if isinstance(s, dict)]
    if not segs:
        return {}

    total_q = your_q = filler = your_words = 0
    your_secs = total_secs = 0.0
    for s in segs:
        text = str(s.get("text") or "")
        q = text.count("?")
        total_q += q
        filler += len(_FILLER.findall(text))
        d = _dur(s)
        total_secs += d
        if (s.get("speaker") or "") == "You":
            your_q += q
            your_words += len(text.split())
            your_secs += d

    # Longest monologue: the longest unbroken run by a single speaker.
    best = run = 0.0
    best_spk = run_spk = ""
    for s in segs:
        spk = s.get("speaker") or "Unknown"
        if spk == run_spk:
            run += _dur(s)
        else:
            run_spk, run = spk, _dur(s)
        if run > best:
            best, best_spk = run, run_spk

    return {
        "questions_total": total_q,
        "questions_you": your_q,
        "longest_monologue_s": round(best, 1),
        "longest_monologue_speaker": best_spk,
        "filler_count": filler,
        "filler_per_min": round(filler / (total_secs / 60), 1) if total_secs >= 30 else None,
        "your_wpm": round(your_words / (your_secs / 60)) if your_secs >= 5 else None,
    }
