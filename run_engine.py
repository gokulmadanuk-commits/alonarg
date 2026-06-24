"""Headless Alonarg engine launcher for background / auto-start.

Runs cleanly under pythonw.exe (no console window) by redirecting output to a log
file before uvicorn configures logging. Ensures the data directories exist but does
NOT override the Whisper model cache (so the already-downloaded model is reused).
"""
import os
import sys
from pathlib import Path


def main() -> None:
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Alonarg"
    base.mkdir(parents=True, exist_ok=True)
    log = open(base / "engine.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = log
    sys.stderr = log

    repo = Path(__file__).resolve().parent
    sys.path.insert(0, str(repo))

    from alonarg import config
    Path(config.DATA_DIR).mkdir(parents=True, exist_ok=True)
    Path(config.RECORDINGS_DIR).mkdir(parents=True, exist_ok=True)

    import uvicorn
    from alonarg import server
    server.start_nudge_scheduler(server.app)  # meeting nudges (web push + banner state)
    uvicorn.run(
        server.app,
        host=config.SERVER_HOST,
        port=config.SERVER_PORT,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
