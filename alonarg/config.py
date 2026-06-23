"""Configuration and environment resolution for Alonarg.

All paths default to ``%LOCALAPPDATA%\\Alonarg`` so large audio files and the
model cache live OUTSIDE any OneDrive-synced source tree. Everything is
overridable via environment variables (``ALONARG_*``).
"""
from __future__ import annotations

import glob
import os
import re
import shutil
from pathlib import Path


def _home() -> Path:
    env = os.environ.get("ALONARG_HOME")
    if env:
        return Path(env)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "Alonarg"


HOME = _home()
DATA_DIR = HOME
DB_PATH = Path(os.environ.get("ALONARG_DB") or (HOME / "alonarg.db"))
RECORDINGS_DIR = Path(os.environ.get("ALONARG_RECORDINGS") or (HOME / "recordings"))
MODELS_DIR = Path(os.environ.get("ALONARG_MODELS") or (HOME / "models"))

# --- Audio ---
SAMPLE_RATE = 16000        # Whisper-native rate; both tracks are resampled to this
CHANNELS = 1

# --- Whisper (faster-whisper) ---
WHISPER_MODEL = os.environ.get("ALONARG_MODEL", "small")
# Default to CPU/int8 for maximum reliability (no CUDA/cuDNN dependency).
# Set ALONARG_DEVICE=cuda + ALONARG_COMPUTE=float16 to use a GPU.
WHISPER_DEVICE = os.environ.get("ALONARG_DEVICE", "cpu")
WHISPER_COMPUTE = os.environ.get("ALONARG_COMPUTE", "int8")

# --- Claude (headless CLI, uses the user's plan, NOT the API) ---
CLAUDE_MODEL = os.environ.get("ALONARG_CLAUDE_MODEL", "")   # "" => CLI default
CLAUDE_TIMEOUT = int(os.environ.get("ALONARG_CLAUDE_TIMEOUT", "300"))

# --- Server ---
SERVER_HOST = os.environ.get("ALONARG_HOST", "127.0.0.1")
SERVER_PORT = int(os.environ.get("ALONARG_PORT", "8765"))

# --- Hotkey ---
HOTKEY = os.environ.get("ALONARG_HOTKEY", "ctrl+alt+r")

# --- Auth / CORS (for the phone PWA upload endpoint) ---
# Shared secret protecting the API. Empty string => auth disabled (open, the
# current desktop-only behavior).
ALONARG_TOKEN = os.environ.get("ALONARG_TOKEN", "")
# Comma-separated list of allowed CORS origins, or "*" for any.
ALONARG_CORS_ORIGINS = os.environ.get("ALONARG_CORS_ORIGINS", "*")


def base_url() -> str:
    return f"http://{SERVER_HOST}:{SERVER_PORT}"


def cors_origins() -> list[str]:
    """Parse ``ALONARG_CORS_ORIGINS`` into a list for CORSMiddleware.

    Returns ``["*"]`` when configured as ``"*"``; otherwise a list of the
    comma-separated origins (stripped, empties dropped).
    """
    raw = (ALONARG_CORS_ORIGINS or "").strip()
    if raw == "*" or raw == "":
        return ["*"]
    return [o.strip() for o in raw.split(",") if o.strip()]


def ensure_dirs() -> None:
    """Create the data directories and point the HF cache at our models dir."""
    for p in (DATA_DIR, RECORDINGS_DIR, MODELS_DIR):
        Path(p).mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(MODELS_DIR))


def _version_key(path: str) -> tuple[int, int, int]:
    matches = re.findall(r"(\d+)\.(\d+)\.(\d+)", path)
    if not matches:
        return (0, 0, 0)
    return tuple(int(x) for x in matches[-1])  # type: ignore[return-value]


def resolve_claude_binary() -> str:
    """Locate the Claude Code CLI executable.

    Resolution order: ALONARG_CLAUDE_BIN env -> PATH -> known install globs
    (newest version wins). Raises FileNotFoundError if none found.
    """
    env = os.environ.get("ALONARG_CLAUDE_BIN")
    if env and Path(env).exists():
        return env

    which = shutil.which("claude")
    if which:
        return which

    home = Path.home()
    localappdata = os.environ.get("LOCALAPPDATA", str(home / "AppData" / "Local"))
    appdata = os.environ.get("APPDATA", str(home / "AppData" / "Roaming"))
    patterns = [
        str(home / ".vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude.exe"),
        str(Path(localappdata) / "Packages/Claude_*/LocalCache/Roaming/Claude/claude-code/*/claude.exe"),
        str(Path(localappdata) / "Programs/claude/claude.exe"),
        str(Path(appdata) / "npm/claude.cmd"),
    ]
    candidates: list[str] = []
    for pat in patterns:
        candidates.extend(glob.glob(pat))
    candidates = [c for c in candidates if Path(c).exists()]
    if not candidates:
        raise FileNotFoundError(
            "Could not locate the 'claude' CLI. Install Claude Code or set "
            "ALONARG_CLAUDE_BIN to the full path of claude.exe."
        )
    candidates.sort(key=_version_key, reverse=True)
    return candidates[0]
