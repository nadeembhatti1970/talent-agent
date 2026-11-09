from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

import pandas as pd


class SummaryReportWriterAgent:
    """Compiles the pipeline artifacts into a sortable CSV and linked HTML report."""

    def write(
        self, jobs: list[dict[str, Any]], directory: Path, raw_count: int,
        formats: list[str] | None = None,
        html_filename: str = "tracker.html", csv_filename: str = "tracker.csv",
    ) -> None:
        formats = formats or ["html", "csv"]
        rows = []
        for job in jobs:
            review = job.get("document_review", {})
            if not isinstance(review, dict):
                review = {}
            findings = (
                review.get("cv_findings", [])
                + review.get("cover_letter_findings", [])
                + review.get("non_blocking_notes", [])
            )
            previous = job.get("previous_application")
            rows.append({
                "score": job.get("score", ""), "title": job.get("title", ""),
                "company": job.get("company", ""), "location": job.get("location", ""),
                "employment_type": job.get("employment_type", ""),
                "source": job.get("source", ""),
                "role_family": job.get("role_family", ""),
                "salary": job.get("salary", ""),
                "day_rate": job.get("day_rate", ""),
                "ir35_status": job.get("ir35_status", ""),
                "contract_length": job.get("contract_length", ""),
                "start_date": job.get("start_date", ""),
                "url": job.get("url", ""),
                "application_status": job.get("application_status", ""),
                "previously_applied": (
                    previous.get("last_applied_at", "")
                    if isinstance(previous, dict) else job.get("previously_applied", "")
                ),
                "description_missing": "Yes" if job.get("description_missing") else "",
                "cv_document": job.get("cv_document", ""),
                "cover_letter_document": job.get("cover_letter_document", ""),
                "review_status": (
                    "Approved" if review.get("approved") is True
                    else "Needs attention" if review.get("approved") is False
                    else ""
                ),
                "linkedin_verification": review.get("linkedin_status", ""),
                "review_findings": " | ".join(str(item) for item in findings),
                "rationale": job.get("rationale", ""),
            })
        frame = pd.DataFrame(rows, columns=[
            "score", "title", "company", "location", "employment_type", "source",
            "role_family", "salary", "day_rate", "ir35_status", "contract_length",
            "start_date", "url", "application_status", "previously_applied",
            "description_missing", "cv_document", "cover_letter_document",
            "review_status", "linkedin_verification", "review_findings", "rationale",
        ])
        if "csv" in formats:
            frame.to_csv(directory / csv_filename, index=False)
        escaped = frame.copy()
        for column in escaped.columns:
            escaped[column] = escaped[column].fillna("").astype(str).map(lambda value: value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
        if not escaped.empty:
            escaped["title"] = [f'<a target="_blank" rel="noopener" href="{quote(url, safe=":/?#=&%")}">{title}</a>' if url else title for title, url in zip(escaped["title"], frame["url"].fillna(""))]
            for col in ("cv_document", "cover_letter_document"):
                escaped[col] = escaped[col].map(lambda name: f'<a href="applications/{quote(name)}">{name}</a>' if name else "")
        table = escaped.to_html(index=False, escape=False, classes="jobs")
        contract_count = sum(
            1 for job in jobs if str(job.get("employment_type", "")).lower() == "contract"
        )
        applied_count = sum(
            1 for job in jobs if job.get("application_status") == "applied"
        )
        summary = (
            f"{len(jobs)} roles qualified from {raw_count} listings · "
            f"{contract_count} contract · {applied_count} applied · Generated {directory.name}"
        )
        html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Talent Agent Job Tracker</title><style>
body{{font:15px/1.5 system-ui,sans-serif;margin:2rem auto;padding:0 1rem;max-width:1200px;color:#182230;background:#f6f8fb}}
h1{{margin-bottom:.25rem}}.summary{{color:#526071;margin-bottom:1.5rem}}.table-wrap{{overflow:auto;background:white;border-radius:12px;padding:1rem;box-shadow:0 2px 12px #15223812}}
table{{border-collapse:collapse;width:100%;min-width:800px}}th,td{{padding:.65rem;text-align:left;border-bottom:1px solid #e5eaf0;vertical-align:top}}th{{background:#eef3f8;position:sticky;top:0}}a{{color:#075fc7}}
</style></head><body><h1>Job Search Tracker</h1><p class="summary">{summary}</p>
<div class="table-wrap">{table}</div></body></html>"""
        if "html" in formats:
            (directory / html_filename).write_text(html, encoding="utf-8")
