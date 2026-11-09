from __future__ import annotations

import os
import sys
from collections import Counter
from typing import Any

import pandas
import requests

from .researcher import fetch_apify_jobs

SOURCES = ("jobspy", "reed", "adzuna", "apify")
DEFAULT_SOURCE = "jobspy"
DEFAULT_LIMIT = 50

_HOURS_OLD = {
    "past_24_hours": 24,
    "past_week": 24 * 7,
    "past_month": 24 * 30,
    "any_time": None,
}


def _hours_old(date_posted: str) -> int | None:
    if date_posted not in _HOURS_OLD:
        raise ValueError(
            "date_posted must be 'past_24_hours', 'past_week', 'past_month', or 'any_time'"
        )
    return _HOURS_OLD[date_posted]


def _required_env(name: str, source: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required when JOB_SOURCE={source}.")
    return value


def _split_employment_type(keywords: str) -> tuple[str, str | None]:
    """Separate the pipeline's trailing employment type from the search text."""
    base, _, trailing = keywords.rpartition(" ")
    kind = trailing.strip().lower()
    if kind in {"contract", "permanent"}:
        return base.strip(), kind
    return keywords.strip(), None


def fetch_jobs(
    keywords: str, location: str, date_posted: str, max_results: int | None
) -> list[dict[str, Any]]:
    """Fetch and interleave raw job records from the configured sources."""
    setting = os.getenv("JOB_SOURCE", DEFAULT_SOURCE).strip().lower() or DEFAULT_SOURCE
    sources = [source.strip() for source in setting.split(",")]
    fetchers = {
        "jobspy": fetch_jobspy_jobs,
        "reed": fetch_reed_jobs,
        "adzuna": fetch_adzuna_jobs,
        "apify": fetch_apify_jobs,
    }
    if any(source not in fetchers for source in sources):
        raise ValueError(
            f"JOB_SOURCE must be a comma-separated list of {', '.join(SOURCES)}; got {setting!r}"
        )
    if len(set(sources)) != len(sources):
        raise ValueError("JOB_SOURCE must not contain duplicate sources")
    results = [
        fetchers[source](keywords, location, date_posted, max_results)
        for source in sources
    ]
    interleaved = [
        jobs[index]
        for index in range(max((len(jobs) for jobs in results), default=0))
        for jobs in results
        if index < len(jobs)
    ]
    counts = Counter(str(job.get("source") or "unknown") for job in interleaved)
    if interleaved:
        print(
            "   📡 Source results: "
            + ", ".join(f"{name}={count}" for name, count in counts.items()),
            flush=True,
        )
    return interleaved


def fetch_jobspy_jobs(
    keywords: str, location: str, date_posted: str, max_results: int | None
) -> list[dict[str, Any]]:
    try:
        from jobspy import scrape_jobs
    except ImportError as exc:
        raise RuntimeError(
            "JOB_SOURCE=jobspy requires python-jobspy: pip install 'talent-agent[jobspy]'"
        ) from exc
    sites = [
        site.strip().lower()
        for site in os.getenv("JOBSPY_SITES", "linkedin,indeed").split(",")
        if site.strip()
    ]
    country = os.getenv("JOBSPY_COUNTRY", "uk").strip() or "uk"
    # The pipeline appends the employment type to the query. LinkedIn ignores JobSpy's job_type
    # filter, so it keeps the word as free text; other sites get a stripped query plus the filter.
    base_terms, _, trailing = keywords.rpartition(" ")
    job_type = {"contract": "contract", "permanent": "fulltime"}.get(trailing.lower())
    hours = _hours_old(date_posted)
    frames = []
    for site in sites:
        kwargs: dict[str, Any] = {
            "site_name": [site],
            "search_term": keywords,
            "location": location or None,
            "results_wanted": max_results or DEFAULT_LIMIT,
            "linkedin_fetch_description": True,
            "country_indeed": country,
            "verbose": 0,
        }
        if site != "linkedin" and job_type:
            kwargs["search_term"] = base_terms
            kwargs["job_type"] = job_type
        if hours is not None:
            kwargs["hours_old"] = hours
        try:
            frames.append(scrape_jobs(**kwargs))
        except Exception as exc:
            # One blocked or failing site must not discard results from the others.
            print(f"   ⚠️ JobSpy {site} search failed: {exc}", file=sys.stderr, flush=True)
    if not frames:
        raise RuntimeError("All JobSpy sites failed; see the warnings above.")
    frame = pandas.concat(frames, ignore_index=True)
    records: list[dict[str, Any]] = []
    for row in frame.to_dict("records"):
        row = {key: (None if value != value else value) for key, value in row.items()}
        salary = None
        if row.get("min_amount") or row.get("max_amount"):
            low, high = row.get("min_amount"), row.get("max_amount")
            amounts = f"{low:g}-{high:g}" if low and high else f"{low or high:g}"
            interval = str(row.get("interval") or "year").lower()
            # LinkedIn often labels day rates as yearly; no real annual salary is this small.
            if interval in {"yearly", "year", "annual", "annually"} and (high or low or 0) < 2000:
                interval = "day"
            salary = f"{row.get('currency') or ''}{amounts} per {interval}"
        job_type = row.get("job_type")
        site = str(row.get("site") or "jobspy").strip().lower()
        records.append({
            "id": f"{site}-{row.get('id')}" if row.get("id") else None,
            "title": row.get("title"),
            "company": row.get("company"),
            "location": row.get("location"),
            "url": row.get("job_url"),
            "description": row.get("description"),
            "employment_type": ", ".join(job_type) if isinstance(job_type, (list, tuple)) else job_type,
            "workplace_arrangement": "remote" if row.get("is_remote") else None,
            "salary": salary,
            "source": site,
        })
    return records


def fetch_reed_jobs(
    keywords: str, location: str, date_posted: str, max_results: int | None
) -> list[dict[str, Any]]:
    from datetime import datetime, timedelta, timezone

    api_key = _required_env("REED_API_KEY", "reed")
    base_keywords, employment_type = _split_employment_type(keywords)
    hours = _hours_old(date_posted)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours) if hours else None
    limit = max_results or DEFAULT_LIMIT
    records: list[dict[str, Any]] = []
    skip = 0
    page_size = min(100, limit)
    while len(records) < limit:
        params: dict[str, Any] = {
            "keywords": base_keywords or keywords,
            "locationName": location,
            "resultsToTake": page_size,
            "resultsToSkip": skip,
        }
        if employment_type == "contract":
            params["contract"] = "true"
        elif employment_type == "permanent":
            params["permanent"] = "true"
        response = requests.get(
            "https://www.reed.co.uk/api/1.0/search",
            params=params,
            auth=(api_key, ""),
            timeout=(10, 30),
        )
        if response.status_code in (401, 403):
            raise RuntimeError(f"Reed rejected the credentials (HTTP {response.status_code}). Check REED_API_KEY.")
        response.raise_for_status()
        results = response.json().get("results")
        if not isinstance(results, list):
            raise ValueError("Reed returned an unexpected payload; expected a results array")
        if not results:
            break
        skip += len(results)
        for item in results:
            if cutoff is not None:
                posted = _parse_reed_date(item.get("date"))
                if posted is not None and posted < cutoff:
                    continue
            low, high = item.get("minimumSalary"), item.get("maximumSalary")
            salary = None
            if low or high:
                amounts = f"{low:g}-{high:g}" if low and high else f"{low or high:g}"
                ceiling = max(value for value in (low, high) if isinstance(value, (int, float)))
                period = "day" if ceiling < 2_000 else "year"
                salary = f"{item.get('currency') or 'GBP'} {amounts} per {period}"
            records.append({
                "id": f"reed-{item.get('jobId')}",
                "title": item.get("jobTitle"),
                "company": item.get("employerName"),
                "location": item.get("locationName"),
                "url": item.get("jobUrl"),
                "description": item.get("jobDescription"),
                "salary": salary,
                "employment_type": employment_type,
                "source": "reed",
            })
        if len(results) < page_size:
            break
    return records[:limit]


