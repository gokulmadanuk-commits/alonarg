"""Entry point: ``python -m alonarg``.

Launches the whole local app:
  1. Ensure data dirs exist.
  2. Start the FastAPI server (uvicorn) on a background daemon thread.
  3. Wait until the server answers ``GET /api/status`` (so the UI is ready).
  4. Register the global hotkey.
  5. Open the dashboard in the browser.
  6. Run the system-tray icon on the MAIN thread (blocks until Quit).

Everything is failure-tolerant; the only thing that ends the app is Quit from
the tray (or Ctrl+C).
"""
from __future__ import annotations

import logging
import threading
import time
import webbrowser

from alonarg import config, tray

log = logging.getLogger(__name__)


def _start_server() -> threading.Thread:
    """Start uvicorn serving ``alonarg.server:app`` on a daemon thread."""
    import uvicorn

    cfg = uvicorn.Config(
        "alonarg.server:app",
        host=config.SERVER_HOST,
        port=config.SERVER_PORT,
        log_level="warning",
    )
    server = uvicorn.Server(cfg)

    thread = threading.Thread(target=server.run, daemon=True, name="alonarg-uvicorn")
    thread.start()
    return thread


def _wait_for_server(timeout: float = 10.0, interval: float = 0.25) -> bool:
    """Poll ``GET /api/status`` until it responds or ``timeout`` elapses."""
    import httpx

    url = f"{config.base_url()}/api/status"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(url, timeout=2.0)
            if resp.status_code == 200:
                return True
        except Exception:  # noqa: BLE001 - server not up yet
            pass
        time.sleep(interval)
    return False


def main() -> None:
    logging.basicConfig(level=logging.INFO)

    config.ensure_dirs()

    log.info("Starting Alonarg server on %s ...", config.base_url())
    _start_server()

    if _wait_for_server():
        log.info("Server is ready.")
    else:
        log.warning("Server did not respond in time; continuing anyway.")

    controller = tray.AppController()

    tray.register_hotkey(controller)

    try:
        webbrowser.open(config.base_url())
    except Exception:  # noqa: BLE001
        log.warning("Could not open dashboard in browser", exc_info=True)

    icon = tray.run_tray(controller)
    try:
        icon.run()  # blocks on the main thread until Quit
    except KeyboardInterrupt:
        log.info("Interrupted; shutting down.")
        try:
            icon.stop()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    main()
