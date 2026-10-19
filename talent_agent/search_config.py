from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .settings import GITHUB_PROFILE_URL, PROJECT_ROOT

DEFAULT_SEARCH_CONFIG = PROJECT_ROOT / "config" / "search.json"
ALLOWED_EMPLOYMENT_TYPES = {"contract", "permanent"}
ALLOWED_WORKPLACE_ARRANGEMENTS = {"remote", "hybrid", "onsite"}
DEFAULT_OUTPUT_CONFIG = {
    "root_dir": "outputs",
    "dated_subfolders": True,
    "date_format": "%Y-%m-%d",
    "raw_jobs_filename": "raw_jobs.json",
    "prioritized_jobs_filename": "prioritized_jobs.json",
    "tracker_html_filename": "tracker.html",
    "tracker_csv_filename": "tracker.csv",
    "tracker_formats": ["html", "csv"],
    "application_format": "docx",
}


def load_search_config(path: Path = DEFAULT_SEARCH_CONFIG) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("Search configuration must be a JSON object")
    config.setdefault("keywords", [])
    locations = config.get("locations", config.get("location", ""))
    if isinstance(locations, str):
        locations = [locations] if locations.strip() else []
    if not isinstance(locations, list) or not all(isinstance(value, str) for value in locations):
        raise ValueError("Search config 'locations' must be a string or an array of strings")
    config["locations"] = [value.strip() for value in locations if value.strip()] or [""]
    config.pop("location", None)
    config.setdefault("github_profile_url", GITHUB_PROFILE_URL)
    github_url = str(config["github_profile_url"] or GITHUB_PROFILE_URL).strip()
    if github_url:
        parsed_github_url = urlparse(github_url)
        if parsed_github_url.scheme != "https" or parsed_github_url.hostname not in {"github.com", "www.github.com"}:
            raise ValueError("github_profile_url must be an HTTPS GitHub profile URL")
    config["github_profile_url"] = github_url
    config.setdefault("date_posted", "past_week")
    config.setdefault("max_results", 50)
    config.setdefault("employment_types", ["contract", "permanent"])
    config.setdefault("workplace_arrangements", sorted(ALLOWED_WORKPLACE_ARRANGEMENTS))
    if not isinstance(config["workplace_arrangements"], list):
        raise ValueError("Search config 'workplace_arrangements' must be an array")
    arrangement_aliases = {
        "partly remote": "hybrid",
        "partly-remote": "hybrid",
        "partly_remote": "hybrid",
        "on-site": "onsite",
    }
    arrangements = {
        arrangement_aliases.get(str(value).strip().lower(), str(value).strip().lower())
        for value in config["workplace_arrangements"]
    }
    invalid_arrangements = arrangements - ALLOWED_WORKPLACE_ARRANGEMENTS
    if invalid_arrangements:
        raise ValueError(
            "workplace_arrangements may contain only 'remote', 'hybrid' (partly remote), "
            "and/or 'onsite'"
        )
    if not arrangements:
        raise ValueError("Select at least one workplace arrangement")
    config["workplace_arrangements"] = sorted(arrangements)
    config.setdefault("currency", "GBP")
    config.setdefault("minimum_day_rate", None)
    config.setdefault("minimum_annual_salary", None)
    config.setdefault("exclude_unknown_compensation", False)
    config.setdefault("review_documents", True)
    config.setdefault("review_linkedin_profile", True)
    for key in ("review_documents", "review_linkedin_profile"):
        if not isinstance(config[key], bool):
            raise ValueError(f"{key} must be true or false")
    config.setdefault("exclude_security_clearance", False)
    if not isinstance(config["exclude_security_clearance"], bool):
        raise ValueError("exclude_security_clearance must be true or false")
    if (
        "include_unknown_contract_compensation" in config
        and not isinstance(config["include_unknown_contract_compensation"], bool)
    ):
        raise ValueError("include_unknown_contract_compensation must be true or false")
    if not isinstance(config.get("output", {}), dict):
        raise ValueError("Search config 'output' must be an object")
    output = {**DEFAULT_OUTPUT_CONFIG, **config.get("output", {})}
    if output["application_format"] not in {"docx", "md"}:
        raise ValueError("output.application_format must be 'docx' or 'md'")
    if not isinstance(output["tracker_formats"], list) or not set(output["tracker_formats"]).issubset({"html", "csv"}):
        raise ValueError("output.tracker_formats may contain 'html' and/or 'csv'")
    if not output["tracker_formats"]:
        raise ValueError("Select at least one tracker format")
    for key in ("raw_jobs_filename", "prioritized_jobs_filename", "tracker_html_filename", "tracker_csv_filename"):
        name = str(output[key])
        if not name or Path(name).name != name:
            raise ValueError(f"output.{key} must be a filename without a directory")
        output[key] = name
    if not output["root_dir"]:
        raise ValueError("output.root_dir cannot be empty")
    config["output"] = output
    if isinstance(config["keywords"], str):
        config["keywords"] = [config["keywords"]] if config["keywords"].strip() else []
    if not isinstance(config["keywords"], list) or not all(isinstance(value, str) for value in config["keywords"]):
        raise ValueError("Search config 'keywords' must be a string or array of strings")
    if not isinstance(config["employment_types"], list):
        raise ValueError("Search config 'employment_types' must be an array")
    types = {str(value).strip().lower() for value in config["employment_types"]}
    invalid_types = types - ALLOWED_EMPLOYMENT_TYPES
    if invalid_types:
        raise ValueError("employment_types may contain only 'contract' and/or 'permanent'")
    if not types:
        raise ValueError("Select at least one employment type")
    config["employment_types"] = sorted(types)
    if config["date_posted"] not in {"past_24_hours", "past_week"}:
        raise ValueError("date_posted must be 'past_24_hours' or 'past_week'")
    if int(config["max_results"]) < 1:
        raise ValueError("max_results must be at least 1")
    for key in ("minimum_day_rate", "minimum_annual_salary"):
        if config[key] is not None and float(config[key]) < 0:
            raise ValueError(f"{key} cannot be negative")
    return config
