"""FastAPI app + server-rendered dashboard for Alonarg.

The app wires the recorder, database, and processing pipeline together behind a
small JSON API plus two server-rendered pages (dashboard + detail). Everything
is injectable via :func:`create_app` so tests can supply a fake recorder, a temp
database, and a synchronous pipeline stub.

Templates live in ``alonarg/templates`` and static assets in ``alonarg/static``;
both are resolved by absolute path from ``__file__`` so the app works regardless
of the current working directory. No external CDNs are used -- it is fully
offline-friendly.
"""
from __future__ import annotations

import logging
import mimetypes
import shutil
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Body, Depends, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import FileResponse

from alonarg import (
    analytics,
    assistant,
    audio_capture,
    autorecord,
    briefs,
    calendars,
    config,
    ingest,
    msgraph,
    pipeline,
    push,
    summarize,
    trackers,
)
from alonarg.db import Database
from alonarg.types import SummaryResult

log = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATES_DIR = _BASE_DIR / "templates"
_STATIC_DIR = _BASE_DIR / "static"


def _rec_dir(rec_id: int) -> Path:
    """Filesystem directory holding a recording's audio files."""
    return Path(config.RECORDINGS_DIR) / str(rec_id)


def _upload_ext(filename: str | None, content_type: str | None) -> str:
    """Pick a sensible file extension for an uploaded audio blob.

    Prefers the original filename's extension, falls back to one guessed from the
    content type, and defaults to ``.webm`` (the common PWA recording format).
    """
    if filename:
        suffix = Path(filename).suffix
        if suffix:
            return suffix
    if content_type:
        guessed = mimetypes.guess_extension(content_type.split(";")[0].strip())
        if guessed:
            return guessed
    return ".webm"


def _context_for_recording(rec: dict) -> str:
    """Build a compact text block (title + summary + transcript) for the LLM."""
    parts = [f"Title: {rec.get('title') or 'Untitled'}"]
    summ = rec.get("summary") or {}
    if summ.get("summary"):
        parts.append("Summary: " + summ["summary"])
    if summ.get("action_items"):
        parts.append("Action items: " + "; ".join(summ["action_items"]))
    tr = rec.get("transcript") or {}
    if tr.get("text"):
        parts.append("Transcript:\n" + tr["text"])
    return "\n".join(parts)


def _meeting_metadata(row: dict) -> dict:
    """Lightweight, countable metadata for one recording (from a list row)."""
    summ = row.get("summary") or {}
    return {
        "id": row["id"],
        "title": row.get("title") or "Untitled",
        "created_at": row.get("created_at") or "",
        "duration_s": int(row.get("duration_s") or 0),
        "status": row.get("status") or "",
        "n_action_items": len(summ.get("action_items") or []),
        "n_next_steps": len(summ.get("next_steps") or []),
    }


def _stats_block(rows: list[dict]) -> str:
    """Computed overview + per-meeting metadata table for the LLM.

    Counting/"how many" questions are answered from these exact numbers rather
    than by making the model count across transcripts (which it does poorly).
    """
    metas = [_meeting_metadata(r) for r in rows]
    total = len(metas)
    with_ai = sum(1 for m in metas if m["n_action_items"] > 0)
    with_ns = sum(1 for m in metas if m["n_next_steps"] > 0)
    by_status: dict[str, int] = {}
    for m in metas:
        by_status[m["status"]] = by_status.get(m["status"], 0) + 1
    status_line = ", ".join(f"{k}: {v}" for k, v in sorted(by_status.items())) or "n/a"
    lines = [
        "=== OVERVIEW (exact counts computed from ALL meetings) ===",
        f"Total meetings: {total}",
        f"By status -> {status_line}",
        f"Meetings WITH action items: {with_ai}",
        f"Meetings WITH NO action items: {total - with_ai}",
        f"Meetings WITH next steps: {with_ns}",
        f"Meetings WITH NO next steps: {total - with_ns}",
        f"Total action items (all meetings): {sum(m['n_action_items'] for m in metas)}",
        f"Total next steps (all meetings): {sum(m['n_next_steps'] for m in metas)}",
        f"Total recorded seconds (all meetings): {sum(m['duration_s'] for m in metas)}",
        "",
        "=== PER-MEETING METADATA (all meetings) ===",
    ]
    for m in metas:
        lines.append(
            f'- #{m["id"]} "{m["title"]}" | date: {m["created_at"]} | '
            f'duration_s: {m["duration_s"]} | status: {m["status"]} | '
            f'action_items: {m["n_action_items"]} | next_steps: {m["n_next_steps"]}'
        )
    return "\n".join(lines)


