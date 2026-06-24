# Alonarg — Architecture & Module Contracts

Alonarg is a **local** (Windows) meeting recorder like Granola:
mic + system audio → local transcription → Claude summary (via the user's plan,
**not** the API) → a dashboard with summaries, action items, transcript & playback.

## Golden rules for every module
- Python 3.14, Windows. The venv is at `%LOCALAPPDATA%\Alonarg\venv`.
- Import shared types from `alonarg.types`; import settings from `alonarg.config`.
- **Never change a public signature** defined here — other modules + tests depend on it.
- Every module ships with `tests/test_<module>.py` that passes via the project venv:
  `& "$env:LOCALAPPDATA\Alonarg\venv\Scripts\python.exe" -m pytest tests/test_<module>.py`
- Pure logic (resampling, mixing, JSON extraction, segment merge) must be split into
  small functions and unit-tested **without** hardware/model/network. Anything needing
  real devices / the whisper model / the live `claude` CLI is marked `@pytest.mark.integration`.
- No hard-coding to test inputs. Solve the general problem.

## Shared types (`alonarg/types.py`) — already implemented
- `RecordingResult(mixed_path, duration_s, sample_rate, mic_path=None, system_path=None)`
- `Segment(speaker, start, end, text)`  — speaker is `"You"` or `"Others"`
- `TranscriptResult(segments: list[Segment], text: str, language: str)`
- `SummaryResult(title, summary, action_items: list[str], next_steps: list[str])`
All have `to_dict()`; the latter three have `from_dict()`.

## config (`alonarg/config.py`) — already implemented
Key names: `SAMPLE_RATE=16000`, `WHISPER_MODEL`, `WHISPER_DEVICE`, `WHISPER_COMPUTE`,
`DB_PATH`, `RECORDINGS_DIR`, `MODELS_DIR`, `SERVER_HOST`, `SERVER_PORT`, `HOTKEY`,
`CLAUDE_MODEL`, `CLAUDE_TIMEOUT`. Functions: `ensure_dirs()`, `base_url()`,
`resolve_claude_binary() -> str`.

---

## Module: `alonarg/audio_capture.py`
Capture mic + Windows system audio (WASAPI loopback) on **separate tracks**, resample
both to 16 kHz mono, write `mic.wav`, `system.wav`, and a `mixed.wav` (for playback).

Pure functions (unit-test with synthetic numpy arrays — no hardware):
- `to_mono(samples: np.ndarray) -> np.ndarray` — average channels; 1-D passthrough.
- `resample_to_16k(samples: np.ndarray, src_rate: int) -> np.ndarray` — float32 mono @16k via linear interp; identity when src_rate==16000.
- `mix_tracks(a: np.ndarray, b: np.ndarray) -> np.ndarray` — zero-pad to equal length, sum, clip to [-1, 1].
- `write_wav(path, samples: np.ndarray, rate: int = 16000) -> None` — PCM_16 mono via soundfile.
- `read_wav(path) -> tuple[np.ndarray, int]` — returns (float32 mono, rate).

Device discovery (integration):
- `find_devices() -> dict` — returns `{"mic": <input device info or None>, "loopback": <WASAPI loopback device info or None>}`.
  Use PyAudioWPatch: default WASAPI output's loopback for system audio, default input for mic.

Recorder (start/stop, used by the server):
- `class Recorder:`
  - `__init__(self, sample_rate: int = 16000)`
  - `start(self) -> None` — begin capturing mic + loopback in background threads. Idempotent-safe: raise RuntimeError if already recording.
  - `stop(self, out_dir: str | Path) -> RecordingResult` — stop threads, resample, write `mic.wav`/`system.wav` (only for tracks that captured data) and always `mixed.wav`; return `RecordingResult`. If a device was unavailable, that track is `None` and mixed uses whatever exists (silence if neither).
  - `is_recording: bool` (property)
  - `elapsed_s: float` (property) — seconds since start (0 if not recording)
Robustness: a missing/failing device must NOT crash the recorder — log and continue with the other track.

Tests: unit-test the 5 pure functions thoroughly (mixing length/clipping, resample length≈rate ratio, mono averaging, wav round-trip). Mark any real-capture test `integration`.

---

## Module: `alonarg/transcribe.py`
Transcribe each track with faster-whisper and merge into one speaker-labeled transcript.