def _parse_reed_date(value: Any) -> Any:
    from datetime import datetime, timezone

    try:
        return datetime.strptime(str(value), "%d/%m/%Y").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def fetch_adzuna_jobs(
    keywords: str, location: str, date_posted: str, max_results: int | None
) -> list[dict[str, Any]]:
    app_id = _required_env("ADZUNA_APP_ID", "adzuna")
    app_key = _required_env("ADZUNA_APP_KEY", "adzuna")
    country = os.getenv("ADZUNA_COUNTRY", "gb").strip().lower() or "gb"
    base_keywords, employment_type = _split_employment_type(keywords)
    hours = _hours_old(date_posted)
    limit = max_results or DEFAULT_LIMIT
    per_page = min(50, limit)
    records: list[dict[str, Any]] = []
    page = 1
    while len(records) < limit:
        params: dict[str, Any] = {
            "app_id": app_id,
            "app_key": app_key,
            "what": base_keywords or keywords,
            "results_per_page": per_page,
            "content-type": "application/json",
        }
        if location:
            params["where"] = location
        if hours is not None:
            params["max_days_old"] = max(1, hours // 24)
        if employment_type == "contract":
            params["contract"] = 1
        elif employment_type == "permanent":
            params["permanent"] = 1
        response = requests.get(
            f"https://api.adzuna.com/v1/api/jobs/{country}/search/{page}",
            params=params,
            timeout=(10, 30),
        )
        if response.status_code in (401, 403):
            raise RuntimeError(
                f"Adzuna rejected the credentials (HTTP {response.status_code}). Check ADZUNA_APP_ID and ADZUNA_APP_KEY."
            )
        response.raise_for_status()
        results = response.json().get("results")
        if not isinstance(results, list):
            raise ValueError("Adzuna returned an unexpected payload; expected a results array")
        if not results:
            break
        for item in results:
            low, high = item.get("salary_min"), item.get("salary_max")
            salary = None
            if low or high:
                amounts = f"{low:g}-{high:g}" if low and high else f"{low or high:g}"
                salary = f"{amounts} per year"
            api_type = " ".join(
                str(item.get(key)) for key in ("contract_type", "contract_time") if item.get(key)
            ).strip()
            records.append({
                "id": f"adzuna-{item.get('id')}",
                "title": item.get("title"),
                "company": (item.get("company") or {}).get("display_name"),
                "location": (item.get("location") or {}).get("display_name"),
                "url": item.get("redirect_url"),
                "description": item.get("description"),
                "employment_type": api_type or employment_type,
                "salary": salary,
                "source": "adzuna",
            })
        if len(results) < per_page:
            break
        page += 1
    return records[:limit]