def _global_context(db, budget: int = 12000) -> tuple[str, list[int]]:
    """Build context for an across-all-meetings question.

    Always includes a computed OVERVIEW + per-meeting metadata (so counting
    questions are exact and cover every meeting), then as much detailed content
    (summaries + transcripts, newest-first) as fits in ``budget``.
    """
    rows = db.list_recordings()
    stats = _stats_block(rows)
    chunks: list[str] = [stats, "\n=== MEETING DETAILS (summaries + transcripts) ==="]
    used: list[int] = []
    total = len(stats)
    for row in rows:
        rec = db.get_recording(row["id"])
        if rec is None:
            continue
        block = (
            f"### Meeting {rec['id']} ({rec.get('created_at', '')}) - "
            f"{rec.get('title', '')}\n" + _context_for_recording(rec) + "\n"
        )
        if used and total + len(block) > budget:
            break
        chunks.append(block)
        used.append(rec["id"])
        total += len(block)
    return "\n".join(chunks), used


def _clean_list(items) -> list[str]:
    """Strip and drop blank entries from a list of strings."""
    return [s.strip() for s in (items or []) if isinstance(s, str) and s.strip()]


def _clean_contacts(items) -> list[dict]:
    """Normalize contacts to {name, email, phone}, dropping fully-empty rows."""
    out: list[dict] = []
    for c in items or []:
        if not isinstance(c, dict):
            continue
        entry = {
            "name": str(c.get("name", "")).strip(),
            "email": str(c.get("email", "")).strip(),
            "phone": str(c.get("phone", "")).strip(),
        }
        if entry["name"] or entry["email"] or entry["phone"]:
            out.append(entry)
    return out


def _merge_meta(current: dict, detected: dict) -> dict:
    """Union detected people/contacts into existing meta without losing edits."""
    people = list(current.get("people", []) or [])
    seen_p = {p.lower() for p in people}
    for p in detected.get("people", []) or []:
        if p.lower() not in seen_p:
            people.append(p)
            seen_p.add(p.lower())
    contacts = _clean_contacts(current.get("contacts", []))
    seen_c = {(c["name"].lower(), c["email"].lower()) for c in contacts}
    for c in _clean_contacts(detected.get("contacts", [])):
        key = (c["name"].lower(), c["email"].lower())
        if key not in seen_c:
            contacts.append(c)
            seen_c.add(key)
    return {"people": people, "contacts": contacts}


def _merge_state(current: dict | None, updates: dict) -> dict:
    """Shallow-merge UI state (pinned / tags / template / done_actions)."""
    state = dict(current or {})
    state.update(updates)
    return state


def _enrich_recording(db, rec_id: int) -> None:
    """Auto-match a finished recording to its calendar event.

    Fills the Details (people/contacts) and titles the meeting with the calendar
    subject when one matches. Best-effort: any failure is swallowed by the caller.
    """
    rec = db.get_recording(rec_id)
    if rec is None:
        return
    try:
        event = calendars.find_event_for(rec.get("created_at") or "")
    except Exception:  # noqa: BLE001 - calendar may be offline/unavailable
        return
    if not event:
        return
    people: list[str] = []
    contacts: list[dict] = []
    for a in event.get("attendees", []) or []:
        name = str(a.get("name", "")).strip()
        email = str(a.get("email", "")).strip()
        if name and name.lower() not in {p.lower() for p in people}:
            people.append(name)
        if name or email:
            contacts.append({"name": name, "email": email, "phone": ""})
    merged = _merge_meta(rec.get("meta") or {}, {"people": people, "contacts": contacts})
    db.set_meta(rec_id, merged)
    subject = str(event.get("subject", "")).strip()
    if subject:
        db.update_recording(rec_id, title=subject)


