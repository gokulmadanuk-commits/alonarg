"""Post-recording processing pipeline for Alonarg.

After the recorder stops, a recording must be transcribed, then summarized, then
marked done. :func:`process_recording` drives those steps against the database,
updating the recording's status as it goes so the dashboard can reflect progress.

The transcribe/summarize functions are injectable (defaulting to the real
implementations) so the pipeline can be exercised in tests without whisper or the
Claude CLI. Any failure is recorded as an ``error`` status and swallowed: the
pipeline never raises to its caller (it typically runs on a background thread).
"""
from __future__ import annotations

import logging

from alonarg import summarize as summarize_mod
from alonarg import transcribe as transcribe_mod
from alonarg.db import Database
from alonarg.types import RecordingResult

log = logging.getLogger(__name__)


def process_recording(
    db: Database,
    rec_id: int,
    recording: RecordingResult,
    *,
    transcribe_fn=transcribe_mod.transcribe,
    summarize_fn=summarize_mod.summarize,
    enrich_fn=None,
    index_fn=None,
) -> None:
    """Transcribe, summarize, and finalize a recording.

    Steps (each reflected in the recording's ``status``):
      1. ``transcribing`` -> run ``transcribe_fn`` -> store the transcript.
      2. ``summarizing``  -> run ``summarize_fn`` -> store the summary; if the
         summary has a title, update the recording's title to match.
      3. optional ``enrich_fn(rec_id)`` -> match the calendar event and fill in
         people/contacts/title. Its failures are swallowed (never block a
         recording from completing).
      4. ``done``.

    On *any* exception in steps 1-2 the recording is marked ``error`` (with the
    exception message) and the function returns normally -- it never raises.
    """
    try:
        db.set_status(rec_id, "transcribing")
        transcript = transcribe_fn(recording.mic_path, recording.system_path)
        db.set_transcript(rec_id, transcript)

        db.set_status(rec_id, "summarizing")
        summary = summarize_fn(transcript.text)
        db.set_summary(rec_id, summary)
        if summary.title:
            db.update_recording(rec_id, title=summary.title)

        if enrich_fn is not None:
            try:
                enrich_fn(rec_id)
            except Exception:  # noqa: BLE001 - enrichment is best-effort
                log.warning("Auto-enrich failed for recording %s", rec_id, exc_info=True)

        if index_fn is not None:
            try:
                index_fn(rec_id)
            except Exception:  # noqa: BLE001 - semantic indexing is best-effort
                log.warning("Semantic indexing failed for recording %s", rec_id, exc_info=True)

        db.set_status(rec_id, "done")
    except Exception as exc:  # noqa: BLE001 - record failure, never crash caller
        log.exception("Pipeline failed for recording %s", rec_id)
        try:
            db.set_status(rec_id, "error", str(exc))
        except Exception:  # noqa: BLE001 - best effort; don't mask original
            log.exception("Could not record error status for recording %s", rec_id)
