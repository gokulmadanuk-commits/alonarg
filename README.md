# Alonarg

A **local** meeting recorder, transcriber, and summarizer for Windows — like Granola,
but everything runs on your PC. It captures your **microphone** and your **system audio**
(WASAPI loopback), transcribes locally with Whisper, summarizes with **Claude using your
existing plan** (via the Claude Code CLI, *not* the paid API), and shows everything in a
local dashboard with summaries, action items, next steps, full transcripts, and audio playback.

## What it does

1. **Record** — press the hotkey, click the tray icon, or hit Record in the dashboard. Mic
   and system audio are captured on **separate tracks**.
2. **Transcribe** — locally, with [faster-whisper](https://github.com/SYSTRAN/faster-whisper).
   Your mic is labeled **You**, system audio is labeled **Others**.
3. **Summarize** — the transcript is sent to Claude headlessly (`claude -p`), which returns a
   title, a concise summary, **action items**, and **next steps**. This uses your Claude
   subscription, not API credits.
4. **Review** — a dashboard lists every recording with its summary and action items; open one
   to read the full transcript and play back the audio.

## Requirements

- Windows 10/11
- Python 3.14 (the project was built and tested on 3.14.5)
- [Claude Code](https://claude.com/claude-code) installed and signed in (the `claude` CLI;
  Alonarg auto-locates it, including the VS Code extension's bundled binary)
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

## How it works

```
 mic ─┐                              ┌─ "You"  segments ─┐
      ├─ audio_capture (WASAPI) ─►   │                   ├─ transcribe (faster-whisper)
 sys ─┘   mic.wav / system.wav       └─ "Others" segments┘            │
                                                                      ▼
                          mixed.wav (playback)              TranscriptResult
                                                                      │
                                                                      ▼
                                              summarize  →  claude -p (your plan)
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
| `ALONARG_CLAUDE_BIN` | auto | full path to `claude.exe` if auto-detect fails |
| `ALONARG_CLAUDE_MODEL` | CLI default | e.g. `claude-sonnet-4-6` to pick a model for summaries |
| `ALONARG_HOTKEY` | `ctrl+alt+r` | global record toggle |
| `ALONARG_HOST` / `ALONARG_PORT` | `127.0.0.1` / `8765` | dashboard address |

## Where your data lives

`%LOCALAPPDATA%\Alonarg\` — `alonarg.db` (metadata, transcripts, summaries),
`recordings\<id>\` (`mic.wav`, `system.wav`, `mixed.wav`), and `models\` (Whisper cache).
Nothing leaves your machine except the transcript text sent to Claude for summarization.

## Tests

```powershell
& "$env:LOCALAPPDATA\Alonarg\venv\Scripts\python.exe" -m pytest            # everything (89 tests)
& "$env:LOCALAPPDATA\Alonarg\venv\Scripts\python.exe" -m pytest -m "not integration"   # fast, no hardware/model/CLI
```

Integration tests exercise real audio devices, the real Whisper model, and a real `claude` call.

## Troubleshooting

- **No system audio captured:** make sure something is actually playing and the output volume
  isn't muted; loopback captures the **default** output device.
- **`claude` not found:** set `ALONARG_CLAUDE_BIN` to your `claude.exe`.
- **Transcription slow:** use a smaller model (`ALONARG_MODEL=base`) or a GPU
  (`ALONARG_DEVICE=cuda ALONARG_COMPUTE=float16`).
- **Hotkey not working:** another app may own `Ctrl+Alt+R`; change `ALONARG_HOTKEY`.