def build_brief(db, event: dict) -> dict:
    """Compose a pre-meeting brief from the calendar invite, past meetings, and
    recent emails with the attendees. Works even with no prior recordings.

    Returns ``{"brief", "based_on", "emails_used", "mail_available"}``. Raises
    ``RuntimeError`` if the local model is unreachable.
    """
    subject = str(event.get("subject", "") or "").strip()
    organizer = str(event.get("organizer", "") or "").strip()
    body = str(event.get("body", "") or "").strip()
    names: list[str] = []
    emails: list[str] = []
    for a in event.get("attendees") or []:
        if isinstance(a, dict):
            nm = str(a.get("name", "") or "").strip()
            em = str(a.get("email", "") or "").strip()
        else:
            nm, em = str(a).strip(), ""
        if nm or em:
            names.append(nm or em)
        if em:
            emails.append(em)

    # Past recordings matching the subject or any attendee (optional).
    seen: dict[int, dict] = {}
    if subject:
        for r in db.search_recordings(subject):
            seen[r["id"]] = r
    for nm in names:
        if len(nm) >= 3:
            for r in db.search_recordings(nm):
                seen[r["id"]] = r
    rows = sorted(seen.values(), key=lambda r: r.get("created_at", ""), reverse=True)[:5]
    meeting_parts = []
    for r in rows:
        rec = db.get_recording(r["id"]) or r
        summ = rec.get("summary") or {}
        meeting_parts.append(
            f"Meeting: {rec.get('title','')} ({rec.get('created_at','')})\n"
            f"Summary: {summ.get('summary','')}\n"
            f"Action items: {'; '.join(summ.get('action_items') or []) or 'none'}"
        )

    # Recent emails with the attendees (read-only; only if Mail.Read granted).
    # Only touch Graph when the invite actually has attendee email addresses.
    mail_ok = False
    email_parts = []
    if emails:
        mail_ok = msgraph.mail_available()
        if mail_ok:
            seen_msgs: set = set()
            msgs: list[dict] = []
            for em in emails:
                for m in msgraph.read_messages(em, top=5):
                    k = (m["subject"], m["received"])
                    if k not in seen_msgs:
                        seen_msgs.add(k)
                        msgs.append(m)
            msgs.sort(key=lambda x: x["received"], reverse=True)
            for m in msgs[:6]:
                email_parts.append(
                    f"Email ({(m['received'] or '')[:10]}) from {m['from']}: {m['subject']}\n{m['preview']}"
                )

    sections: list[str] = []
    invite_lines = []
    if organizer:
        invite_lines.append(f"Organizer: {organizer}")
    if names:
        invite_lines.append("Attendees: " + ", ".join(names))
    if body:
        invite_lines.append("Invite notes: " + body[:1500])
    if invite_lines:
        sections.append("MEETING INVITE:\n" + "\n".join(invite_lines))
    if meeting_parts:
        sections.append("PAST MEETINGS:\n" + "\n\n".join(meeting_parts))
    if email_parts:
        sections.append("RECENT EMAILS:\n" + "\n\n".join(email_parts))

    if not sections:
        return {"brief": "", "based_on": 0, "emails_used": 0, "mail_available": mail_ok}
    text = assistant.brief(subject, names, "\n\n".join(sections))
    return {"brief": text, "based_on": len(rows), "emails_used": len(email_parts), "mail_available": mail_ok}


def _pregen_briefs(app) -> None:
    """Pre-generate briefs for approved meetings starting within ~48h (cheap cap)."""
    db = app.state.db
    try:
        events = calendars.upcoming_events(7)
    except Exception:  # noqa: BLE001 - calendar offline
        return
    approved = autorecord.approved_keys()
    cached = briefs.all()
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=48)
    made = 0
    for ev in events:
        if made >= 2:  # bound work per cycle
            break
        key = autorecord.event_key(ev)
        if key not in approved or (cached.get(key) or {}).get("brief"):
            continue
        try:
            start = datetime.fromisoformat(ev.get("start", ""))
        except (ValueError, TypeError):
            continue
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if not (now <= start <= horizon):
            continue
        try:
            result = build_brief(db, ev)
        except Exception:  # noqa: BLE001 - model down etc.
            continue
        if result.get("brief"):
            briefs.set(key, {
                "subject": ev.get("subject", ""), "start": ev.get("start", ""),
                "brief": result["brief"], "based_on": result["based_on"],
                "emails_used": result["emails_used"], "generated_at": now.isoformat(),
            })
            made += 1


def _begin_recording(app) -> int:
    """Create a recording row, make its folder, and start the recorder.

    Shared by the manual ``/api/record/start`` endpoint and the auto-record
    scheduler. Marks the row as errored and re-raises if the recorder won't
    start. Caller must ensure the recorder isn't already running.
    """
    db = app.state.db
    recorder = app.state.recorder
    rec_id = db.create_recording(status="recording")
    app.state.current_id = rec_id
    _rec_dir(rec_id).mkdir(parents=True, exist_ok=True)
    try:
        recorder.start()
    except Exception as exc:  # noqa: BLE001 - record the failure on the row
        log.exception("Failed to start recorder")
        db.set_status(rec_id, "error", str(exc))
        app.state.current_id = None
        raise
    return rec_id


def _end_recording(app) -> int:
    """Stop the recorder, persist the audio paths, and kick off processing.

    Shared by ``/api/record/stop`` and the auto-record scheduler. Caller must
    ensure the recorder is running.
    """
    db = app.state.db
    recorder = app.state.recorder
    rec_id = app.state.current_id
    result = recorder.stop(str(_rec_dir(rec_id)))
    db.update_recording(
        rec_id,
        mic_path=result.mic_path,
        system_path=result.system_path,
        mixed_path=result.mixed_path,
        duration_s=result.duration_s,
    )
    app.state.current_id = None
    app.state.run_pipeline(rec_id, result)
    return rec_id


def require_auth(request: Request) -> None:
    """FastAPI dependency enforcing the shared-secret token when configured.

    A no-op when ``config.ALONARG_TOKEN`` is empty (open, current behavior).
    Otherwise requires either an ``Authorization: Bearer <token>`` header or a
    ``?token=<token>`` query parameter equal to the configured token; raises
    ``HTTPException(401)`` if neither matches.
    """
    token = config.ALONARG_TOKEN
    if not token:
        return
    provided = None
    header = request.headers.get("authorization") or request.headers.get("Authorization")
    if header and header.lower().startswith("bearer "):
        provided = header[7:].strip()
    if provided is None:
        provided = request.query_params.get("token")
    if provided != token:
        raise HTTPException(status_code=401, detail="Invalid or missing token")


