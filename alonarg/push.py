"""Web Push (VAPID) for nudging the phone PWA.

Keys and subscriptions live in the data dir. The application server (VAPID) key
pair is generated once. Subscriptions are stored in a JSON file. Sending uses
pywebpush; dead subscriptions (404/410) are pruned automatically.
"""
from __future__ import annotations

import base64
import json
import os
import threading
from pathlib import Path

from alonarg import config

_lock = threading.Lock()
# VAPID "sub" must be a valid mailto:/https: contact. Apple rejects "localhost"
# with 403 BadJwtToken. Override via ALONARG_VAPID_SUB if you like.
VAPID_SUB = os.environ.get("ALONARG_VAPID_SUB", "mailto:nudges@alonarg.app")


def _priv_path() -> Path:
    return Path(config.DATA_DIR) / "vapid_private.pem"


def _pub_path() -> Path:
    return Path(config.DATA_DIR) / "vapid_public.txt"


def _subs_path() -> Path:
    return Path(config.DATA_DIR) / "push_subscriptions.json"


def get_or_create_keys() -> tuple[str, str]:
    """Return (application_server_key_b64url, private_pem_path), generating once."""
    priv, pub = _priv_path(), _pub_path()
    if priv.exists() and pub.exists():
        return pub.read_text(encoding="utf-8").strip(), str(priv)
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    raw = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    appkey = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    priv.parent.mkdir(parents=True, exist_ok=True)
    priv.write_bytes(pem)
    pub.write_text(appkey, encoding="utf-8")
    return appkey, str(priv)


def public_key() -> str:
    """The base64url application server key the PWA passes to pushManager.subscribe."""
    return get_or_create_keys()[0]


def list_subscriptions() -> list[dict]:
    p = _subs_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (ValueError, OSError):
        return []


def _save(subs: list[dict]) -> None:
    _subs_path().parent.mkdir(parents=True, exist_ok=True)
    _subs_path().write_text(json.dumps(subs), encoding="utf-8")


def add_subscription(sub: dict) -> None:
    if not sub or not sub.get("endpoint"):
        return
    with _lock:
        subs = [s for s in list_subscriptions() if s.get("endpoint") != sub["endpoint"]]
        subs.append(sub)
        _save(subs)


def remove_subscription(endpoint: str) -> None:
    with _lock:
        _save([s for s in list_subscriptions() if s.get("endpoint") != endpoint])


def send_to_all(title: str, body: str, url: str = "/", data: dict | None = None) -> dict:
    """Send a notification to every stored subscription. Prunes dead ones.

    Returns ``{"sent": int, "removed": int}``. Safe to call when nothing is
    subscribed (returns zeros).
    """
    from pywebpush import WebPushException, webpush

    subs = list_subscriptions()
    if not subs:
        return {"sent": 0, "removed": 0, "total": 0, "errors": []}
    _appkey, priv_path = get_or_create_keys()
    payload = json.dumps({"title": title, "body": body, "url": url, "data": data or {}})
    sent = removed = 0
    errors: list[str] = []
    for sub in subs:
        try:
            webpush(
                subscription_info=sub,
                data=payload,
                vapid_private_key=priv_path,
                vapid_claims={"sub": VAPID_SUB},
                ttl=120,
            )
            sent += 1
        except WebPushException as exc:
            resp = getattr(exc, "response", None)
            status = getattr(resp, "status_code", None)
            if status in (404, 410):
                remove_subscription(sub.get("endpoint", ""))
                removed += 1
            else:
                errors.append(f"{status}: {(getattr(resp, 'text', '') or str(exc))[:120]}")
        except Exception as exc:  # noqa: BLE001 - never let one bad sub break the rest
            errors.append(str(exc)[:120])
    return {"sent": sent, "removed": removed, "total": len(subs), "errors": errors}
