"""Shared pytest fixtures and helpers for Alonarg tests.

Includes a Windows SAPI text-to-speech helper so transcription can be verified
end-to-end locally (no cloud service) with a known phrase.
"""
from __future__ import annotations

import os
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

# Make the project importable when tests run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def _no_embeddings(monkeypatch):
    """Keep tests hermetic: the semantic 'brain' is off unless a test opts in.

    Otherwise search/ask endpoints would call the local Ollama embeddings server.
    """
    from alonarg import brain, embeddings
    monkeypatch.setattr(embeddings, "available", lambda: False)
    monkeypatch.setattr(brain, "available", lambda: False)


@pytest.fixture()
def tmp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture()
def sine_wav(tmp_path: Path):
    """Factory: write a mono 16k PCM16 sine wav and return its path."""
    def _make(name: str = "tone.wav", seconds: float = 1.0, freq: float = 220.0,
              rate: int = 16000) -> str:
        t = np.linspace(0, seconds, int(rate * seconds), endpoint=False)
        data = (0.3 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
        pcm = (np.clip(data, -1, 1) * 32767).astype("<i2")
        path = tmp_path / name
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm.tobytes())
        return str(path)
    return _make


def make_speech_wav(text: str, path: str | Path, rate: int = -2) -> str:
    """Synthesize `text` to a WAV file using Windows SAPI (System.Speech).

    `rate` is the SAPI speaking rate (-10..10); slower (-2) transcribes more reliably.
    Returns the path. Raises if PowerShell/SAPI is unavailable (caller should skip).
    """
    path = str(path)
    safe = text.replace('"', '`"')
    ps = (
        "Add-Type -AssemblyName System.Speech;"
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        f"$s.Rate = {rate};"
        f'$s.SetOutputToWaveFile("{path}");'
        f'$s.Speak("{safe}");'
        "$s.Dispose()"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        check=True, capture_output=True, timeout=60,
    )
    return path


@pytest.fixture()
def speech_wav(tmp_path: Path):
    """Factory fixture returning make_speech_wav bound to tmp_path; skips if SAPI fails."""
    def _make(text: str, name: str = "speech.wav") -> str:
        target = tmp_path / name
        try:
            return make_speech_wav(text, target)
        except Exception as exc:  # pragma: no cover - environment dependent
            pytest.skip(f"Windows SAPI TTS unavailable: {exc}")
    return _make