def create_app(db=None, recorder=None, run_pipeline=None, run_ingest=None) -> FastAPI:
    """Build the Alonarg FastAPI application.

    ``db``, ``recorder``, ``run_pipeline`` and ``run_ingest`` are injectable for
    tests. By default they are a real :class:`Database`, a real
    :class:`audio_capture.Recorder`, a callback that runs
    :func:`pipeline.process_recording` on a daemon thread, and a callback that
    runs :func:`ingest.process_upload` on a daemon thread.
    """
    db = db or Database()
    recorder = recorder or audio_capture.Recorder()

    if run_pipeline is None:
        def run_pipeline(rec_id, recording_result):  # noqa: ANN001
            thread = threading.Thread(
                target=pipeline.process_recording,
                args=(db, rec_id, recording_result),
                kwargs={"enrich_fn": lambda rid: _enrich_recording(db, rid)},
                daemon=True,
            )
            thread.start()

    if run_ingest is None:
        def run_ingest(rec_id, wav_path):  # noqa: ANN001
            thread = threading.Thread(
                target=ingest.process_upload,
                args=(db, rec_id, wav_path),
                daemon=True,
            )
            thread.start()

    app = FastAPI(title="Alonarg")
    app.state.db = db
    app.state.recorder = recorder
    app.state.run_pipeline = run_pipeline
    app.state.run_ingest = run_ingest
    app.state.current_id = None
    app.state.live_meeting = None
    # Set to the event key when WE auto-started a recording, so the scheduler
    # knows to auto-stop it (and never auto-stops a manual recording).
    app.state.autorecord_active_key = None

    # Allow the phone PWA (a different origin) to call the API. Bearer-token
    # auth means we never use cookies/credentials.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins(),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    # -- pages ------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        recordings = db.list_recordings()
        # Pinned meetings float to the top (stable: keeps newest-first within each group).
        recordings.sort(key=lambda r: 0 if (r.get("state") or {}).get("pinned") else 1)
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {"recordings": recordings, "token": config.ALONARG_TOKEN},
        )

    @app.get("/recording/{rec_id}", response_class=HTMLResponse)
    def recording_detail(request: Request, rec_id: int):
        rec = db.get_recording(rec_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="Recording not found")
        segments = (rec.get("transcript") or {}).get("segments")
        return templates.TemplateResponse(
            request,
            "detail.html",
            {
                "rec": rec,
                "token": config.ALONARG_TOKEN,
                "talk_time": analytics.talk_time(segments),
                "coaching": analytics.coaching_metrics(segments),
                "related": db.related_recordings(rec_id),
                "templates": summarize.template_choices(),
            },
        )

    # -- recording control / status --------------------------------------
    @app.get("/api/status", dependencies=[Depends(require_auth)])
    def status():
        return {
            "recording": recorder.is_recording,
            "elapsed_s": recorder.elapsed_s,
            "current_id": app.state.current_id,
        }

    @app.post("/api/record/start", dependencies=[Depends(require_auth)])
    def record_start():
        if recorder.is_recording:
            raise HTTPException(status_code=409, detail="Already recording")
        # A manual start clears any auto-record ownership of the session.
        app.state.autorecord_active_key = None
        try:
            rec_id = _begin_recording(app)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"Could not start recording: {exc}")
        return {"id": rec_id}

    @app.post("/api/record/stop", dependencies=[Depends(require_auth)])
    def record_stop():
        if not recorder.is_recording:
            raise HTTPException(status_code=409, detail="Not recording")
        app.state.autorecord_active_key = None
        rec_id = _end_recording(app)
        return {"id": rec_id, "status": "transcribing"}

    # -- upload (phone PWA) ----------------------------------------------
    @app.post("/api/upload", dependencies=[Depends(require_auth)])
    async def api_upload(
        file: UploadFile,
        title: str | None = Form(default=None),
        source: str | None = Form(default=None),
        created_at: str | None = Form(default=None),
        client_duration_s: float | None = Form(default=None),
    ):
        rec_id = db.create_recording(
            title=title or "Phone recording",
            status="recording",
            created_at=created_at or None,
        )
        rec_dir = _rec_dir(rec_id)
        rec_dir.mkdir(parents=True, exist_ok=True)

        ext = _upload_ext(file.filename, file.content_type)
        upload_path = rec_dir / f"upload{ext}"
        data = await file.read()
        upload_path.write_bytes(data)

        mixed_path = rec_dir / "mixed.wav"
        ingest.convert_to_wav(str(upload_path), str(mixed_path))

        db.update_recording(
            rec_id,
            mixed_path=str(mixed_path),
            mic_path=str(mixed_path),
            system_path=None,
            duration_s=client_duration_s or 0.0,
            status="transcribing",
        )
        app.state.run_ingest(rec_id, str(mixed_path))
        return {"id": rec_id, "status": "transcribing"}

    # -- recordings CRUD (JSON) ------------------------------------------
    @app.get("/api/recordings", dependencies=[Depends(require_auth)])
    def api_recordings():
        return db.list_recordings()

    @app.get("/api/recordings/{rec_id}", dependencies=[Depends(require_auth)])
    def api_recording(rec_id: int):
        rec = db.get_recording(rec_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="Recording not found")
        return rec

    @app.patch("/api/recordings/{rec_id}", dependencies=[Depends(require_auth)])
    def api_rename(rec_id: int, title: str = Body(..., embed=True)):
        rec = db.get_recording(rec_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="Recording not found")
        new_title = (title or "").strip()
        if not new_title:
            raise HTTPException(status_code=400, detail="Title cannot be empty")
        db.update_recording(rec_id, title=new_title)
        return {"id": rec_id, "title": new_title}

    @app.put("/api/recordings/{rec_id}/summary", dependencies=[Depends(require_auth)])
    def api_update_summary(
        rec_id: int,
        summary: str | None = Body(default=None),
        action_items: list[str] | None = Body(default=None),
        next_steps: list[str] | None = Body(default=None),
    ):
        rec = db.get_recording(rec_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="Recording not found")
        cur = rec.get("summary") or {}
        merged = SummaryResult(
            title=cur.get("title", ""),
            summary=cur.get("summary", "") if summary is None else summary.strip(),
            action_items=list(cur.get("action_items", []))
            if action_items is None
            else _clean_list(action_items),
            next_steps=list(cur.get("next_steps", []))
            if next_steps is None
            else _clean_list(next_steps),
        )
        db.set_summary(rec_id, merged)
        return merged.to_dict()

    @app.post("/api/recordings/{rec_id}/resummarize", dependencies=[Depends(require_auth)])
    def api_resummarize(rec_id: int, template: str = Body("general", embed=True)):
        rec = db.get_recording(rec_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="Recording not found")
        transcript = (rec.get("transcript") or {}).get("text", "")
        if not transcript.strip():
            raise HTTPException(status_code=400, detail="No transcript to summarize yet")
        if template not in summarize.TEMPLATES:
            template = "general"
        try:
            result = summarize.summarize(transcript, template=template)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        # Keep the user's (possibly edited) title rather than the model's.
        cur_title = (rec.get("title") or "").strip()
        if cur_title:
            result.title = cur_title
        db.set_summary(rec_id, result)
        db.set_state(rec_id, _merge_state(rec.get("state"), {"template": template}))
        return {"summary": result.to_dict(), "template": template}

    @app.put("/api/recordings/{rec_id}/meta", dependencies=[Depends(require_auth)])
    def api_update_meta(
        rec_id: int,
        people: list[str] | None = Body(default=None),
        contacts: list[dict] | None = Body(default=None),
    ):
        rec = db.get_recording(rec_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="Recording not found")
        cur = rec.get("meta") or {}
        meta = {
            "people": _clean_list(people) if people is not None else list(cur.get("people", []) or []),
            "contacts": _clean_contacts(contacts) if contacts is not None else _clean_contacts(cur.get("contacts", [])),
        }
        db.set_meta(rec_id, meta)
        return meta

    @app.post("/api/recordings/{rec_id}/detect", dependencies=[Depends(require_auth)])
    def api_detect(rec_id: int):
        rec = db.get_recording(rec_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="Recording not found")
        transcript = (rec.get("transcript") or {}).get("text", "")
        summary = (rec.get("summary") or {}).get("summary", "")
        try:
            detected = assistant.extract_details(transcript, summary)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        merged = _merge_meta(rec.get("meta") or {}, detected)
        db.set_meta(rec_id, merged)
        return merged

    @app.post("/api/recordings/{rec_id}/sync-calendar", dependencies=[Depends(require_auth)])
    def api_sync_calendar(rec_id: int):
        rec = db.get_recording(rec_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="Recording not found")
        try:
            event = calendars.find_event_for(rec.get("created_at") or "")
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=f"Could not read the calendar: {exc}")
        if not event:
            return {"matched": False}
        people: list[str] = []
        contacts: list[dict] = []
        for a in event.get("attendees", []) or []:
            name = str(a.get("name", "")).strip()
            email = str(a.get("email", "")).strip()
            if name and name.lower() not in {p.lower() for p in people}:
                people.append(name)
            if name or email:
                contacts.append({"name": name, "email": email, "phone": ""})
        merged = _merge_meta(rec.get("meta") or {}, {"people": people, "contacts": contacts})
        db.set_meta(rec_id, merged)
        subject = str(event.get("subject", "")).strip()
        cur_title = (rec.get("title") or "").strip()
        title_updated = False
        if subject and cur_title in ("", "Untitled recording", "Phone recording"):
            db.update_recording(rec_id, title=subject)
            title_updated = True
        return {
            "matched": True,
            "subject": subject,
            "attendees": len(contacts),
            "title_updated": title_updated,
            "meta": merged,
        }

    @app.delete("/api/recordings/{rec_id}", dependencies=[Depends(require_auth)])
    def api_delete(rec_id: int):
        rec = db.get_recording(rec_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="Recording not found")
        folder = _rec_dir(rec_id)
        if folder.exists():
            try:
                shutil.rmtree(folder)
            except Exception:  # noqa: BLE001 - best effort cleanup
                log.warning("Could not remove recording folder %s", folder)
        db.delete_recording(rec_id)
        return {"deleted": True}

    # -- search + local-AI assistant -------------------------------------
    @app.get("/api/search", dependencies=[Depends(require_auth)])
    def api_search(q: str = ""):
        return db.search_recordings(q)

    @app.post("/api/ask", dependencies=[Depends(require_auth)])
    def api_ask(
        question: str = Body(...),
        recording_id: int | None = Body(default=None),
    ):
        q = (question or "").strip()
        if not q:
            raise HTTPException(status_code=400, detail="Question is required")
        if recording_id is not None:
            rec = db.get_recording(recording_id)
            if rec is None:
                raise HTTPException(status_code=404, detail="Recording not found")
            context, used = _context_for_recording(rec), [recording_id]
        else:
            context, used = _global_context(db)
        if not context.strip():
            return {"answer": "There are no meetings to search yet.", "used": []}
        try:
            answer = assistant.ask(q, context)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        return {"answer": answer, "used": used}

    @app.post("/api/draft-email", dependencies=[Depends(require_auth)])
    def api_draft_email(
        item: str = Body(...),
        recording_id: int | None = Body(default=None),
    ):
        it = (item or "").strip()
        if not it:
            raise HTTPException(status_code=400, detail="Action item is required")
        context = ""
        if recording_id is not None:
            rec = db.get_recording(recording_id)
            if rec is None:
                raise HTTPException(status_code=404, detail="Recording not found")
            summ = rec.get("summary") or {}
            context = (rec.get("title") or "") + "\n" + (summ.get("summary") or "")
        try:
            draft = assistant.draft_email(it, context)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        return draft

    # -- tags / pin + global action-item (tasks) hub ---------------------
    @app.put("/api/recordings/{rec_id}/state", dependencies=[Depends(require_auth)])
    def api_update_state(
        rec_id: int,
        tags: list[str] | None = Body(default=None),
        pinned: bool | None = Body(default=None),
    ):
        rec = db.get_recording(rec_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="Recording not found")
        updates: dict = {}
        if tags is not None:
            updates["tags"] = _clean_list(tags)
        if pinned is not None:
            updates["pinned"] = bool(pinned)
        state = _merge_state(rec.get("state"), updates)
        db.set_state(rec_id, state)
        return state

    @app.get("/api/action-items", dependencies=[Depends(require_auth)])
    def api_action_items(open_only: bool = False):
        """Every action item across all meetings, with its done state."""
        out: list[dict] = []
        for row in db.list_recordings():
            summ = row.get("summary") or {}
            items = summ.get("action_items") or []
            if not items:
                continue
            done = set((row.get("state") or {}).get("done_actions") or [])
            for it in items:
                is_done = it in done
                if open_only and is_done:
                    continue
                out.append({
                    "recording_id": row["id"],
                    "title": row.get("title") or "Untitled",
                    "created_at": row.get("created_at") or "",
                    "item": it,
                    "done": is_done,
                })
        return out

    @app.post("/api/action-items/toggle", dependencies=[Depends(require_auth)])
    def api_toggle_action(
        recording_id: int = Body(...),
        item: str = Body(...),
        done: bool = Body(...),
    ):
        rec = db.get_recording(recording_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="Recording not found")
        state = dict(rec.get("state") or {})
        done_actions = [d for d in (state.get("done_actions") or []) if d != item]
        if done:
            done_actions.append(item)
        state["done_actions"] = done_actions
        db.set_state(recording_id, state)
        return {"ok": True, "done": done}

    # -- meeting nudges + web push ---------------------------------------
    @app.get("/api/nudge/status", dependencies=[Depends(require_auth)])
    def nudge_status():
        recording = getattr(recorder, "is_recording", False)
        live = app.state.live_meeting
        return {"recording": recording, "live_meeting": live, "nudgeable": bool(live) and not recording}

    @app.get("/api/push/key", dependencies=[Depends(require_auth)])
    def push_key():
        return {"key": push.public_key()}

    @app.post("/api/push/subscribe", dependencies=[Depends(require_auth)])
    def push_subscribe(subscription: dict = Body(...)):
        push.add_subscription(subscription)
        return {"ok": True}

    @app.post("/api/push/unsubscribe", dependencies=[Depends(require_auth)])
    def push_unsubscribe(endpoint: str = Body(..., embed=True)):
        push.remove_subscription(endpoint)
        return {"ok": True}

    @app.post("/api/push/test", dependencies=[Depends(require_auth)])
    def push_test():
        return push.send_to_all("Alonarg", "Test nudge — this is how meeting reminders look.", "/")

    # -- Microsoft Graph calendar (new Outlook / M365) -------------------
    @app.get("/api/graph/status", dependencies=[Depends(require_auth)])
    def graph_status():
        signed_in = msgraph.is_signed_in()
        return {
            "signed_in": signed_in,
            "account": msgraph.account_name(),
            "pending": msgraph.login_pending(),
            "mail": msgraph.mail_available() if signed_in else False,
        }

    @app.post("/api/graph/login", dependencies=[Depends(require_auth)])
    def graph_login():
        try:
            return msgraph.start_device_login()
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc))

    @app.post("/api/graph/logout", dependencies=[Depends(require_auth)])
    def graph_logout():
        msgraph.sign_out()
        return {"signed_in": False}

    # -- calendar view + auto-record -------------------------------------
    @app.get("/api/calendar/events", dependencies=[Depends(require_auth)])
    def calendar_events(days: int = 7):
        days = max(1, min(int(days or 7), 31))
        if not msgraph.is_signed_in() and calendars.active_source() == "outlook":
            # Outlook fallback: only works if classic Outlook is running.
            try:
                events = calendars.upcoming_events(days)
            except RuntimeError:
                return {"connected": False, "source": "outlook", "events": []}
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=503, detail=f"Could not read the calendar: {exc}")
        else:
            if not msgraph.is_signed_in():
                return {"connected": False, "source": "graph", "events": []}
            try:
                events = calendars.upcoming_events(days)
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=503, detail=f"Could not read the calendar: {exc}")
        approved = autorecord.approved_keys()
        for ev in events:
            key = autorecord.event_key(ev)
            ev["key"] = key
            ev["auto_record"] = key in approved
        return {"connected": True, "source": calendars.active_source(), "events": events}

    @app.post("/api/calendar/autorecord", dependencies=[Depends(require_auth)])
    def calendar_autorecord(
        event: dict = Body(...),
        enabled: bool = Body(...),
    ):
        if enabled:
            entry = autorecord.approve(event)
            if not entry:
                raise HTTPException(status_code=400, detail="Event is missing an identifier")
        else:
            autorecord.unapprove(autorecord.event_key(event))
        return {"ok": True, "enabled": enabled, "key": autorecord.event_key(event)}

    @app.post("/api/calendar/brief", dependencies=[Depends(require_auth)])
    def api_brief(
        event: dict = Body(default=None),
        subject: str = Body(""),
        attendees: list[str] = Body(default=None),
        emails: list[str] = Body(default=None),
    ):
        """A pre-meeting brief from the calendar invite, past meetings, and recent emails.

        Prefer passing the full ``event`` (subject/attendees/body). Falls back to
        ``subject`` + ``attendees`` (names) + ``emails`` for older callers.
        """
        if not event:
            att = [{"name": n, "email": ""} for n in (attendees or [])]
            att += [{"name": "", "email": e} for e in (emails or [])]
            event = {"subject": subject or "", "attendees": att}
        try:
            return build_brief(db, event)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc))

    # -- pre-meeting briefs (auto-generated for flagged meetings) ---------
    @app.get("/api/briefs", dependencies=[Depends(require_auth)])
    def api_list_briefs():
        connected = msgraph.is_signed_in()
        try:
            events = calendars.upcoming_events(14)
        except Exception:  # noqa: BLE001 - calendar offline/unavailable
            events = []
        approved = autorecord.approved_keys()
        cached = briefs.all()
        items = []
        for ev in events:
            key = autorecord.event_key(ev)
            if key not in approved:
                continue
            c = cached.get(key) or {}
            items.append({
                "key": key,
                "subject": ev.get("subject", ""),
                "start": ev.get("start", ""),
                "end": ev.get("end", ""),
                "brief": c.get("brief", ""),
                "generated_at": c.get("generated_at", ""),
                "based_on": c.get("based_on", 0),
                "emails_used": c.get("emails_used", 0),
                "has_brief": bool(c.get("brief")),
            })
        items.sort(key=lambda x: x.get("start", ""))
        return {"connected": connected, "mail": msgraph.mail_available(), "briefs": items}

    @app.post("/api/briefs/generate", dependencies=[Depends(require_auth)])
    def api_generate_brief(key: str = Body(..., embed=True)):
        try:
            events = calendars.upcoming_events(14)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=f"Could not read the calendar: {exc}")
        ev = next((e for e in events if autorecord.event_key(e) == key), None)
        if ev is None:
            raise HTTPException(status_code=404, detail="Meeting not found in your upcoming calendar")
        try:
            result = build_brief(db, ev)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        generated_at = datetime.now(timezone.utc).isoformat()
        briefs.set(key, {
            "subject": ev.get("subject", ""), "start": ev.get("start", ""),
            "brief": result["brief"], "based_on": result["based_on"],
            "emails_used": result["emails_used"], "generated_at": generated_at,
        })
        return {**result, "key": key, "generated_at": generated_at}

    # -- keyword trackers (across all meetings) --------------------------
    def _tracker_counts(terms: list[str]) -> list[dict]:
        return [{"term": t, "count": len(db.search_recordings(t))} for t in terms]

    @app.get("/api/trackers", dependencies=[Depends(require_auth)])
    def api_trackers():
        return _tracker_counts(trackers.list_trackers())

    @app.post("/api/trackers", dependencies=[Depends(require_auth)])
    def api_add_tracker(term: str = Body(..., embed=True)):
        return _tracker_counts(trackers.add_tracker(term))

    @app.post("/api/trackers/delete", dependencies=[Depends(require_auth)])
    def api_del_tracker(term: str = Body(..., embed=True)):
        return _tracker_counts(trackers.remove_tracker(term))

    # -- system / admin info (Settings view) ------------------------------
    @app.get("/api/system/info", dependencies=[Depends(require_auth)])
    def system_info():
        rows = db.list_recordings()
        return {
            "data_dir": str(config.DATA_DIR),
            "db_path": str(config.DB_PATH),
            "recordings_dir": str(config.RECORDINGS_DIR),
            "model": config.OLLAMA_MODEL,
            "whisper_model": config.WHISPER_MODEL,
            "recordings_count": len(rows),
            "calendar_source": calendars.active_source(),
            "calendar_connected": msgraph.is_signed_in(),
            "auto_record_count": len(autorecord.list_approved()),
        }

    # -- audio playback ---------------------------------------------------
    @app.get("/audio/{rec_id}", dependencies=[Depends(require_auth)])
    def audio(rec_id: int):
        rec = db.get_recording(rec_id)
        if rec is None:
            raise HTTPException(status_code=404, detail="Recording not found")
        mixed_path = rec.get("mixed_path")
        if not mixed_path or not Path(mixed_path).exists():
            raise HTTPException(status_code=404, detail="Audio not available")
        # FileResponse handles HTTP Range requests (seeking) automatically.
        return FileResponse(mixed_path, media_type="audio/wav")

    return app


