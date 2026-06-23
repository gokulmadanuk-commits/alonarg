"""Local transcription of audio tracks with faster-whisper.

Each track is transcribed independently and labeled with a speaker
("You" for the mic track, "Others" for the system-audio track), then the
per-track segments are merged into one chronologically ordered, speaker-labeled
transcript.

The whisper model is loaded lazily and cached as a module-level singleton keyed
by (model_size, device, compute_type) so it only loads once per configuration.
"""
from __future__ import annotations

import os

from faster_whisper import WhisperModel

from alonarg import config
from alonarg.types import Segment, TranscriptResult

# Module-level cache of loaded models, keyed by (model_size, device, compute_type).
_MODEL_CACHE: dict[tuple[str, str, str], WhisperModel] = {}


def get_model(
    model_size: str = config.WHISPER_MODEL,
    device: str = config.WHISPER_DEVICE,
    compute_type: str = config.WHISPER_COMPUTE,
) -> WhisperModel:
    """Return a cached ``WhisperModel`` for the given configuration.

    The model is loaded once per (model_size, device, compute_type) combination
    and reused on subsequent calls. The model auto-downloads to the HF cache on
    first use.
    """
    key = (model_size, device, compute_type)
    model = _MODEL_CACHE.get(key)
    if model is None:
        model = WhisperModel(model_size, device=device, compute_type=compute_type)
        _MODEL_CACHE[key] = model
    return model


def transcribe_track(
    path: str,
    speaker: str,
    model: WhisperModel | None = None,
) -> tuple[list[Segment], str]:
    """Transcribe a single audio track, labeling every segment with ``speaker``.

    Returns ``(segments, detected_language)``. If ``path`` is falsy or the file
    is missing, returns ``([], "")`` without loading the model.
    """
    if not path or not os.path.isfile(path):
        return [], ""

    if model is None:
        model = get_model()

    segments_iter, info = model.transcribe(path)

    segments: list[Segment] = []
    for seg in segments_iter:
        text = (seg.text or "").strip()
        if not text:
            continue
        segments.append(
            Segment(
                speaker=speaker,
                start=float(seg.start),
                end=float(seg.end),
                text=text,
            )
        )

    return segments, info.language


def merge_segments(*segment_lists: list[Segment]) -> list[Segment]:
    """Concatenate segment lists and sort by ``start`` (stable). Pure function."""
    merged: list[Segment] = []
    for segs in segment_lists:
        merged.extend(segs)
    merged.sort(key=lambda s: s.start)
    return merged


def render_text(segments: list[Segment]) -> str:
    """Render segments as ``[mm:ss] {speaker}: {text}`` lines. Pure function."""
    lines: list[str] = []
    for seg in segments:
        total = int(seg.start)
        minutes, seconds = divmod(total, 60)
        lines.append(f"[{minutes:02d}:{seconds:02d}] {seg.speaker}: {seg.text}")
    return "\n".join(lines)


def transcribe(
    mic_path: str | None,
    system_path: str | None,
    model: WhisperModel | None = None,
) -> TranscriptResult:
    """Transcribe both tracks, merge, render, and pick the detected language."""
    mic_segs, lang1 = transcribe_track(mic_path, "You", model)
    sys_segs, lang2 = transcribe_track(system_path, "Others", model)

    merged = merge_segments(mic_segs, sys_segs)
    text = render_text(merged)
    language = lang1 or lang2 or ""

    return TranscriptResult(segments=merged, text=text, language=language)
