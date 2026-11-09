from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .memory import read_json_object, write_json

LEDGER_VERSION = 1


def _normalized(value: Any) -> str:
    text = str(value or "").lower()
    text = re.sub(r"\b(?:ltd|limited|plc|inc|llc)\b\.?", "", text)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def application_key(job: dict[str, Any]) -> str:
    """Stable identity for a role, independent of source URL changes."""
    return f"{_normalized(job.get('company'))}|{_normalized(job.get('title'))}"


def load_ledger(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"version": LEDGER_VERSION, "applications": {}}
    data = read_json_object(path)
    if not isinstance(data.get("applications"), dict):
        return {"version": LEDGER_VERSION, "applications": {}}
    data["version"] = LEDGER_VERSION
    return data


def previous_application(ledger: dict[str, Any], job: dict[str, Any]) -> dict[str, Any] | None:
    record = ledger.get("applications", {}).get(application_key(job))
    return record if isinstance(record, dict) else None


def should_skip(
    ledger: dict[str, Any],
    job: dict[str, Any],
    reapply_after_days: int | float | None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Return the previous application when a role is still in its cooldown."""
    record = previous_application(ledger, job)
    if not record:
        return None
    if reapply_after_days is None or float(reapply_after_days) <= 0:
        return None
    last_value = record.get("last_applied_at")
    if not last_value:
        return record
    try:
        last = datetime.fromisoformat(str(last_value))
    except ValueError:
        return record
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    if current - last < timedelta(days=float(reapply_after_days)):
        return record
    return None


def record_application(
    path: Path,
    job: dict[str, Any],
    documents: dict[str, Any] | None = None,
    status: str = "applied",
    applied_at: datetime | None = None,
) -> dict[str, Any]:
    ledger = load_ledger(path)
    key = application_key(job)
    applications = ledger.setdefault("applications", {})
    existing = applications.get(key)
    record = existing if isinstance(existing, dict) else {}
    timestamp = (applied_at or datetime.now(timezone.utc)).isoformat()
    record.update({
        "company": job.get("company", ""),
        "title": job.get("title", ""),
        "url": job.get("url", ""),
        "employment_type": job.get("employment_type", ""),
        "status": status,
        "first_applied_at": record.get("first_applied_at") or timestamp,
        "last_applied_at": timestamp,
        "count": int(record.get("count", 0)) + 1,
        "documents": documents or record.get("documents", {}),
    })
    applications[key] = record
    ledger["version"] = LEDGER_VERSION
    write_json(path, ledger)
    return record
