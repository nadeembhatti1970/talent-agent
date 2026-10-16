from __future__ import annotations

import re
from typing import Any


def filter_jobs(jobs: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    allowed_types = set(config["employment_types"])
    filtered: list[dict[str, Any]] = []
    for job in jobs:
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
        if compensation_period and expected_period and compensation_period != expected_period:
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


def _compensation_period(value: str) -> str | None:
    text = value.lower()
    daily = bool(re.search(r"(?:/\s*day|per\s+day|daily|a\s+day|\bday\s+rate\b)", text))
    annual = bool(re.search(r"(?:/\s*(?:year|yr|annum|pa)|per\s+(?:year|annum)|\bannual(?:ly)?\b|\bpa\b|\bp\.a\.\b)", text))
    if daily and not annual:
        return "daily"
    if annual and not daily:
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
    if period == "annual" and employment_type != "permanent":
        return None
    numbers = [float(value.replace(",", "")) for value in re.findall(r"\d[\d,]*(?:\.\d+)?", text)]
    if not numbers:
        return None
    if re.search(r"\d\s*k\b", text):
        numbers = [number * 1000 if number < 1000 else number for number in numbers]
    return max(numbers)