- `get_model(model_size=config.WHISPER_MODEL, device=config.WHISPER_DEVICE, compute_type=config.WHISPER_COMPUTE)` — return a cached `WhisperModel` (module-level singleton keyed by args).
- `transcribe_track(path: str, speaker: str, model=None) -> tuple[list[Segment], str]` — returns (segments labeled `speaker`, detected language). Empty list if path is None/missing/silent.
- `merge_segments(*segment_lists: list[Segment]) -> list[Segment]` — concatenate, sort by `start` (stable).  **Pure — unit-test this.**
- `render_text(segments: list[Segment]) -> str` — lines like `[mm:ss] You: ...`.  **Pure — unit-test this.**
- `transcribe(mic_path: str | None, system_path: str | None, model=None) -> TranscriptResult` — transcribe each present track, merge, render text, pick language.

Tests:
- Unit (no model): `merge_segments` ordering across lists; `render_text` formatting & timestamps.
- Integration: use `tests/conftest.py::make_speech_wav` to synthesize a known phrase, run
  `transcribe(mic_path=clip, system_path=None)` and assert a distinctive word from the phrase
  appears (case-insensitive) and the segment speaker is `"You"`. Mark `integration`.

---

## Module: `alonarg/summarize.py`
Summarize a transcript with a small **local LLM** via Ollama's HTTP API (no API key, no
subscription, fully offline). Config: `OLLAMA_HOST` (default `http://127.0.0.1:11434`),
`OLLAMA_MODEL` (default `llama3.2:3b`), `OLLAMA_NUM_CTX` (default `8192`), `OLLAMA_TIMEOUT`.

Mechanism: POST `{OLLAMA_HOST}/api/chat` with `{"model", "messages":[{system},{user}],
"stream": false, "format": "json", "options": {"temperature": 0.2, "num_ctx": OLLAMA_NUM_CTX}}`.
`format:"json"` forces Ollama to return valid JSON. The model's answer is in
`response["message"]["content"]` — itself the JSON object we asked for.

- `build_prompt(transcript_text) -> tuple[str,str]` (or system+user strings) — instruct the model
  to output ONLY a JSON object with keys `title` (short), `summary` (concise paragraph(s)),
  `action_items` (array of strings), `next_steps` (array of strings).
