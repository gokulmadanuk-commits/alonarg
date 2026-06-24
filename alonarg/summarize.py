"""Summarize a meeting transcript with a small local LLM via Ollama's HTTP API.

This module POSTs the transcript to a locally running Ollama server
(``{OLLAMA_HOST}/api/chat``) with ``format:"json"`` so the model is forced to
return a single JSON object. The model's answer lives in
``response["message"]["content"]`` and is itself the JSON object we asked for.
No API key, no subscription, fully offline.

Pure helpers (``build_prompt``, ``extract_json``, ``parse_summary``) are split
out so they can be unit-tested without any network call.
"""
from __future__ import annotations

import json

import httpx

from alonarg import config
from alonarg.types import SummaryResult


def build_prompt(transcript_text: str) -> tuple[str, str]:
    """Build the (system_prompt, user_prompt) pair for the chat request.

    The system prompt instructs the model to summarize meeting transcripts and
    to output ONLY a JSON object with exactly four keys. The user prompt carries
    the transcript itself.
    """
    system_prompt = (
        "You are a meeting-notes assistant. You summarize meeting transcripts. "
        "Respond with ONLY a single JSON object and nothing else -- no markdown "
        "code fences, no explanation, no preamble or trailing text. The JSON "
        "object must have exactly these keys:\n"
        '  "title": a short string naming the meeting.\n'
        '  "summary": a concise paragraph summarizing the meeting.\n'
        '  "action_items": an array of strings, each a concrete action item '
        "(empty array if none).\n"
        '  "next_steps": an array of strings describing follow-up next steps '
        "(empty array if none)."
    )
    user_prompt = f"Transcript:\n{transcript_text}"
    return system_prompt, user_prompt


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


def parse_summary(response: dict) -> SummaryResult:
    """Parse an Ollama ``/api/chat`` response dict into a ``SummaryResult``.

    Reads ``response["message"]["content"]`` (the model's answer), extracts the
    JSON object from it, and builds the ``SummaryResult``. Pure: no network.
    Raises a clear error if the response shape is missing the expected fields.
    """
    try:
        content = response["message"]["content"]
    except (KeyError, TypeError) as exc:
        raise ValueError(
            "Ollama response missing 'message.content'; got: "
            f"{response!r}"
        ) from exc
    data = extract_json(content)
    return SummaryResult.from_dict(data)


def summarize(
    transcript_text: str,
    model: str | None = None,
    host: str | None = None,
    timeout: int = config.OLLAMA_TIMEOUT,
) -> SummaryResult:
    """Summarize ``transcript_text`` via a local Ollama model.

    POSTs a chat request to ``{host}/api/chat`` with ``format:"json"`` so Ollama
    returns valid JSON, then parses the response into a ``SummaryResult``.

    Raises ``RuntimeError`` if Ollama is unreachable (with a hint to start it /
    pull the model) or if it returns a non-200 status (including the response
    body, which surfaces model-not-found and similar hints).
    """
    host = (host or config.OLLAMA_HOST).rstrip("/")
    model = model or config.OLLAMA_MODEL

    system_prompt, user_prompt = build_prompt(transcript_text)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.2, "num_ctx": config.OLLAMA_NUM_CTX},
    }

    url = f"{host}/api/chat"
    try:
        resp = httpx.post(url, json=payload, timeout=timeout)
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        raise RuntimeError(
            f"Cannot reach Ollama at {host}. Start Ollama and run: "
            f"ollama pull {model}"
        ) from exc

    if resp.status_code != 200:
        body = (resp.text or "").strip()
        raise RuntimeError(
            f"Ollama returned HTTP {resp.status_code} from {url}: {body} "
            f"(is the model '{model}' pulled? try: ollama pull {model})"
        )

    return parse_summary(resp.json())
