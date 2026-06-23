"""System-tray icon + global hotkey for Alonarg.

This is the *control* layer: it does not record anything itself. It toggles
recording by calling the local FastAPI server's JSON API over HTTP (via httpx),
exposes a pystray tray menu, and registers a global hotkey (``config.HOTKEY``).

Everything here is failure-tolerant by design: a tray, hotkey, or HTTP failure
must NEVER crash the app. The HTTP layer is hidden behind a small injectable
client so it can be unit-tested without a live server.
"""
from __future__ import annotations

import logging
import webbrowser

import pystray
from PIL import Image, ImageDraw

from alonarg import config

log = logging.getLogger(__name__)

# Lazily-created module-level default httpx client (tests pass their own fake).
_default_client = None

# Safe status to fall back to whenever the server can't be reached.
_SAFE_STATUS = {"recording": False, "elapsed_s": 0, "current_id": None}


def _get_client():
    """Return (creating if needed) the module-level default httpx client."""
    global _default_client
    if _default_client is None:
        import httpx

        _default_client = httpx.Client(timeout=5.0)
    return _default_client


# ---------------------------------------------------------------------------
# HTTP control (all failure-tolerant; never raise)
# ---------------------------------------------------------------------------
def get_status(client=None) -> dict:
    """GET /api/status and return parsed JSON.

    On any connection/HTTP error, return a safe default that reports
    "not recording" so the UI stays usable while the server is down.
    """
    client = client or _get_client()
    url = f"{config.base_url()}/api/status"
    try:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.json()
    except Exception:  # noqa: BLE001 - control layer must never crash
        log.warning("get_status failed; assuming not recording", exc_info=True)
        return dict(_SAFE_STATUS)


def start_recording(client=None) -> dict:
    """POST /api/record/start and return parsed JSON ({} on error). Never raises."""
    client = client or _get_client()
    url = f"{config.base_url()}/api/record/start"
    try:
        resp = client.post(url)
        resp.raise_for_status()
        return resp.json()
    except Exception:  # noqa: BLE001
        log.warning("start_recording failed", exc_info=True)
        return {}


def stop_recording(client=None) -> dict:
    """POST /api/record/stop and return parsed JSON ({} on error). Never raises."""
    client = client or _get_client()
    url = f"{config.base_url()}/api/record/stop"
    try:
        resp = client.post(url)
        resp.raise_for_status()
        return resp.json()
    except Exception:  # noqa: BLE001
        log.warning("stop_recording failed", exc_info=True)
        return {}


def toggle_recording(client=None) -> str:
    """Toggle recording based on current server status.

    Returns ``"stopped"`` if we were recording (and asked to stop), else
    ``"started"``. Never raises.
    """
    try:
        status = get_status(client)
        if status.get("recording"):
            stop_recording(client)
            return "stopped"
        start_recording(client)
        return "started"
    except Exception:  # noqa: BLE001 - belt-and-suspenders
        log.warning("toggle_recording failed", exc_info=True)
        return "started"


def open_dashboard() -> None:
    """Open the local dashboard in the default web browser. Never raises."""
    try:
        webbrowser.open(config.base_url())
    except Exception:  # noqa: BLE001
        log.warning("open_dashboard failed", exc_info=True)


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------
class AppController:
    """Thin wrapper around the HTTP control functions with a shared client.

    Holds an optional injected httpx-like client so the tray/hotkey callbacks
    all talk to the server consistently and remain easy to test.
    """

    def __init__(self, client=None):
        self.client = client

    def get_status(self) -> dict:
        return get_status(self.client)

    def start_recording(self) -> dict:
        return start_recording(self.client)

    def stop_recording(self) -> dict:
        return stop_recording(self.client)

    def toggle_recording(self, *args, **kwargs) -> str:
        # pystray/keyboard pass extra args (icon, item); ignore them.
        return toggle_recording(self.client)

    def open_dashboard(self, *args, **kwargs) -> None:
        open_dashboard()


# ---------------------------------------------------------------------------
# Tray + icon (thin, not exercised as live loops in tests)
# ---------------------------------------------------------------------------
def make_icon_image(size: int = 64) -> Image.Image:
    """Generate a simple tray icon: a colored circle with a centered dot.

    Pure and testable -- returns a PIL Image, draws nothing on screen.
    """
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    pad = max(1, size // 8)
    # Outer ring
    draw.ellipse(
        (pad, pad, size - pad, size - pad),
        fill=(33, 150, 243, 255),
        outline=(255, 255, 255, 255),
        width=max(1, size // 16),
    )
    # Inner record dot
    inner = size // 4
    cx = cy = size // 2
    draw.ellipse(
        (cx - inner // 2, cy - inner // 2, cx + inner // 2, cy + inner // 2),
        fill=(244, 67, 54, 255),
    )
    return image


def build_menu(controller) -> pystray.Menu:
    """Build the tray menu WITHOUT starting the tray loop.

    Items: Start/Stop Recording, Open Dashboard, Quit.
    """
    def _quit(icon, item):  # noqa: ANN001
        try:
            icon.stop()
        except Exception:  # noqa: BLE001
            log.warning("Failed to stop tray icon", exc_info=True)

    return pystray.Menu(
        pystray.MenuItem("Start/Stop Recording", lambda icon, item: controller.toggle_recording()),
        pystray.MenuItem("Open Dashboard", lambda icon, item: controller.open_dashboard()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", _quit),
    )


def run_tray(controller) -> pystray.Icon:
    """Create the tray Icon (menu + image) and return it.

    The caller is responsible for calling ``.run()`` (which blocks). Returning
    the icon keeps this function side-effect-light and testable in isolation.
    """
    icon = pystray.Icon(
        "alonarg",
        icon=make_icon_image(),
        title="Alonarg",
        menu=build_menu(controller),
    )
    return icon


def register_hotkey(controller, hotkey: str = None) -> None:
    """Register the global hotkey to toggle recording.

    Wrapped in try/except: if the hotkey can't be registered (e.g. permissions,
    no keyboard backend), log a warning and carry on -- never crash the app.
    """
    if hotkey is None:
        hotkey = config.HOTKEY
    try:
        import keyboard

        keyboard.add_hotkey(hotkey, controller.toggle_recording)
        log.info("Registered global hotkey: %s", hotkey)
    except Exception:  # noqa: BLE001
        log.warning("Could not register hotkey %r; continuing without it", hotkey, exc_info=True)
