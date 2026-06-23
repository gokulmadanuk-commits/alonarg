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
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import FileResponse

from alonarg import audio_capture, config, ingest, pipeline
from alonarg.db import Database

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
        return templates.TemplateResponse(
            request, "detail.html", {"rec": rec, "token": config.ALONARG_TOKEN}
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
        rec_id = db.create_recording(status="recording")
        app.state.current_id = rec_id
        _rec_dir(rec_id).mkdir(parents=True, exist_ok=True)
        try:
            recorder.start()
        except Exception as exc:  # noqa: BLE001
            log.exception("Failed to start recorder")
            db.set_status(rec_id, "error", str(exc))
            app.state.current_id = None
            raise HTTPException(status_code=500, detail=f"Could not start recording: {exc}")
        return {"id": rec_id}

    @app.post("/api/record/stop", dependencies=[Depends(require_auth)])
    def record_stop():
        if not recorder.is_recording:
            raise HTTPException(status_code=409, detail="Not recording")
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


# Module-level app so ``uvicorn alonarg.server:app`` works.
app = create_app()