- `extract_json(text: str) -> dict` — robustly pull a JSON object from text: strip ```json fences,
  else find the first balanced `{...}`. Raises ValueError if none.  **Pure — unit-test.**
- `parse_summary(ollama_response: dict) -> SummaryResult` — take `["message"]["content"]`,
  `extract_json`, build `SummaryResult`.  **Pure — unit-test with canned response dicts.**
- `summarize(transcript_text: str, model: str | None = None, host: str | None = None, timeout: int = config.OLLAMA_TIMEOUT) -> SummaryResult`
  — POST to Ollama via httpx, raise a clear error if Ollama is unreachable (hint: start Ollama /
  `ollama pull <model>`) or returns an error, else `parse_summary(response.json())`.

Tests:
- Unit (monkeypatch `httpx.post`/Client): `extract_json` (raw, fenced, prose, nested braces);
  `parse_summary` with a realistic `{"message":{"content":"{...}"}}` dict; `summarize` returns the
  expected `SummaryResult` and posts to `/api/chat` with `format:"json"`; connection error raises a clear RuntimeError.
- Integration: real `summarize("You: we ship Friday. Others: I'll email the client.")` against local
  Ollama; assert non-empty title/summary and that action_items/next_steps are lists. Mark `integration`.

---

## Module: `alonarg/db.py`
SQLite persistence (stdlib `sqlite3`).

- `class Database:`
  - `__init__(self, path: str | Path = config.DB_PATH)` — connect (check_same_thread=False), create schema if absent.
  - `create_recording(self, *, title="Untitled recording", status="recording", created_at=None, mic_path=None, system_path=None, mixed_path=None, duration_s=0.0) -> int` — returns new id. `created_at` defaults to ISO-8601 UTC now.
  - `update_recording(self, rec_id: int, **fields) -> None` — update arbitrary columns (whitelist).
  - `set_status(self, rec_id, status: str, error: str | None = None) -> None`
  - `set_transcript(self, rec_id, transcript: TranscriptResult) -> None`
  - `set_summary(self, rec_id, summary: SummaryResult) -> None`
  - `get_recording(self, rec_id) -> dict | None` — full row; `transcript` and `summary` parsed to dicts (or None).
  - `list_recordings(self) -> list[dict]` — newest first; each row includes parsed `summary` (dict|None), counts, but may omit the big transcript body.
  - `delete_recording(self, rec_id) -> bool` — delete row; return True if existed.
  - `close(self) -> None`

Status vocabulary: `recording` → `transcribing` → `summarizing` → `done`; or `error`.
Columns: id, title, created_at, duration_s, status, error, mic_path, system_path, mixed_path,
transcript_json, summary_json.

Tests: temp-file DB; create→get round-trip; status transitions; transcript/summary store+parse;
list ordering newest-first; delete returns True then False.

---

## Module: `alonarg/pipeline.py`
Orchestrate post-recording processing.

- `process_recording(db: Database, rec_id: int, recording: RecordingResult, *, transcribe_fn=..., summarize_fn=...) -> None`
  1. `set_status(rec_id, "transcribing")`; `t = transcribe_fn(recording.mic_path, recording.system_path)`; `db.set_transcript(rec_id, t)`.
  2. `set_status(rec_id, "summarizing")`; `s = summarize_fn(t.text)`; `db.set_summary(rec_id, s)`; if `s.title`, update row title.
  3. `set_status(rec_id, "done")`.
  - On any exception: `set_status(rec_id, "error", str(exc))` and swallow (do not crash caller).
  `transcribe_fn`/`summarize_fn` are injectable for testing (defaults = real ones).

Tests: inject fake transcribe/summarize; assert status sequence and stored data; inject a raising
fn and assert status becomes `error` with message.

---

## Module: `alonarg/server.py`
FastAPI app + server-rendered dashboard (Jinja2 templates in `alonarg/templates`,
static CSS/JS in `alonarg/static`). **No external CDNs** (offline-friendly).

- `create_app(db: Database | None = None, recorder=None, run_pipeline=None) -> FastAPI`
  - `db` defaults to `Database()`; `recorder` defaults to `audio_capture.Recorder()`;
    `run_pipeline(rec_id, recording_result)` defaults to spawning a daemon thread that calls
    `pipeline.process_recording(db, ...)`. All injectable for tests.
- Module-level `app = create_app()` for uvicorn (`uvicorn alonarg.server:app`).

Routes:
- `GET /` → dashboard.html (list of recordings, with live record control).
- `GET /recording/{id}` → detail.html (summary, action items, next steps, transcript, audio player). 404 if missing.
- `GET /api/status` → `{"recording": bool, "elapsed_s": float, "current_id": int|null}`.
- `POST /api/record/start` → create row(status=recording), `recorder.start()`, store current id → `{"id": int}`. 409 if already recording.
- `POST /api/record/stop` → `recorder.stop(dir)`, update row (paths,duration), `run_pipeline(id, result)`, clear current → `{"id": int, "status": "transcribing"}`. 409 if not recording.
- `GET /api/recordings` → list (JSON).
- `GET /api/recordings/{id}` → one (JSON) or 404.
- `DELETE /api/recordings/{id}` → delete row + files → `{"deleted": true}` or 404.
- `GET /audio/{id}` → serve `mixed_path` with HTTP range support (use Starlette `FileResponse`). 404 if missing.

Recording dir per id: `config.RECORDINGS_DIR / str(id)` (created on start).

Tests (TestClient, temp DB, **fake recorder**, synchronous `run_pipeline` stub):
- start→status(recording true)→stop→status(false); 409 paths; recordings list/get/delete;
  `/audio/{id}` returns 200 + audio content-type for a real small wav; 404s. Do **not** require
  real devices or whisper in server tests.

---

## Module: `alonarg/tray.py` + entry point `alonarg/__main__.py` / `run.py`
- Tray (`pystray`) + global hotkey (`keyboard`, default `config.HOTKEY`) that toggle recording by
  calling the local server API (`POST /api/record/start|stop`) via httpx; menu: Start/Stop,
  Open Dashboard, Quit. Failures here must never crash the app.
- Entry point: `config.ensure_dirs()`, start uvicorn in a thread, start tray+hotkey, open the
  dashboard in the browser. `python -m alonarg`.

Tests: light — `build_menu()`/toggle logic with a mocked HTTP client; no real tray loop in tests.
