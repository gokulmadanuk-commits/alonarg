"""Tests for the tray control layer.

These run with NO live server, NO real tray loop, and NO real global hotkey.
HTTP is exercised through a small fake httpx-like client; connection errors are
simulated to prove the control functions are failure-tolerant.
"""
from __future__ import annotations

import httpx
import pystray
import pytest
from PIL import Image

from alonarg import config, tray


# ---------------------------------------------------------------------------
# Fake httpx-like client
# ---------------------------------------------------------------------------
class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "error", request=None, response=None
            )


class FakeClient:
    """Records calls and returns canned responses keyed by URL suffix."""

    def __init__(self, status_payload=None, raise_on=None):
        self.status_payload = status_payload or {
            "recording": False,
            "elapsed_s": 0,
            "current_id": None,
        }
        # set of path-suffixes that should raise a connection error
        self.raise_on = raise_on or set()
        self.get_calls = []
        self.post_calls = []

    def _maybe_raise(self, url):
        for suffix in self.raise_on:
            if url.endswith(suffix):
                raise httpx.ConnectError("connection refused")

    def get(self, url, *args, **kwargs):
        self.get_calls.append(url)
        self._maybe_raise(url)
        if url.endswith("/api/status"):
            return FakeResponse(self.status_payload)
        return FakeResponse({})

    def post(self, url, *args, **kwargs):
        self.post_calls.append(url)
        self._maybe_raise(url)
        if url.endswith("/api/record/start"):
            return FakeResponse({"id": 42})
        if url.endswith("/api/record/stop"):
            return FakeResponse({"id": 42, "status": "transcribing"})
        return FakeResponse({})


# ---------------------------------------------------------------------------
# get_status
# ---------------------------------------------------------------------------
def test_get_status_returns_parsed_dict():
    client = FakeClient(status_payload={"recording": True, "elapsed_s": 5, "current_id": 7})
    result = tray.get_status(client)
    assert result == {"recording": True, "elapsed_s": 5, "current_id": 7}
    assert any(url.endswith("/api/status") for url in client.get_calls)


def test_get_status_connection_error_returns_safe_default():
    client = FakeClient(raise_on={"/api/status"})
    result = tray.get_status(client)
    assert result == {"recording": False, "elapsed_s": 0, "current_id": None}


# ---------------------------------------------------------------------------
# toggle_recording
# ---------------------------------------------------------------------------
def test_toggle_when_recording_stops():
    client = FakeClient(status_payload={"recording": True, "elapsed_s": 3, "current_id": 1})
    result = tray.toggle_recording(client)
    assert result == "stopped"
    assert any(url.endswith("/api/record/stop") for url in client.post_calls)
    assert not any(url.endswith("/api/record/start") for url in client.post_calls)


def test_toggle_when_not_recording_starts():
    client = FakeClient(status_payload={"recording": False, "elapsed_s": 0, "current_id": None})
    result = tray.toggle_recording(client)
    assert result == "started"
    assert any(url.endswith("/api/record/start") for url in client.post_calls)
    assert not any(url.endswith("/api/record/stop") for url in client.post_calls)


# ---------------------------------------------------------------------------
# start_recording / stop_recording never raise
# ---------------------------------------------------------------------------
def test_start_recording_returns_json():
    client = FakeClient()
    result = tray.start_recording(client)
    assert result == {"id": 42}


def test_stop_recording_returns_json():
    client = FakeClient()
    result = tray.stop_recording(client)
    assert result == {"id": 42, "status": "transcribing"}


def test_start_recording_connection_error_returns_empty():
    client = FakeClient(raise_on={"/api/record/start"})
    result = tray.start_recording(client)
    assert result == {}


def test_stop_recording_connection_error_returns_empty():
    client = FakeClient(raise_on={"/api/record/stop"})
    result = tray.stop_recording(client)
    assert result == {}


def test_toggle_never_raises_on_total_failure():
    client = FakeClient(raise_on={"/api/status", "/api/record/start", "/api/record/stop"})
    # status fails -> safe default (not recording) -> tries start (also fails) -> "started"
    result = tray.toggle_recording(client)
    assert result == "started"


# ---------------------------------------------------------------------------
# AppController
# ---------------------------------------------------------------------------
def test_controller_toggle_ignores_extra_args():
    client = FakeClient(status_payload={"recording": False, "elapsed_s": 0, "current_id": None})
    controller = tray.AppController(client=client)
    # pystray/keyboard pass (icon, item) -- must be tolerated
    result = controller.toggle_recording(object(), object())
    assert result == "started"


def test_controller_uses_injected_client():
    client = FakeClient(status_payload={"recording": True, "elapsed_s": 1, "current_id": 9})
    controller = tray.AppController(client=client)
    assert controller.get_status()["recording"] is True


# ---------------------------------------------------------------------------
# Icon image
# ---------------------------------------------------------------------------
def test_make_icon_image_returns_image_with_positive_size():
    img = tray.make_icon_image()
    assert isinstance(img, Image.Image)
    w, h = img.size
    assert w > 0 and h > 0


# ---------------------------------------------------------------------------
# Menu (built without running the tray loop)
# ---------------------------------------------------------------------------
def test_build_menu_has_at_least_three_items():
    controller = tray.AppController(client=FakeClient())
    menu = tray.build_menu(controller)
    assert isinstance(menu, pystray.Menu)
    items = list(menu)
    # at least Start/Stop, Open Dashboard, Quit (separators may add more)
    labeled = [it for it in items if getattr(it, "text", None)]
    assert len(labeled) >= 3


# ---------------------------------------------------------------------------
# register_hotkey (monkeypatched keyboard backend; never crashes)
# ---------------------------------------------------------------------------
def test_register_hotkey_calls_keyboard(monkeypatch):
    import keyboard

    calls = []

    def fake_add_hotkey(hotkey, callback):
        calls.append((hotkey, callback))

    monkeypatch.setattr(keyboard, "add_hotkey", fake_add_hotkey)
    controller = tray.AppController(client=FakeClient())
    tray.register_hotkey(controller, hotkey="ctrl+alt+x")

    assert len(calls) == 1
    assert calls[0][0] == "ctrl+alt+x"
    assert calls[0][1] == controller.toggle_recording


def test_register_hotkey_default_uses_config(monkeypatch):
    import keyboard

    calls = []
    monkeypatch.setattr(keyboard, "add_hotkey", lambda hk, cb: calls.append(hk))
    controller = tray.AppController(client=FakeClient())
    tray.register_hotkey(controller)
    assert calls == [config.HOTKEY]


def test_register_hotkey_swallows_errors(monkeypatch):
    import keyboard

    def boom(hotkey, callback):
        raise RuntimeError("no keyboard backend")

    monkeypatch.setattr(keyboard, "add_hotkey", boom)
    controller = tray.AppController(client=FakeClient())
    # Must not raise.
    tray.register_hotkey(controller, hotkey="ctrl+alt+y")
