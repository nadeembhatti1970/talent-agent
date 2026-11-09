from __future__ import annotations

import re
from typing import Any


_CLEARANCE_REQUIRED_PATTERNS = (
    re.compile(
        r"\b(?:security\s+)?clearance\b"
        r"(?:\s+(?:is\s+)?(?:required|essential|mandatory|a\s+requirement))\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:required|essential|mandatory)\s+"
        r"(?:(?:active|current|valid|existing)\s+)?"
        r"(?:security\s+)?clearance\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:must|need(?:s)?\s+to)\s+"
        r"(?:already\s+)?(?:hold|have|possess|obtain|maintain|be\s+eligible\s+for)\b"
        r"[^.\n]{0,80}\b(?:security\s+)?clearance\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:SC|DV|CTC|Developed\s+Vetting|Counter\s+Terrorist\s+Check)\s+"
        r"(?:clearance|vetting|eligibility)\b"
        r"[^.\n]{0,60}\b(?:required|essential|mandatory|must\s+have|must\s+hold)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:required|essential|mandatory|must\s+have|must\s+hold)\b"
        r"[^.\n]{0,60}\b(?:SC|DV|CTC|Developed\s+Vetting|Counter\s+Terrorist\s+Check)\s+"
        r"(?:clearance|vetting|eligibility)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:SC|DV|CTC)[\s-]+(?:cleared|vetted)\b"
        r"(?![^.\n]{0,40}\b(?:desirable|preferred|advantageous|beneficial|nice\s+to\s+have)\b)",
        re.IGNORECASE,
    ),
)
_WORKPLACE_ARRANGEMENTS = {"remote", "hybrid", "onsite"}
_HOURS_PER_WORKDAY = 8
# Reed and Adzuna sometimes return a bare amount without a pay period. A
# contract day rate is normally below £2,000, while an annual salary is above
# £20,000. Values in between are ambiguous and are left unclassified.
_DAY_RATE_CEILING = 2_000
_ANNUAL_SALARY_FLOOR = 20_000

_ROLE_FAMILIES = (
    ("sre", re.compile(r"\bsite\s+reliability\b|\bsre\b", re.IGNORECASE)),
    ("devops", re.compile(r"\bdevops\b|\bdev\s+ops\b", re.IGNORECASE)),
    ("platform", re.compile(
        r"\bplatform\s+(?:engineer|engineering|architect|lead|manager|specialist|consultant)\b|"
        r"\b(?:engineer|engineering|architect|lead|manager|specialist|consultant)\s+platform\b",
        re.IGNORECASE,
    )),
    ("cloud", re.compile(
        r"\bcloud\s+(?:engineer|engineering|architect|infrastructure|platform|specialist|consultant)\b|"
        r"\b(?:engineer|architect|specialist|consultant)\s+cloud\b",
        re.IGNORECASE,
    )),
    ("infrastructure", re.compile(
        r"\binfrastructure\s+(?:engineer|engineering|architect|lead|specialist|consultant)\b|"
        r"\b(?:engineer|architect|lead|specialist|consultant)\s+infrastructure\b",
        re.IGNORECASE,
    )),
    ("automation", re.compile(
        r"\bautomation\s+(?:engineer|engineering|architect|specialist|consultant)\b|"
        r"\b(?:python|aws|cloud|devops|infrastructure)\s+automation\b",
        re.IGNORECASE,
    )),
    ("mlops", re.compile(r"\bmlops\b|\bml\s+platform\b|\bmachine\s+learning\s+platform\b", re.IGNORECASE)),
    ("release", re.compile(r"\bbuild\s+(?:and|&)\s+release\b|\brelease\s+engineer\b", re.IGNORECASE)),
)


def classify_role_family(job: dict[str, Any]) -> str:
    """Return the candidate's core role family for a listing, based on its title.

    The title is used deliberately: descriptions often mention cloud or platform
    work for backend, data, and security roles, which would defeat the gate.
    """
    title = str(job.get("title") or "")
    for family, pattern in _ROLE_FAMILIES:
        if pattern.search(title):
            return family
    return ""


def application_gate(
    job: dict[str, Any], minimum_tech_overlap: float, role_family_gate: bool
) -> tuple[bool, str]:
    """Decide whether a scored job should receive tailored application documents."""
    if not role_family_gate:
        return True, ""
    if not classify_role_family(job):
        return False, "filtered_role_family"
    try:
        tech_overlap = float(job["tech_overlap"])
    except (KeyError, TypeError, ValueError):
        return True, ""
    if tech_overlap < float(minimum_tech_overlap):
        return False, "filtered_tech_overlap"
    return True, ""


def classify_workplace_arrangement(job: dict[str, Any]) -> str:
    explicit_fields = (
        "workplace_arrangement",
        "workplace_type",
        "workplaceType",
        "remote_type",
        "remoteType",
        "work_location_type",
        "workLocationType",
    )
    for field in explicit_fields:
        value = str(job.get(field, "")).strip()
        if value:
            arrangement = _arrangement_from_text(value)
            if arrangement:
                return arrangement

    return _arrangement_from_text(
        "\n".join(
            str(job.get(field, ""))
            for field in ("title", "location", "description")
            if job.get(field)
        )
    ) or "unknown"


