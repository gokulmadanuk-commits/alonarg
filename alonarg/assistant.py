"""Local-LLM helpers built on Ollama: Q&A over meetings and email drafting.

Both reuse the same local model as the summarizer (``config.OLLAMA_*``), so there
is no API cost and nothing leaves the machine. ``ask`` returns plain text;
``draft_email`` returns ``{"subject", "body"}``.
"""
from __future__ import annotations

import re

import httpx

from alonarg import config
from alonarg.summarize import extract_json

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Generic speaker labels / non-names to drop from inferred people.
_GENERIC_PEOPLE = {
    "you", "others", "other", "speaker", "speakers", "me", "them", "they",
    "unknown", "n/a", "none", "participant", "participants", "attendee",
    "attendees", "host", "guest", "caller", "everyone", "team", "the team",
}


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
        "information: an optional computed OVERVIEW with exact counts, a "
        "per-meeting metadata table (status, action item / next step counts, "
        "duration, date), and meeting summaries/transcripts. For counting or "
        "'how many' questions, use the exact numbers from the OVERVIEW/metadata "
        "instead of counting yourself. If the answer isn't present, say you "
        "couldn't find it in the meetings. Be concise and specific."
    )
    user = (
        "=== MEETING NOTES & TRANSCRIPTS ===\n"
        f"{context_text}\n\n=== QUESTION ===\n{question}"
    )
    return _chat(system, user, **kw).strip()


def brief(subject: str, attendees: list[str], context_text: str, **kw) -> dict:
    """A structured, scannable pre-meeting brief grounded in the provided context.

    Returns ``{"headline": str, "points": [str], "actions": [str]}``: a one-line
    headline, key context bullets, and open/action items to raise.
    """
    system = (
        "You write a concise, scannable PRE-MEETING BRIEF from the user's past "
        "meetings, recent emails, and the calendar invite. Respond with ONLY a JSON "
        "object with exactly these keys:\n"
        '  "headline": one short sentence on what this meeting is and where things stand.\n'
        '  "points": an array of 3-6 short bullet strings — key context, recent updates, '
        "and decisions worth knowing going in.\n"
        '  "actions": an array of short bullet strings — open items, follow-ups you owe, '
        "or things to raise (empty array if none).\n"
        "Base everything ONLY on the provided information; if little is known, say so in "
        "the headline and keep the arrays short. Keep bullets crisp. No markdown, no preamble."
    )
    user = (
        f"Upcoming meeting: {subject or '(untitled)'}\n"
        f"Attendees: {', '.join(a for a in attendees if a) or '(unknown)'}\n\n"
        f"Context:\n{context_text}"
    )
    content = _chat(system, user, fmt="json", **kw)
    try:
        data = extract_json(content)
    except ValueError:
        data = {}

    def _arr(x):
        return [str(i).strip() for i in (x or []) if str(i).strip()]

    return {
        "headline": str(data.get("headline", "")).strip(),
        "points": _arr(data.get("points")),
        "actions": _arr(data.get("actions")),
    }


def ask_cited(question: str, numbered_context: str, **kw) -> str:
    """Answer using ONLY numbered context snippets, citing them inline as [n]."""
    system = (
        "You answer questions about the user's meetings using ONLY the numbered "
        "context snippets provided. After each statement, cite the snippet "
        "number(s) you used in square brackets, e.g. [1] or [2][3]. If the answer "
        "isn't in the snippets, say you couldn't find it in the meetings. Be concise."
    )
    user = "Context snippets:\n" + numbered_context + "\n\n=== QUESTION ===\n" + question
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


def extract_details(transcript_text: str, summary_text: str = "", **kw) -> dict:
    """Infer attendees + contact details from a meeting.

    Returns ``{"people": [str, ...], "contacts": [{"name","email","phone"}, ...]}``.
    Raises RuntimeError if the local model is unreachable (so callers can report
    it). Literal emails found in the transcript text are added as a backstop.
    """
    system = (
        "You extract who was in a meeting and any contact details mentioned. "
        'Respond with ONLY a JSON object: {"people": ["Name (role/company if '
        'mentioned)", ...], "contacts": [{"name": "", "email": "", "phone": ""}]}. '
        "Include only people and details actually mentioned; never invent names, "
        "emails, or numbers. Ignore generic speaker labels such as 'You', "
        "'Others', or 'Speaker' - list only real named people. Use empty strings "
        "for unknown fields."
    )
    user = f"Summary:\n{summary_text or '(none)'}\n\nTranscript:\n{transcript_text or '(none)'}"
    content = _chat(system, user, fmt="json", **kw)  # raises RuntimeError if model down

    people: list[str] = []
    contacts: list[dict] = []
    try:
        data = extract_json(content)
    except ValueError:
        data = {}
    for p in data.get("people") or []:
        name = str(p).strip()
        base = re.sub(r"\(.*?\)", "", name).strip().lower()  # ignore "(role)" annotations
        if name and base and base not in _GENERIC_PEOPLE:
            people.append(name)
    for c in data.get("contacts") or []:
        if not isinstance(c, dict):
            continue
        entry = {
            "name": str(c.get("name", "")).strip(),
            "email": str(c.get("email", "")).strip(),
            "phone": str(c.get("phone", "")).strip(),
        }
        if entry["name"] or entry["email"] or entry["phone"]:
            contacts.append(entry)

    seen = {c["email"].lower() for c in contacts if c["email"]}
    for em in _EMAIL_RE.findall(transcript_text or ""):
        if em.lower() not in seen:
            contacts.append({"name": "", "email": em, "phone": ""})
            seen.add(em.lower())

    return {"people": people, "contacts": contacts}
