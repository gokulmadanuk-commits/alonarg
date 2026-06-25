"""Microsoft Graph calendar access via MSAL device-code login.

Works with "new Outlook" / Microsoft 365 (which has no local COM). The user signs
in once via the device-code flow; tokens (incl. refresh) are cached in the data
dir so it stays connected. Calendar reads use /me/calendarView and are normalized
to the same event shape :mod:`alonarg.outlook` produces, so the rest of the app is
source-agnostic.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path

import httpx

from alonarg import config

GRAPH = "https://graph.microsoft.com/v1.0"
# Requested at login (device-code consent). Mail is read-only and only used to
# enrich pre-meeting briefs. Calendar and mail tokens are acquired per-scope so
# that calendar keeps working even if mail consent hasn't been granted yet.
CAL_SCOPES = ["Calendars.Read"]
MAIL_SCOPES = ["Mail.Read"]
SCOPES = CAL_SCOPES + MAIL_SCOPES

_lock = threading.Lock()
_app = None
_cache = None
_flow = None  # pending device-code flow (None when not logging in)


def _cache_path() -> Path:
    return Path(config.DATA_DIR) / "graph_token_cache.json"


def _load_cache():
    import msal

    cache = msal.SerializableTokenCache()
    p = _cache_path()
    if p.exists():
        try:
            cache.deserialize(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - corrupt cache -> start fresh
            pass
    return cache


def _save_cache() -> None:
    if _cache is not None and _cache.has_state_changed:
        _cache_path().parent.mkdir(parents=True, exist_ok=True)
        _cache_path().write_text(_cache.serialize(), encoding="utf-8")


def _get_app():
    global _app, _cache
    import msal

    with _lock:
        if _app is None:
            _cache = _load_cache()
            _app = msal.PublicClientApplication(
                config.GRAPH_CLIENT_ID,
                authority=config.GRAPH_AUTHORITY,
                token_cache=_cache,
            )
        return _app


def is_signed_in() -> bool:
    try:
        return bool(_get_app().get_accounts())
    except Exception:  # noqa: BLE001
        return False


def account_name() -> str | None:
    accts = _get_app().get_accounts()
    return accts[0].get("username") if accts else None


def login_pending() -> bool:
    return _flow is not None


def get_token(scopes: list[str] | None = None) -> str | None:
    """Return a valid access token for ``scopes`` from the cache, or None.

    Per-scope so calendar (Calendars.Read) keeps working even when mail
    (Mail.Read) hasn't been consented yet — that read simply returns None.
    """
    app = _get_app()
    accts = app.get_accounts()
    if not accts:
        return None
    result = app.acquire_token_silent(scopes or CAL_SCOPES, account=accts[0])
    _save_cache()
    if result and "access_token" in result:
        return result["access_token"]
    return None


def mail_available() -> bool:
    """True if we hold a usable Mail.Read token (i.e. email reads will work)."""
    try:
        return bool(get_token(MAIL_SCOPES))
    except Exception:  # noqa: BLE001
        return False


def start_device_login() -> dict:
    """Begin the device-code flow. Returns the code/URL for the user.

    Completion is awaited on a background thread; callers poll :func:`is_signed_in`.
    """
    global _flow
    app = _get_app()
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise RuntimeError(flow.get("error_description") or "Could not start Microsoft sign-in.")
    _flow = flow

    def _complete():
        global _flow
        try:
            app.acquire_token_by_device_flow(flow)  # blocks until done/expired
            _save_cache()
        except Exception:  # noqa: BLE001
            pass
        finally:
            _flow = None

    threading.Thread(target=_complete, daemon=True).start()
    return {
        "user_code": flow.get("user_code"),
        "verification_uri": flow.get("verification_uri"),
        "message": flow.get("message"),
        "expires_in": flow.get("expires_in"),
    }


def sign_out() -> None:
    global _app, _cache, _flow
    app = _get_app()
    for acct in app.get_accounts():
        app.remove_account(acct)
    _save_cache()
    try:
        _cache_path().unlink()
    except OSError:
        pass
    with _lock:
        _app = None
        _cache = None
        _flow = None


def _iso(dt: str | None) -> str:
    """Graph returns UTC dateTimes like '2026-06-24T20:00:00.0000000' (no offset)."""
    if not dt:
        return ""
    dt = dt.replace("Z", "").split(".")[0]
    return dt + "+00:00"


def _to_event(item: dict) -> dict:
    attendees = []
    for a in item.get("attendees", []) or []:
        ea = a.get("emailAddress") or {}
        attendees.append({"name": ea.get("name", ""), "email": ea.get("address", "")})
    resp = (item.get("responseStatus") or {}).get("response") or ""
    return {
        "id": item.get("id", ""),
        "subject": item.get("subject", ""),
        "start": _iso((item.get("start") or {}).get("dateTime")),
        "end": _iso((item.get("end") or {}).get("dateTime")),
        "attendees": attendees,
        "allDay": bool(item.get("isAllDay")),
        "response": 4 if resp == "declined" else 0,
        "organizer": ((item.get("organizer") or {}).get("emailAddress") or {}).get("name", ""),
        "body": (item.get("bodyPreview") or "").strip(),
    }


def read_events_window(start: datetime, end: datetime) -> list[dict]:
    """Return calendar events overlapping [start, end]. Empty list if not signed in."""
    token = get_token(CAL_SCOPES)
    if not token:
        return []
    params = {
        "startDateTime": start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "endDateTime": end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "$select": "id,subject,start,end,attendees,isAllDay,responseStatus,organizer,bodyPreview",
        "$orderby": "start/dateTime",
        "$top": "50",
    }
    headers = {"Authorization": "Bearer " + token, "Prefer": 'outlook.timezone="UTC"'}
    try:
        resp = httpx.get(GRAPH + "/me/calendarView", params=params, headers=headers, timeout=20)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Microsoft Graph request failed: {exc}") from exc
    if resp.status_code != 200:
        raise RuntimeError(f"Microsoft Graph error {resp.status_code}: {resp.text[:200]}")
    return [_to_event(it) for it in (resp.json().get("value") or [])]


def read_messages(address: str, top: int = 5) -> list[dict]:
    """Recent emails involving ``address`` (from/to/cc), newest first.

    Returns ``[{subject, from, received, preview}]``. Empty if Mail.Read isn't
    granted or anything goes wrong (best-effort enrichment only).
    """
    address = (address or "").strip()
    if not address:
        return []
    token = get_token(MAIL_SCOPES)
    if not token:
        return []
    params = {
        "$search": f'"{address}"',
        "$select": "subject,from,toRecipients,receivedDateTime,bodyPreview",
        "$top": str(max(1, min(top, 25))),
    }
    headers = {"Authorization": "Bearer " + token, "ConsistencyLevel": "eventual"}
    try:
        resp = httpx.get(GRAPH + "/me/messages", params=params, headers=headers, timeout=20)
    except httpx.HTTPError:
        return []
    if resp.status_code != 200:
        return []
    out = []
    for m in resp.json().get("value") or []:
        out.append({
            "subject": m.get("subject", "") or "(no subject)",
            "from": ((m.get("from") or {}).get("emailAddress") or {}).get("address", ""),
            "received": m.get("receivedDateTime", ""),
            "preview": (m.get("bodyPreview", "") or "")[:400],
        })
    out.sort(key=lambda x: x["received"], reverse=True)
    return out
