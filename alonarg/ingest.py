"""Ingest a single uploaded audio file into the Alonarg pipeline.

A phone records ONE track of an in-person meeting (there is no separate system
track). This module converts an arbitrary uploaded audio file to the 16 kHz mono
PCM WAV that whisper expects, then drives the same transcribe -> summarize ->
dashboard flow as :mod:`alonarg.pipeline`, but for a single neutrally-labeled
"Speaker" track.

Like the recording pipeline, the transcribe/summarize functions are injectable
(defaulting to the real implementations) so this can be exercised in tests
without whisper or the Claude CLI. :func:`process_upload` never raises: any
failure is recorded as an ``error`` status and swallowed (it typically runs on a
background thread).
"""
from __future__ import annotations

import logging
import os
import subprocess

from alonarg import summarize as summarize_mod
from alonarg import transcribe as transcribe_mod
from alonarg.db import Database
from alonarg.types import TranscriptResult

log = logging.getLogger(__name__)


def convert_to_wav(src_path: str, dst_path: str) -> None:
    """Convert ``src_path`` to a 16 kHz mono PCM WAV at ``dst_path`` via ffmpeg.

    Runs ``ffmpeg -y -i <src> -ac 1 -ar 16000 <dst>``. Raises ``RuntimeError``
    (including ffmpeg's stderr) on a non-zero exit.
    """
    argv = ["ffmpeg", "-y", "-i", str(src_path), "-ac", "1", "-ar", "16000", str(dst_path)]
    no_window = 0x08000000 if os.name == "nt" else 0  # don't flash a console window
    result = subprocess.run(argv, capture_output=True, text=True, creationflags=no_window)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed to convert {src_path!r} (exit {result.returncode}): "
            f"{(result.stderr or '').strip()}"
        )


def _default_transcribe(wav_path: str) -> TranscriptResult:
    """Transcribe a single uploaded track, labeled neutrally as "Speaker"."""
    segments, language = transcribe_mod.transcribe_track(wav_path, "Speaker")
    return TranscriptResult(
        segments=segments,
        text=transcribe_mod.render_text(segments),
        language=language,
    )


def process_upload(
    db: Database,
    rec_id: int,
    wav_path: str,
    *,
    transcribe_fn=None,
    summarize_fn=None,
) -> None:
    """Transcribe, summarize, and finalize a single uploaded recording.

    Mirrors :func:`alonarg.pipeline.process_recording` but for one track:
      1. ``transcribing`` -> run ``transcribe_fn(wav_path)`` -> store transcript.
      2. ``summarizing``  -> run ``summarize_fn(transcript.text)`` -> store the
         summary; if it has a title, update the recording's title to match.
      3. ``done``.

    ``transcribe_fn(wav_path) -> TranscriptResult`` and
    ``summarize_fn(text) -> SummaryResult`` are injectable for testing.

    On *any* exception the recording is marked ``error`` (with the exception
    message) and the function returns normally -- it never raises.
    """
    if transcribe_fn is None:
        transcribe_fn = _default_transcribe
    if summarize_fn is None:
        summarize_fn = summarize_mod.summarize

    try:
        db.set_status(rec_id, "transcribing")
        transcript = transcribe_fn(wav_path)
        db.set_transcript(rec_id, transcript)

        db.set_status(rec_id, "summarizing")
        summary = summarize_fn(transcript.text)
        db.set_summary(rec_id, summary)
        if summary.title:
            db.update_recording(rec_id, title=summary.title)

        db.set_status(rec_id, "done")
    except Exception as exc:  # noqa: BLE001 - record failure, never crash caller
        log.exception("Upload processing failed for recording %s", rec_id)
        try:
            db.set_status(rec_id, "error", str(exc))
        except Exception:  # noqa: BLE001 - best effort; don't mask original
            log.exception("Could not record error status for recording %s", rec_id)
