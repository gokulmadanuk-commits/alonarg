# Alonarg

A **local** meeting recorder, transcriber, and summarizer for Windows — like Granola,
but everything runs on your PC. It captures your **microphone** and your **system audio**
(WASAPI loopback), transcribes locally with Whisper, summarizes with a small **local LLM**
(via [Ollama](https://ollama.com) — no API, no subscription, fully offline), and shows everything
in a local dashboard with summaries, action items, next steps, full transcripts, and audio playback.

## What it does

1. **Record** — press the hotkey, click the tray icon, or hit Record in the dashboard. Mic
   and system audio are captured on **separate tracks**.
2. **Transcribe** — locally, with [faster-whisper](https://github.com/SYSTRAN/faster-whisper).
   Your mic is labeled **You**, system audio is labeled **Others**.
3. **Summarize** — the transcript is sent to a small local LLM (Ollama, default `llama3.2:3b`),
   which returns a title, a concise summary, **action items**, and **next steps**. Runs entirely
   on your machine — no API, no subscription.
4. **Review** — a dashboard lists every recording with its summary and action items; open one
   to read the full transcript and play back the audio.

## Requirements

- Windows 10/11
- Python 3.14 (the project was built and tested on 3.14.5)
- [Ollama](https://ollama.com) installed (auto-runs as a background service on `localhost:11434`)
  with a small model pulled: `ollama pull llama3.2:3b` (~2 GB)
- ~500 MB free for the Whisper "small" model (downloaded once, on first transcription)

## Setup

```powershell
# from the project folder
.\setup.ps1
```

This creates a virtual environment at `%LOCALAPPDATA%\Alonarg\venv` (kept **outside** OneDrive
so large files don't sync) and installs the dependencies from `requirements.txt`.

## Run

```powershell
.\run.ps1
# or:  & "$env:LOCALAPPDATA\Alonarg\venv\Scripts\python.exe" -m alonarg
```

This starts the local server, opens the dashboard at <http://127.0.0.1:8765>, places an icon in
the system tray, and registers the global hotkey.

### Controls
- **Hotkey:** `Ctrl+Alt+R` toggles recording from anywhere.
- **Tray icon:** Start/Stop Recording, Open Dashboard, Quit.
- **Dashboard:** a Record button with a live timer; cards update through
  `recording → transcribing → summarizing → done` on their own.

### Always-on (optional)
Run the engine headless at logon so it keeps working while the screen is locked:
```powershell
.\install-autostart.ps1        # remove with: .\install-autostart.ps1 -Remove
```
Ollama already auto-starts as a background service. Keep the laptop from sleeping for
uninterrupted processing (Windows Settings → Power).

## Phone companion (PWA)

An installable, offline-first phone app lets you record **in-person** meetings and run them
through the same pipeline and dashboard. It's live at **https://alonarg.vercel.app** (hosted on
Vercel; source in `pwa/`).

- Open it on your phone and **Add to Home Screen**. You can record and queue meetings offline.
- The heavy lifting (transcription + local-LLM summary) stays on your PC, so the phone
  uploads recordings to your PC's engine over a secure tunnel and reads back the same dashboard.
- One command on the PC sets this up: **`.\connect.ps1`** — see **[CONNECT.md](CONNECT.md)** for
  the full walkthrough (tunnel + token + PWA settings).

The backend gains `POST /api/upload`, CORS, and an optional bearer token (`ALONARG_TOKEN`) that
gates `/api/*` and `/audio/*` whenever set. With no token (default) the desktop app behaves
exactly as before.

## How it works

```
 mic ─┐                              ┌─ "You"  segments ─┐
      ├─ audio_capture (WASAPI) ─►   │                   ├─ transcribe (faster-whisper)
 sys ─┘   mic.wav / system.wav       └─ "Others" segments┘            │
                                                                      ▼
                          mixed.wav (playback)              TranscriptResult
                                                                      │
                                                                      ▼
                                              summarize  →  local LLM (Ollama)
                                                                      │
                                                                      ▼
                                   SQLite (db)  ◄── pipeline ──►  SummaryResult
                                          │
                                          ▼
                              FastAPI server + dashboard
```

Modules (`alonarg/`): `config`, `types`, `audio_capture`, `transcribe`, `summarize`, `db`,
`pipeline`, `server` (+ `templates`/`static`), `tray`, `__main__`. See `ARCHITECTURE.md` for
the full contract of each.

## Configuration (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `ALONARG_HOME` | `%LOCALAPPDATA%\Alonarg` | data root (db, recordings, models) |
| `ALONARG_MODEL` | `small` | Whisper model (`tiny`/`base`/`small`/`medium`/`large-v3`) |
| `ALONARG_DEVICE` | `cpu` | `cpu` or `cuda` (GPU; also set `ALONARG_COMPUTE=float16`) |
| `ALONARG_COMPUTE` | `int8` | ctranslate2 compute type |
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | Ollama API endpoint |
| `OLLAMA_MODEL` | `llama3.2:3b` | local summary model (e.g. `qwen2.5:3b`, `llama3.1:8b`) |
| `OLLAMA_NUM_CTX` | `8192` | context window for long transcripts |
| `ALONARG_HOTKEY` | `ctrl+alt+r` | global record toggle |
| `ALONARG_HOST` / `ALONARG_PORT` | `127.0.0.1` / `8765` | dashboard address |

## Where your data lives

`%LOCALAPPDATA%\Alonarg\` — `alonarg.db` (metadata, transcripts, summaries),
`recordings\<id>\` (`mic.wav`, `system.wav`, `mixed.wav`), and `models\` (Whisper cache).
Nothing leaves your machine — transcription and summarization both run locally.

## Tests

```powershell
& "$env:LOCALAPPDATA\Alonarg\venv\Scripts\python.exe" -m pytest            # everything (89 tests)
& "$env:LOCALAPPDATA\Alonarg\venv\Scripts\python.exe" -m pytest -m "not integration"   # fast, no hardware/model/CLI
```

Integration tests exercise real audio devices, the real Whisper model, and a real local-LLM (Ollama) call.

## Troubleshooting

- **No system audio captured:** make sure something is actually playing and the output volume
  isn't muted; loopback captures the **default** output device.
- **Summaries fail / connection refused:** ensure Ollama is running (`ollama list`) and the
  model is pulled (`ollama pull llama3.2:3b`).
- **Transcription slow:** use a smaller model (`ALONARG_MODEL=base`) or a GPU
  (`ALONARG_DEVICE=cuda ALONARG_COMPUTE=float16`).
- **Hotkey not working:** another app may own `Ctrl+Alt+R`; change `ALONARG_HOTKEY`.
