"""Aggregate people across meetings — a lightweight relationship memory.

Pure functions over the recordings store (no network). A "person" is identified
by email when available, otherwise by a normalized name, and merged across all
meetings they appear in (from each recording's ``meta`` people/contacts).
"""
from __future__ import annotations

import re

# Generic speaker labels / non-names to never treat as a person.
_GENERIC = {
    "you", "others", "other", "speaker", "speakers", "me", "them", "they",
    "unknown", "n/a", "none", "participant", "participants", "attendee",
    "attendees", "host", "guest", "caller", "everyone", "team", "the team",
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _strip_role(name: str) -> str:
    """Drop a trailing "(role/company)" annotation from an inferred name."""
    return re.sub(r"\(.*?\)", "", name or "").strip()


def _person_key(name: str, email: str) -> str:
    e = (email or "").strip().lower()
    if e:
        return e
    n = _norm(_strip_role(name))
    return "name:" + n if n else ""


def _participants(meta: dict):
    """Yield ``(name, email, phone)`` for everyone named in a recording's meta."""
    meta = meta or {}
    seen_names = set()
    for c in meta.get("contacts", []) or []:
        if not isinstance(c, dict):
            continue
        nm = str(c.get("name", "")).strip()
        em = str(c.get("email", "")).strip()
        ph = str(c.get("phone", "")).strip()
        if nm or em:
            seen_names.add(_norm(_strip_role(nm)))
            yield nm, em, ph
    for p in meta.get("people", []) or []:
        nm = _strip_role(str(p))
        if nm and _norm(nm) not in seen_names:
            yield nm, "", ""


def _build(db, self_emails=()) -> dict:
    """Build the full person map keyed by person-key."""
    self_e = {str(e).lower() for e in (self_emails or [])}
    rows = db.list_recordings()

    rec_open: dict = {}
    for row in rows:
        summ = row.get("summary") or {}
        done = set((row.get("state") or {}).get("done_actions") or [])
        rec_open[row["id"]] = [it for it in (summ.get("action_items") or []) if it not in done]

    people: dict = {}
    for row in rows:
        for nm, em, ph in _participants(row.get("meta") or {}):
            key = _person_key(nm, em)
            if not key:
                continue
            if em and em.lower() in self_e:
                continue
            base = _strip_role(nm)
            if _norm(base) in _GENERIC:
                continue
            p = people.get(key)
            if p is None:
                p = {"id": key, "name": "", "email": "", "phone": "",
                     "meetings": [], "open_actions": [], "_mids": set()}
                people[key] = p
            if base and len(base) > len(p["name"]):
                p["name"] = base
            if em and not p["email"]:
                p["email"] = em.lower()
            if ph and not p["phone"]:
                p["phone"] = ph
            if row["id"] not in p["_mids"]:
                p["_mids"].add(row["id"])
                p["meetings"].append({
                    "id": row["id"],
                    "title": row.get("title") or "Untitled",
                    "created_at": row.get("created_at") or "",
                })
                for it in rec_open.get(row["id"], []):
                    p["open_actions"].append({
                        "recording_id": row["id"],
                        "title": row.get("title") or "Untitled",
                        "item": it,
                    })

    for p in people.values():
        if not p["name"]:
            p["name"] = p["email"] or "Unknown"
        p["meetings"].sort(key=lambda m: m["created_at"], reverse=True)
        p["last_met"] = p["meetings"][0]["created_at"] if p["meetings"] else ""
        p["meeting_count"] = len(p["meetings"])
        p.pop("_mids", None)
    return people


def aggregate(db, self_emails=()) -> list[dict]:
    """List of people (summary rows), most-recently-met first."""
    people = _build(db, self_emails)
    out = [
        {
            "id": p["id"], "name": p["name"], "email": p["email"],
            "meeting_count": p["meeting_count"], "last_met": p["last_met"],
            "open_actions": len(p["open_actions"]),
        }
        for p in people.values()
    ]
    # Action-first ordering (à la OnePageCRM): people with open items surface
    # first (more open = higher), then most-recently-met.
    out.sort(key=lambda x: (1 if x["open_actions"] else 0, x["open_actions"], x["last_met"] or ""), reverse=True)
    return out


def person_detail(db, person_id: str, self_emails=()) -> dict | None:
    """Full record for one person (meetings + open action items + contact), or None."""
    return _build(db, self_emails).get(person_id)
