"""Tests for alonarg.transcribe.

Unit tests cover the pure logic (segment merging, text rendering, and the
empty-input path of ``transcribe``) and never load the whisper model. The
integration test synthesizes a known phrase via Windows SAPI and runs real
local transcription end-to-end.
"""
from __future__ import annotations

import pytest

from alonarg.transcribe import merge_segments, render_text, transcribe
from alonarg.types import Segment, TranscriptResult


# --------------------------------------------------------------------------- #
# Unit tests (no model load)
# --------------------------------------------------------------------------- #
def test_merge_segments_sorts_globally_and_preserves_all():
    you = [
        Segment("You", 0.0, 1.0, "first"),
        Segment("You", 4.0, 5.0, "third"),
    ]
    others = [
        Segment("Others", 2.0, 3.0, "second"),
        Segment("Others", 6.0, 7.0, "fourth"),
    ]

    merged = merge_segments(you, others)

    # All segments preserved.
    assert len(merged) == len(you) + len(others)
    # Globally sorted by start time.
    starts = [s.start for s in merged]
    assert starts == sorted(starts)
    assert starts == [0.0, 2.0, 4.0, 6.0]
    # Speakers and text travel with their segments.
    assert [s.speaker for s in merged] == ["You", "Others", "You", "Others"]
    assert [s.text for s in merged] == ["first", "second", "third", "fourth"]


def test_merge_segments_is_stable_for_equal_starts():
    a = [Segment("You", 1.0, 2.0, "a-you")]
    b = [Segment("Others", 1.0, 2.0, "b-others")]
    merged = merge_segments(a, b)
    # Equal starts keep input order (a before b).
    assert [s.text for s in merged] == ["a-you", "b-others"]


def test_render_text_formats_lines_and_timestamps():
    segments = [
        Segment("You", 0.0, 1.0, "hello there"),
        Segment("Others", 65.0, 70.0, "general kenobi"),
    ]
    rendered = render_text(segments)
    assert rendered == (
        "[00:00] You: hello there\n"
        "[01:05] Others: general kenobi"
    )


def test_render_text_timestamp_minute_rollover():
    # 65.0s -> 01:05 ; sub-second fractions truncate to the floor second.
    assert render_text([Segment("You", 65.0, 66.0, "x")]) == "[01:05] You: x"
    assert render_text([Segment("You", 5.9, 6.0, "y")]) == "[00:05] You: y"
    assert render_text([Segment("You", 600.0, 601.0, "z")]) == "[10:00] You: z"


def test_render_text_empty():
    assert render_text([]) == ""


def test_transcribe_with_no_paths_returns_empty_result():
    result = transcribe(mic_path=None, system_path=None)
    assert isinstance(result, TranscriptResult)
    assert result.segments == []
    assert result.text == ""
    assert result.language == ""


# --------------------------------------------------------------------------- #
# Integration test (real transcription; downloads model on first run)
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_transcribe_real_speech(speech_wav):
    phrase = "The quick brown fox jumps over the lazy dog."
    clip = speech_wav(phrase)

    result = transcribe(mic_path=clip, system_path=None)

    text = result.text.lower()
    assert any(
        w in text for w in ["fox", "quick", "brown", "lazy", "dog"]
    ), result.text
    assert result.segments
    assert result.segments[0].speaker == "You"
