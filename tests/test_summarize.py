"""Tests for alonarg.summarize.

Unit tests are hermetic (no network/subprocess) and monkeypatch
``subprocess.run``. A single integration test makes one real call to the
``claude`` CLI and is marked ``@pytest.mark.integration``.
"""
from __future__ import annotations

import json

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

def _envelope(result_text: str, is_error: bool = False) -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": is_error,
            "result": result_text,
        }
    )


def test_parse_summary_success_envelope():
    inner = {
        "title": "Weekly Sync",
        "summary": "The team agreed to ship the dashboard.",
        "action_items": ["Email the client", "Finish the dashboard"],
        "next_steps": ["Review on Monday"],
    }
    stdout = _envelope(json.dumps(inner))
    result = summarize.parse_summary(stdout)
    assert isinstance(result, SummaryResult)
    assert result.title == "Weekly Sync"
    assert result.summary == "The team agreed to ship the dashboard."
    assert result.action_items == ["Email the client", "Finish the dashboard"]
    assert result.next_steps == ["Review on Monday"]


def test_parse_summary_fenced_result():
    inner = '```json\n{"title": "Fenced", "summary": "ok", "action_items": [], "next_steps": []}\n```'
    stdout = _envelope(inner)
    result = summarize.parse_summary(stdout)
    assert result.title == "Fenced"
    assert result.summary == "ok"
    assert result.action_items == []
    assert result.next_steps == []


def test_parse_summary_error_envelope_raises():
    stdout = _envelope("something went wrong", is_error=True)
    with pytest.raises(RuntimeError) as exc:
        summarize.parse_summary(stdout)
    assert "something went wrong" in str(exc.value)


def test_parse_summary_error_envelope_no_result_message():
    stdout = json.dumps({"type": "result", "is_error": True, "result": ""})
    with pytest.raises(RuntimeError) as exc:
        summarize.parse_summary(stdout)
    assert "claude error" in str(exc.value)


# --------------------------------------------------------------------------- #
# summarize (subprocess monkeypatched)
# --------------------------------------------------------------------------- #

class _FakeCompleted:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_summarize_success(monkeypatch):
    inner = {
        "title": "Kickoff",
        "summary": "We planned the project.",
        "action_items": ["Draft spec"],
        "next_steps": ["Schedule review"],
    }
    stdout = _envelope(json.dumps(inner))

    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return _FakeCompleted(returncode=0, stdout=stdout)

    monkeypatch.setattr(summarize.subprocess, "run", fake_run)
    monkeypatch.setattr(config, "resolve_claude_binary", lambda: "C:/dummy/claude.exe")

    result = summarize.summarize("You: hi. Others: hello.")

    assert isinstance(result, SummaryResult)
    assert result.title == "Kickoff"
    assert result.summary == "We planned the project."
    assert result.action_items == ["Draft spec"]
    assert result.next_steps == ["Schedule review"]

    argv = captured["argv"]
    assert argv[0] == "C:/dummy/claude.exe"
    assert "-p" in argv
    assert "--output-format" in argv
    # json must immediately follow --output-format
    assert argv[argv.index("--output-format") + 1] == "json"
    assert "--max-turns" in argv
    # transcript is fed on stdin, not as an argument
    assert captured["kwargs"]["input"] == "You: hi. Others: hello."


def test_summarize_passes_model(monkeypatch):
    stdout = _envelope('{"title": "x", "summary": "y", "action_items": [], "next_steps": []}')

    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return _FakeCompleted(returncode=0, stdout=stdout)

    monkeypatch.setattr(summarize.subprocess, "run", fake_run)
    monkeypatch.setattr(config, "resolve_claude_binary", lambda: "claude")

    summarize.summarize("transcript", model="opus")
    argv = captured["argv"]
    assert "--model" in argv
    assert argv[argv.index("--model") + 1] == "opus"


def test_summarize_nonzero_returncode_raises(monkeypatch):
    def fake_run(argv, **kwargs):
        return _FakeCompleted(returncode=2, stdout="", stderr="boom: not authenticated")

    monkeypatch.setattr(summarize.subprocess, "run", fake_run)
    monkeypatch.setattr(config, "resolve_claude_binary", lambda: "claude")

    with pytest.raises(RuntimeError) as exc:
        summarize.summarize("transcript")
    assert "boom: not authenticated" in str(exc.value)


# --------------------------------------------------------------------------- #
# integration: one real call to the claude CLI
# --------------------------------------------------------------------------- #

@pytest.mark.integration
def test_summarize_integration_real_claude():
    s = summarize.summarize(
        "You: We will ship the dashboard on Friday. "
        "Others: I will email the client the timeline today."
    )
    assert s.title
    assert s.summary
    assert isinstance(s.action_items, list)
    assert isinstance(s.next_steps, list)
