from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import requests

from .llm import OpenRouterAgent


def normalize_jobs(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in records:
        url = str(item.get("url") or item.get("job_url") or item.get("link") or "").strip()
        title = str(item.get("title") or item.get("position") or item.get("jobTitle") or "Untitled role").strip()
        company = str(item.get("company") or item.get("companyName") or "Unknown company").strip()
        description = str(item.get("description") or item.get("descriptionText") or item.get("jobDescription") or item.get("descriptionHtml") or "").strip()
        raw_type = str(item.get("employment_type") or item.get("employmentType") or item.get("contract_type") or item.get("jobType") or item.get("type") or "").strip()
        employment_type = _employment_type(raw_type, title, description)
        identity = url or f"{company.lower()}|{title.lower()}|{item.get('location', '')}"
        job_id = str(item.get("job_id") or item.get("id") or hashlib.sha256(identity.encode()).hexdigest()[:16])
        if identity in seen:
            continue
        seen.add(identity)
        jobs.append({
            "job_id": job_id,
            "title": title,
            "company": company,
            "location": str(item.get("location") or item.get("jobLocation") or "Not specified").strip(),
            "url": url,
            "description": description,
            "salary": item.get("salary") or item.get("salaryRange") or item.get("salaryInfo") or item.get("salaryInsights") or "Not specified",
            "employment_type": employment_type,
            "scraped_at": str(item.get("scraped_at") or datetime.now(timezone.utc).isoformat()),
        })
    return jobs


def _employment_type(raw_type: str, title: str, description: str) -> str:
    raw = raw_type.lower().replace("_", "-")
    title_text = title.lower().replace("_", "-")
    desc = description.lower().replace("_", "-")
    contract_terms = ("contract", "contractor", "day rate", "daily rate", "per day", "/day", "inside ir35", "outside ir35", "fixed-term")
    permanent_terms = ("permanent", "full-time", "full time", "employee")
    if any(word in raw for word in contract_terms):
        return "contract"
    if any(word in raw for word in permanent_terms):
        return "permanent"
    if any(word in title_text for word in contract_terms):
        return "contract"
    if any(word in title_text for word in permanent_terms):
        return "permanent"
    if any(word in desc for word in ("contract role", "contract position", "day rate", "daily rate", "per day", "/day", "inside ir35", "outside ir35", "fixed-term")):
        return "contract"
    if any(word in desc for word in ("permanent role", "full-time", "full time", "annual salary", "per annum", "/year")):
        return "permanent"
    return "unknown"


class ResearcherAgent:
    def __init__(self, model: str | None = None) -> None:
        self.agent = OpenRouterAgent("Researcher", model)

    def normalize(self, records: list[dict[str, Any]], query: str, location: str) -> list[dict[str, Any]]:
        normalized = normalize_jobs(records)
        if not normalized:
            return []
        compact = [{k: job[k] for k in ("job_id", "title", "company", "location", "url", "description", "salary")} for job in normalized]
        prompt = (
            "Review these raw job listings for relevance to the search. Preserve every factual field and never invent details. "
            "Drop records that are clearly not job vacancies or are missing a meaningful description. Return a JSON array of the "
            "kept records, retaining their job_id and all fields. Search: " + query + "; location: " + location + "\n\n" + str(compact)
        )
        try:
            import json
            result = json.loads(self.agent.complete("You normalize job search data carefully. Output JSON only.", prompt, json_output=True))
            if isinstance(result, list):
                # Keep original timestamp and ensure model did not fabricate new identities.
                original = {job["job_id"]: job for job in normalized}
                kept = []
                for record in result:
                    source = original.get(str(record.get("job_id", "")))
                    if source:
                        kept.append(source)
                return kept
        except Exception:
            pass
        return normalized


def fetch_apify_jobs(keywords: str, location: str, date_posted: str, max_results: int) -> list[dict[str, Any]]:
    token = os.getenv("APIFY_API_TOKEN")
    if not token:
        raise RuntimeError("APIFY_API_TOKEN is required for --search; alternatively provide --jobs JSON.")
    actor_id = os.getenv("APIFY_ACTOR_ID", "").strip()
    if not actor_id:
        raise RuntimeError("APIFY_ACTOR_ID is required for --search; set it to an Apify actor you can access.")
    # Apify accepts owner~actor as the canonical ID. Convert the common
    # owner/actor Store URL form so it remains one path segment.
    if "/" in actor_id and "~" not in actor_id:
        owner, actor_name = actor_id.split("/", 1)
        actor_id = f"{owner}~{actor_name}"
    endpoint = f"https://api.apify.com/v2/actors/{quote(actor_id, safe='~')}/run-sync-get-dataset-items"
    api_date = {"past_24_hours": "past24Hours", "past_week": "pastWeek"}.get(date_posted, "anyTime")
    response = requests.post(
        endpoint,
        params={"format": "json", "clean": "true", "limit": max_results, "maxItems": max_results},
        headers={"Authorization": f"Bearer {token}"},
        json={
            "keywords": keywords,
            "location": location,
            "datePosted": api_date,
            "limitPerSource": max_results,
            "scrapeCompany": False,
        },
        timeout=180,
    )
    if response.status_code == 404:
        raise RuntimeError(f"Apify returned 404 for actor '{actor_id}'. Check that APIFY_ACTOR_ID names an actor available to your account.")
    if response.status_code in (401, 403):
        raise RuntimeError(f"Apify rejected the credentials (HTTP {response.status_code}). Check the rotated APIFY_API_TOKEN and actor access.")
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError("Apify actor returned an unexpected payload; expected a JSON array")
    return payload
