"""Summarize a meeting transcript by driving the Claude Code CLI headlessly.

This module shells out to the ``claude`` CLI in headless mode
(``claude -p ... --output-format json --max-turns 1``) feeding the transcript on
stdin. The CLI uses the user's Claude plan (NOT the paid API). The stdout is a
JSON envelope whose ``result`` field holds the model's answer, which we instruct
the model to make a single JSON object.

Pure helpers (``build_prompt``, ``extract_json``, ``parse_summary``) are split out
so they can be unit-tested without any subprocess/network.
"""
from __future__ import annotations

import json
import subprocess

from alonarg import config
from alonarg.types import SummaryResult


def build_prompt() -> str:
    """Instruction for the headless model.

    Tells the model a meeting transcript will arrive on stdin and that it must
    respond with ONLY a single JSON object (no markdown fences, no prose).
    """
    return (
        "You are a meeting-notes assistant. A meeting transcript will be provided "
        "on standard input (stdin). Read the entire transcript and produce a "
        "summary of the meeting.\n\n"
        "Respond with ONLY a single JSON object and nothing else. Do not wrap it "
        "in markdown code fences. Do not add any explanation, preamble, or trailing "
        "text. The JSON object must have exactly these keys:\n"
        '  "title": a short string naming the meeting.\n'
        '  "summary": a concise paragraph summarizing the meeting.\n'
        '  "action_items": an array of strings, each a concrete action item '
        "(empty array if none).\n"
        '  "next_steps": an array of strings describing follow-up next steps '
        "(empty array if none).\n\n"
        "Output the raw JSON object only."
    )


def extract_json(text: str) -> dict:
    """Robustly extract a single JSON object from ``text``.

    Handles: (a) raw JSON, (b) ```json ... ``` (or plain ``` ... ```) fenced
    blocks, (c) JSON embedded in surrounding prose. Strategy: strip code fences
    if present, then scan for the first ``{`` and find its matching closing
    ``}`` via a balanced-brace scan that respects strings and escapes, and
    ``json.loads`` that substring.

    Pure: performs no I/O. Raises ``ValueError`` if nothing parses.
    """
    if text is None:
        raise ValueError("no JSON object found in empty text")

    candidate = _strip_code_fences(text)

    # First, try the whole (de-fenced) candidate as raw JSON.
    stripped = candidate.strip()
    if stripped:
        try:
            obj = json.loads(stripped)
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            pass

    # Otherwise scan for the first balanced {...} and parse it.
    span = _find_balanced_object(candidate)
    if span is not None:
        try:
            obj = json.loads(span)
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, ValueError):
            pass

    # Fall back to scanning the original text (in case fence-stripping mangled it).
    if candidate != text:
        span = _find_balanced_object(text)
        if span is not None:
            try:
                obj = json.loads(span)
                if isinstance(obj, dict):
                    return obj
            except (json.JSONDecodeError, ValueError):
                pass

    raise ValueError("no JSON object could be extracted from text")


def _strip_code_fences(text: str) -> str:
    """Return the contents of the first ```...``` fenced block, or ``text``.

    Recognizes an optional language tag (e.g. ```json) on the opening fence.
    """
    lines = text.splitlines()
    in_fence = False
    collected: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not in_fence:
            if stripped.startswith("```"):
                in_fence = True
                continue
        else:
            if stripped.startswith("```"):
                return "\n".join(collected)
            collected.append(line)
    # No matching closing fence found; return text unchanged.
    return text


def _find_balanced_object(text: str) -> str | None:
    """Return the substring of the first balanced ``{...}`` object, respecting
    string literals and escapes. ``None`` if no balanced object is found."""
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def parse_summary(claude_stdout: str) -> SummaryResult:
    """Parse the Claude CLI JSON envelope into a ``SummaryResult``.

    Loads the envelope, raises ``RuntimeError`` if ``is_error`` is true, takes
    the ``result`` field, extracts the JSON object from it, and builds the
    ``SummaryResult``. Pure: no subprocess.
    """
    envelope = json.loads(claude_stdout)
    if envelope.get("is_error"):
        raise RuntimeError(envelope.get("result") or "claude error")
    result_text = envelope["result"]
    data = extract_json(result_text)
    return SummaryResult.from_dict(data)


def summarize(
    transcript_text: str,
    claude_bin: str | None = None,
    model: str | None = None,
    timeout: int = config.CLAUDE_TIMEOUT,
) -> SummaryResult:
    """Summarize ``transcript_text`` via the headless Claude CLI.

    Resolves the CLI binary, runs it with the transcript piped on stdin, and
    parses the resulting envelope into a ``SummaryResult``. Raises
    ``RuntimeError`` on non-zero exit or an error envelope.
    """
    bin = claude_bin or config.resolve_claude_binary()
    model = model if model is not None else (config.CLAUDE_MODEL or None)

    argv = [bin, "-p", build_prompt(), "--output-format", "json", "--max-turns", "1"]
    if model:
        argv.extend(["--model", model])

    result = subprocess.run(
        argv,
        input=transcript_text,
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"claude CLI exited with code {result.returncode}: "
            f"{(result.stderr or '').strip()}"
        )
    return parse_summary(result.stdout)
