"""Local-LLM helpers built on Ollama: Q&A over meetings and email drafting.

Both reuse the same local model as the summarizer (``config.OLLAMA_*``), so there
is no API cost and nothing leaves the machine. ``ask`` returns plain text;
``draft_email`` returns ``{"subject", "body"}``.
"""
from __future__ import annotations

import httpx

from alonarg import config
from alonarg.summarize import extract_json


def _chat(
    system: str,
    user: str,
    *,
    fmt: str | None = None,
    host: str | None = None,
    model: str | None = None,
    timeout: int | None = None,
) -> str:
    """Single-turn chat against the local Ollama model; returns the text reply."""
    host = (host or config.OLLAMA_HOST).rstrip("/")
    model = model or config.OLLAMA_MODEL
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {"temperature": 0.3, "num_ctx": config.OLLAMA_NUM_CTX},
    }
    if fmt:
        payload["format"] = fmt
    try:
        resp = httpx.post(
            f"{host}/api/chat", json=payload, timeout=timeout or config.OLLAMA_TIMEOUT
        )
    except httpx.HTTPError as exc:
        raise RuntimeError(
            f"Cannot reach the local AI (Ollama) at {host}. Make sure Ollama is "
            f"running. ({exc})"
        ) from exc
    if resp.status_code != 200:
        raise RuntimeError(f"Ollama error {resp.status_code}: {resp.text}")
    data = resp.json()
    return (data.get("message") or {}).get("content", "")


def ask(question: str, context_text: str, **kw) -> str:
    """Answer a question grounded ONLY in the provided meeting text."""
    system = (
        "You answer questions about the user's meetings using ONLY the provided "
        "notes and transcripts. If the answer is not in them, say you couldn't "
        "find it in the meetings. Be concise and specific."
    )
    user = (
        "=== MEETING NOTES & TRANSCRIPTS ===\n"
        f"{context_text}\n\n=== QUESTION ===\n{question}"
    )
    return _chat(system, user, **kw).strip()


def draft_email(action_item: str, context_text: str = "", **kw) -> dict:
    """Draft a short professional email for an action item.

    Returns ``{"subject": str, "body": str}``.
    """
    system = (
        "You draft short, professional, ready-to-send emails. Respond with ONLY "
        'a JSON object with keys "subject" and "body". Keep it concise and '
        "friendly-professional. Do not invent specific names, dates, or facts "
        "that aren't given; use a neutral placeholder like [Name] only if needed."
    )
    user = (
        f"Meeting context:\n{context_text or '(none)'}\n\n"
        f"Write an email to carry out this action item:\n{action_item}"
    )
    content = _chat(system, user, fmt="json", **kw)
    data = extract_json(content)
    return {
        "subject": str(data.get("subject", "")).strip(),
        "body": str(data.get("body", "")).strip(),
    }
