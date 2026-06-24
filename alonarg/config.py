"""Configuration and environment resolution for Alonarg.

All paths default to ``%LOCALAPPDATA%\\Alonarg`` so large audio files and the
model cache live OUTSIDE any OneDrive-synced source tree. Everything is
overridable via environment variables (``ALONARG_*``).
"""
from __future__ import annotations

import os
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

# --- Ollama (local LLM via HTTP API; no API key, no subscription, offline) ---
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")
OLLAMA_NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX", "8192"))
OLLAMA_TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", "300"))

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