def start_nudge_scheduler(app) -> threading.Thread:
    """Background loop: auto-record approved meetings + nudge for the rest.

    Polls the calendar every ``config.NUDGE_POLL_SECONDS`` and:
      * updates ``app.state.live_meeting`` for the dashboard banner,
      * auto-starts a recording when an approved meeting is in progress, and
        auto-stops it when that meeting ends (never touching manual recordings),
      * otherwise sends one web-push nudge per live, non-approved meeting.
    Failures are swallowed so the loop never dies.
    """
    def _loop():
        nudged: set = set()
        while True:
            try:
                event = calendars.find_current_event()
            except Exception:  # noqa: BLE001
                event = None
            app.state.live_meeting = (
                {"subject": event.get("subject", ""), "start": event.get("start", ""),
                 "end": event.get("end", "")}
                if event else None
            )
            recorder = app.state.recorder
            recording = getattr(recorder, "is_recording", False)
            active_key = getattr(app.state, "autorecord_active_key", None)

            # --- auto-record approved meetings ---
            try:
                action = autorecord.decide(event, recording, active_key)
            except Exception:  # noqa: BLE001
                action = "none"
            if action == "start":
                try:
                    _begin_recording(app)
                    app.state.autorecord_active_key = autorecord.event_key(event)
                    recording = True
                    subject = event.get("subject", "Meeting")
                    push.send_to_all("Recording started", f"Auto-recording “{subject}”.", "/")
                except Exception:  # noqa: BLE001
                    log.warning("auto-record start failed", exc_info=True)
            elif action == "stop":
                try:
                    _end_recording(app)
                    app.state.autorecord_active_key = None
                    recording = False
                except Exception:  # noqa: BLE001
                    log.warning("auto-record stop failed", exc_info=True)

            # --- nudge for live, non-approved meetings ---
            if event and not recording and not autorecord.is_approved(event):
                key = (event.get("subject", ""), event.get("start", ""))
                if key not in nudged:
                    nudged.add(key)
                    try:
                        push.send_to_all(
                            "Record this meeting?",
                            f"“{event.get('subject', 'Meeting')}” is in progress. Open Alonarg to record.",
                            "/",
                        )
                    except Exception:  # noqa: BLE001
                        log.warning("nudge push failed", exc_info=True)

            # --- pre-generate briefs for soon, flagged meetings ---
            try:
                _pregen_briefs(app)
            except Exception:  # noqa: BLE001
                log.warning("brief pre-generation failed", exc_info=True)

            time.sleep(max(15, config.NUDGE_POLL_SECONDS))

    thread = threading.Thread(target=_loop, daemon=True, name="alonarg-nudge")
    thread.start()
    return thread


# Module-level app so ``uvicorn alonarg.server:app`` works.
app = create_app()
