"""Tests for alonarg.summarize.

Unit tests are hermetic (no network) and monkeypatch ``httpx.post``. A single
integration test makes one real call to a local Ollama server and is marked
``@pytest.mark.integration``; it skips cleanly when the endpoint/model is not
available.
"""
from __future__ import annotations

import json

import httpx
import pytest

from alonarg import config, summarize
from alonarg.types import SummaryResult


# --------------------------------------------------------------------------- #
# extract_json
# --------------------------------------------------------------------------- #

def test_extract_json_raw_object():
    text = '{"title": "T", "summary": "S", "action_items": [], "next_steps": []}'
    assert summarize.extract_json(text) == {
        "title": "T",
        "summary": "S",
        "action_items": [],
        "next_steps": [],
    }


def test_extract_json_fenced_json():
    text = '```json\n{"title": "Hello", "summary": "world"}\n```'
    assert summarize.extract_json(text) == {"title": "Hello", "summary": "world"}


def test_extract_json_fenced_plain():
    text = '```\n{"a": 1, "b": 2}\n```'
    assert summarize.extract_json(text) == {"a": 1, "b": 2}


def test_extract_json_embedded_in_prose():
    text = (
        "Sure! Here is the summary you asked for:\n"
        '{"title": "Standup", "action_items": ["ship"]}\n'
        "Let me know if you need anything else."
    )
    assert summarize.extract_json(text) == {
        "title": "Standup",
        "action_items": ["ship"],
    }


def test_extract_json_nested_braces():
    text = 'noise {"outer": {"inner": {"deep": true}}, "n": 1} trailing'
    assert summarize.extract_json(text) == {
        "outer": {"inner": {"deep": True}},
        "n": 1,
    }


def test_extract_json_braces_inside_strings():
    # Braces inside string literals must not confuse the balanced-brace scan.
    text = '{"summary": "we discussed {scope} and } edge cases", "ok": true}'
    assert summarize.extract_json(text) == {
        "summary": "we discussed {scope} and } edge cases",
        "ok": True,
    }


def test_extract_json_escaped_quote_inside_string():
    text = r'{"summary": "she said \"hi\" then left {", "ok": 1}'
    assert summarize.extract_json(text) == {
        "summary": 'she said "hi" then left {',
        "ok": 1,
    }


def test_extract_json_garbage_raises():
    with pytest.raises(ValueError):
        summarize.extract_json("this is not json at all, no braces here")


def test_extract_json_unbalanced_raises():
    with pytest.raises(ValueError):
        summarize.extract_json('{"title": "oops"')


# --------------------------------------------------------------------------- #
# parse_summary
# --------------------------------------------------------------------------- #

def _response(content: str) -> dict:
    """Build a minimal Ollama /api/chat response with ``content`` as the answer."""
    return {"message": {"role": "assistant", "content": content}}


def test_parse_summary_success_response():
    inner = {
        "title": "Weekly Sync",
        "summary": "The team agreed to ship the dashboard.",
        "action_items": ["Email the client", "Finish the dashboard"],
        "next_steps": ["Review on Monday"],
    }
    response = _response(json.dumps(inner))
    result = summarize.parse_summary(response)
    assert isinstance(result, SummaryResult)
    assert result.title == "Weekly Sync"
    assert result.summary == "The team agreed to ship the dashboard."
    assert result.action_items == ["Email the client", "Finish the dashboard"]
    assert result.next_steps == ["Review on Monday"]


def test_parse_summary_fenced_content():
    inner = '```json\n{"title": "Fenced", "summary": "ok", "action_items": [], "next_steps": []}\n```'
    response = _response(inner)
    result = summarize.parse_summary(response)
    assert result.title == "Fenced"
    assert result.summary == "ok"
    assert result.action_items == []
    assert result.next_steps == []


def test_parse_summary_missing_message_raises():
    with pytest.raises((ValueError, KeyError)):
        summarize.parse_summary({"done": True})


# --------------------------------------------------------------------------- #
# summarize (httpx.post monkeypatched)
# --------------------------------------------------------------------------- #

class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self) -> dict:
        return self._payload


def test_summarize_success(monkeypatch):
    inner = {
        "title": "Kickoff",
        "summary": "We planned the project.",
        "action_items": ["Draft spec"],
        "next_steps": ["Schedule review"],
    }
    response_payload = _response(json.dumps(inner))

    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return _FakeResponse(status_code=200, payload=response_payload)

    monkeypatch.setattr(summarize.httpx, "post", fake_post)

    result = summarize.summarize("You: hi. Others: hello.")

    assert isinstance(result, SummaryResult)
    assert result.title == "Kickoff"
    assert result.summary == "We planned the project."
    assert result.action_items == ["Draft spec"]
    assert result.next_steps == ["Schedule review"]

    # Posted to the /api/chat endpoint.
    assert captured["url"].endswith("/api/chat")
    payload = captured["kwargs"]["json"]
    assert payload["format"] == "json"
    assert payload["model"] == config.OLLAMA_MODEL
    assert payload["stream"] is False
    # system + user messages; transcript carried in the user message.
    roles = [m["role"] for m in payload["messages"]]
    assert roles == ["system", "user"]
    assert "You: hi. Others: hello." in payload["messages"][1]["content"]


def test_summarize_passes_model_and_host(monkeypatch):
    response_payload = _response(
        '{"title": "x", "summary": "y", "action_items": [], "next_steps": []}'
    )
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return _FakeResponse(status_code=200, payload=response_payload)

    monkeypatch.setattr(summarize.httpx, "post", fake_post)

    summarize.summarize("transcript", model="mistral", host="http://example:1234/")

    # Trailing slash on host is stripped; model passed through to payload.
    assert captured["url"] == "http://example:1234/api/chat"
    assert captured["kwargs"]["json"]["model"] == "mistral"


def test_summarize_connection_error_raises(monkeypatch):
    def fake_post(url, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(summarize.httpx, "post", fake_post)

    with pytest.raises(RuntimeError) as exc:
        summarize.summarize("transcript")
    msg = str(exc.value)
    assert "Ollama" in msg
    assert "ollama pull" in msg


def test_summarize_non_200_raises(monkeypatch):
    def fake_post(url, **kwargs):
        return _FakeResponse(status_code=404, text='{"error":"model not found"}')

    monkeypatch.setattr(summarize.httpx, "post", fake_post)

    with pytest.raises(RuntimeError) as exc:
        summarize.summarize("transcript")
    msg = str(exc.value)
    assert "404" in msg
    assert "model not found" in msg


# --------------------------------------------------------------------------- #
# integration: one real call to a local Ollama server
# --------------------------------------------------------------------------- #

def _ollama_has_model() -> bool:
    try:
        import httpx
        import alonarg.config as c

        r = httpx.get(c.OLLAMA_HOST.rstrip("/") + "/api/tags", timeout=3)
        return r.status_code == 200 and (
            c.OLLAMA_MODEL in r.text or c.OLLAMA_MODEL.split(":")[0] in r.text
        )
    except Exception:
        return False


@pytest.mark.integration
@pytest.mark.skipif(not _ollama_has_model(), reason="Ollama model not available")
def test_summarize_integration_real_ollama():
    s = summarize.summarize(
        "You: We ship the dashboard Friday. "
        "Others: I'll email the client the timeline today."
    )
    assert s.title
    assert s.summary
    assert isinstance(s.action_items, list)
    assert isinstance(s.next_steps, list)
