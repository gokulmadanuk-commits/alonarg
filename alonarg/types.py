"""Shared data types for Alonarg.

These dataclasses are the contracts between modules. They are JSON-serializable
via ``to_dict()`` and reconstructable via ``from_dict()`` so they can be stored
in SQLite and rendered by the dashboard. Do not change these signatures without
updating every module + its tests.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class RecordingResult:
    """Result of an audio capture session."""

    mixed_path: str                 # always present: mic+system mixed mono 16k WAV (for playback)
    duration_s: float
    sample_rate: int
    mic_path: str | None = None     # raw mic track (may be None if no mic)
    system_path: str | None = None  # raw system/loopback track (may be None if no loopback)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Segment:
    """A single transcribed utterance attributed to a speaker."""

    speaker: str   # "You" (mic) or "Others" (system audio)
    start: float   # seconds from start of recording
    end: float     # seconds
    text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Segment":
        return cls(
            speaker=d["speaker"],
            start=float(d["start"]),
            end=float(d["end"]),
            text=d["text"],
        )


@dataclass
class TranscriptResult:
    """Full transcript: ordered, speaker-labeled segments + a rendered text view."""

    segments: list[Segment] = field(default_factory=list)
    text: str = ""          # human-readable rendered transcript
    language: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "segments": [s.to_dict() for s in self.segments],
            "text": self.text,
            "language": self.language,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TranscriptResult":
        return cls(
            segments=[Segment.from_dict(s) for s in d.get("segments", [])],
            text=d.get("text", ""),
            language=d.get("language", ""),
        )


@dataclass
class SummaryResult:
    """Meeting summary produced by Claude."""

    title: str = ""
    summary: str = ""
    action_items: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SummaryResult":
        return cls(
            title=d.get("title", ""),
            summary=d.get("summary", ""),
            action_items=list(d.get("action_items", []) or []),
            next_steps=list(d.get("next_steps", []) or []),
        )