def _arrangement_from_text(value: str) -> str | None:
    text = value.lower().replace("_", " ").replace("-", " ")
    hybrid = any(
        term in text
        for term in ("hybrid", "partly remote", "part remote", "partially remote")
    )
    remote = any(
        term in text
        for term in ("remote", "work from home", "home based", "fully home based")
    )
    onsite = any(
        term in text
        for term in ("onsite", "on site", "office based")
    )
    if hybrid or (remote and onsite):
        return "hybrid"
    if remote:
        return "remote"
    if onsite:
        return "onsite"
    return None


def filter_jobs(jobs: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    allowed_types = set(config["employment_types"])
    allowed_arrangements = set(config.get("workplace_arrangements", _WORKPLACE_ARRANGEMENTS))
    filtered: list[dict[str, Any]] = []
    for job in jobs:
        if config.get("exclude_security_clearance", False) and _requires_security_clearance(job):
            continue
        arrangement = classify_workplace_arrangement(job)
        if arrangement in _WORKPLACE_ARRANGEMENTS and arrangement not in allowed_arrangements:
            continue
        kind = str(job.get("employment_type", "unknown")).lower()
        # Unknown types remain eligible when both categories are selected; a single-category
        # search requires a positive classification to avoid silently mixing job types.
        if kind in {"contract", "permanent"} and kind not in allowed_types:
            continue
        if kind == "unknown" and len(allowed_types) == 1:
            continue
        salary = str(job.get("salary", ""))
        compensation_period = _compensation_period(salary)
        expected_period = "daily" if kind == "contract" else "annual" if kind == "permanent" else None
        if (
            compensation_period
            and expected_period
            and compensation_period != expected_period
            and not (kind == "contract" and compensation_period == "hourly")
        ):
            continue
        minimum = config["minimum_day_rate"] if kind == "contract" else config["minimum_annual_salary"] if kind == "permanent" else None
        if minimum is not None:
            amount = _matching_compensation(salary, kind, str(config.get("currency", "GBP")))
            exclude_unknown = (
                not config.get(
                    "include_unknown_contract_compensation",
                    not config["exclude_unknown_compensation"],
                )
                if kind == "contract"
                else config["exclude_unknown_compensation"]
            )
            if amount is None and exclude_unknown:
                continue
            if amount is not None and amount < float(minimum):
                continue
        filtered.append(job)
    return filtered


def _requires_security_clearance(job: dict[str, Any]) -> bool:
    clearance_fields = (
        job.get("security_clearance"),
        job.get("clearance"),
        job.get("title"),
        job.get("description"),
    )
    text = "\n".join(str(value) for value in clearance_fields if value)
    return any(pattern.search(text) for pattern in _CLEARANCE_REQUIRED_PATTERNS)


def _compensation_period(value: str) -> str | None:
    text = value.lower()
    daily = bool(re.search(r"(?:/\s*day|per\s+day|daily|a\s+day|\bday\s+rate\b)", text))
    annual = bool(re.search(r"(?:/\s*(?:year|yr|annum|pa)|per\s+(?:year|annum)|\bannual(?:ly)?\b|\bpa\b|\bp\.a\.\b)", text))
    hourly = bool(
        re.search(r"(?:/\s*(?:hr|hour)\b|per\s+hour\b|\bhourly\b|\ban?\s+hour\b)", text)
    )
    if daily and not annual and not hourly:
        return "daily"
    if annual and not daily and not hourly:
        return "annual"
    if hourly and not daily and not annual:
        return "hourly"
    if daily or annual or hourly:
        return None

    # Some sources (notably Reed) omit the pay period. Infer it from the
    # amount so a bare day rate is not mistaken for an annual salary and a
    # bare annual salary is not mistaken for a day rate.
    numbers = [float(value.replace(",", "")) for value in re.findall(r"\d[\d,]*(?:\.\d+)?", text)]
    if not numbers:
        return None
    amount = max(numbers)
    if amount < _DAY_RATE_CEILING:
        return "daily"
    if amount >= _ANNUAL_SALARY_FLOOR:
        return "annual"
    return None


def _matching_compensation(value: str, employment_type: str, currency: str) -> float | None:
    text = value.lower()
    currency = currency.upper()
    symbols = {"GBP": "£", "EUR": "€", "USD": "$"}
    explicit_currency = next((code for code, symbol in symbols.items() if symbol in value), None)
    if explicit_currency is None:
        for code in symbols:
            if re.search(rf"\b{code}\b", value.upper()):
                explicit_currency = code
                break
    if explicit_currency and explicit_currency != currency:
        return None
    text = text.replace("£", "").replace("€", "").replace("$", "")
    period = _compensation_period(text)
    if period == "daily" and employment_type != "contract":
        return None
    if period == "hourly" and employment_type != "contract":
        return None
    if period == "annual" and employment_type != "permanent":
        return None
    numbers = [float(value.replace(",", "")) for value in re.findall(r"\d[\d,]*(?:\.\d+)?", text)]
    if not numbers:
        return None
    if re.search(r"\d\s*k\b", text):
        numbers = [number * 1000 if number < 1000 else number for number in numbers]
    amount = max(numbers)
    return amount * _HOURS_PER_WORKDAY if period == "hourly" else amount
