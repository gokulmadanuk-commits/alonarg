"""Read the user's Outlook *desktop* calendar via PowerShell COM (Windows-only).

No OAuth / app registration: this uses the already-signed-in Outlook desktop
profile. We shell out to PowerShell (which has native COM support) instead of
depending on pywin32, and parse the JSON it returns.

Used to enrich a recording with its matching calendar event: the event title and
its attendees (name + resolved SMTP email).
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta

# PowerShell that lists calendar items overlapping a [__LO__, __HI__] local-time
# window and prints them as JSON. Exchange addresses are resolved to SMTP.
_PS_TEMPLATE = r"""
$ErrorActionPreference = 'Stop'
try { $ol = New-Object -ComObject Outlook.Application } catch { Write-Output '{"error":"outlook_unavailable"}'; exit 0 }
$ns = $ol.GetNamespace('MAPI')
$cal = $ns.GetDefaultFolder(9)
$items = $cal.Items
$items.IncludeRecurrences = $true
$items.Sort('[Start]')
$filter = "[Start] <= '__HI__' AND [End] >= '__LO__'"
$res = $items.Restrict($filter)
$out = @()
foreach ($it in $res) {
  $att = @()
  try {
    foreach ($r in $it.Recipients) {
      $addr = ''
      try { $addr = [string]$r.Address } catch {}
      if ($addr -notlike '*@*') {
        try { $eu = $r.AddressEntry.GetExchangeUser(); if ($eu) { $addr = [string]$eu.PrimarySmtpAddress } } catch {}
      }
      $att += [pscustomobject]@{ name = [string]$r.Name; email = $addr }
    }
  } catch {}
  $out += [pscustomobject]@{
    subject   = [string]$it.Subject
    start     = $it.Start.ToString('o')
    end       = $it.End.ToString('o')
    organizer = [string]$it.Organizer
    attendees = $att
  }
}
ConvertTo-Json -InputObject @($out) -Depth 6
"""


def _run_ps(script: str, timeout: int = 60) -> str:
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=timeout,
    )
    return proc.stdout or ""


def read_events_window(lo_local: str, hi_local: str) -> list[dict]:
    """Return Outlook calendar events overlapping the given local-time window."""
    script = _PS_TEMPLATE.replace("__LO__", lo_local).replace("__HI__", hi_local)
    out = _run_ps(script).strip()
    if not out:
        return []
    data = json.loads(out)
    if isinstance(data, dict):
        if data.get("error") == "outlook_unavailable":
            raise RuntimeError("Outlook desktop isn't available. Open Outlook and try again.")
        data = [data]
    return data


def _parse_dt(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def pick_event(events: list[dict], when: datetime, nearest_tolerance_s: int = 1500) -> dict | None:
    """Pure: pick the event whose [start, end] contains ``when``.

    Containment always wins (handles back-to-back meetings: you match the one you
    are actually in). Otherwise fall back to the nearest event *start* — but only
    if it's within ``nearest_tolerance_s`` (default 25 min), so a far-away meeting
    is never matched. Returns None if nothing is close enough.
    """
    best = None
    best_delta = None
    for ev in events:
        start = _parse_dt(ev.get("start", ""))
        end = _parse_dt(ev.get("end", ""))
        if start is None:
            continue
        if end is not None and start <= when <= end:
            return ev
        delta = abs((start - when).total_seconds())
        if best_delta is None or delta < best_delta:
            best, best_delta = ev, delta
    if best is not None and best_delta is not None and best_delta <= nearest_tolerance_s:
        return best
    return None


def find_event_for(created_at_iso: str, window_minutes: int = 90) -> dict | None:
    """Find the Outlook event matching a recording's start time. None if no match."""
    when = _parse_dt(created_at_iso)
    if when is None:
        return None
    when_local = when.astimezone()
    lo = (when_local - timedelta(minutes=window_minutes)).strftime("%m/%d/%Y %I:%M %p")
    hi = (when_local + timedelta(minutes=window_minutes)).strftime("%m/%d/%Y %I:%M %p")
    return pick_event(read_events_window(lo, hi), when_local)
