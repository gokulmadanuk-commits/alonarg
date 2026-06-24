"""Unit tests for the local-LLM assistant (Ollama), with httpx mocked."""
from __future__ import annotations

import httpx
import pytest

from alonarg import assistant


class _Resp:
    def __init__(self, content, status=200):
        self._content = content
        self.status_code = status
        self.text = content if isinstance(content, str) else ""

    def json(self):
        return {"message": {"content": self._content}}


def test_ask_returns_text(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        assert url.endswith("/api/chat")
        assert "QUESTION" in json["messages"][-1]["content"]
        return _Resp("The deadline is Friday.")

    monkeypatch.setattr(assistant.httpx, "post", fake_post)
    out = assistant.ask("When is the deadline?", "Meeting: deadline is Friday.")
    assert out == "The deadline is Friday."


def test_ask_connection_error(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(assistant.httpx, "post", boom)
    with pytest.raises(RuntimeError) as e:
        assistant.ask("q", "ctx")
    assert "Ollama" in str(e.value)


def test_draft_email_parses_json(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        assert json.get("format") == "json"
        return _Resp('{"subject": "Project timeline", "body": "Hi,\\n\\nHere is the timeline.\\n\\nThanks"}')

    monkeypatch.setattr(assistant.httpx, "post", fake_post)
    d = assistant.draft_email("Email the client the timeline", "Sync about timeline")
    assert d["subject"] == "Project timeline"
    assert "timeline" in d["body"].lower()


def test_draft_email_error_status(monkeypatch):
    monkeypatch.setattr(assistant.httpx, "post", lambda *a, **k: _Resp("nope", status=500))
    with pytest.raises(RuntimeError):
        assistant.draft_email("do thing")
