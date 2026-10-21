from __future__ import annotations

import json
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

from .llm import OpenRouterAgent

DOCUMENT_REVIEW_VERSION = 7
MAX_LINKEDIN_PROFILE_BYTES = 2_000_000
MAX_LINKEDIN_PROFILE_TEXT = 12_000


class _ProfileTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.metadata: list[str] = []
        self.ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag in {"script", "style", "noscript", "svg"}:
            self.ignored_depth += 1
        if tag == "meta" and attributes.get("content"):
            name = (attributes.get("property") or attributes.get("name") or "").lower()
            if name in {"og:title", "og:description", "description"}:
                self.metadata.append(str(attributes["content"]))
        elif tag in {"p", "br", "li", "h1", "h2", "h3"} and not self.ignored_depth:
            self.text.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self.ignored_depth:
            self.ignored_depth -= 1
        elif tag in {"p", "li", "h1", "h2", "h3"} and not self.ignored_depth:
            self.text.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.ignored_depth and data.strip():
            self.text.append(data.strip())

    def result(self) -> str:
        text = "\n".join(self.metadata + self.text)
        return "\n".join(line.strip() for line in text.splitlines() if line.strip())[
            :MAX_LINKEDIN_PROFILE_TEXT
        ]


def _valid_linkedin_profile_url(url: str) -> bool:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    return (
        parsed.scheme == "https"
        and hostname in {"linkedin.com", "www.linkedin.com"}
        and parsed.path.startswith("/in/")
    )


def fetch_linkedin_profile(url: str) -> dict[str, str]:
    fetched_at = datetime.now(timezone.utc).isoformat()
    if not _valid_linkedin_profile_url(url):
        return {
            "url": url,
            "status": "unavailable",
            "text": "",
            "reason": "LinkedIn profile URL must be an HTTPS linkedin.com/in/ URL.",
            "fetched_at": fetched_at,
        }

    current_url = url
    for _ in range(4):
        parsed = urlparse(current_url)
        if not _valid_linkedin_profile_url(current_url):
            return {
                "url": url,
                "status": "unavailable",
                "text": "",
                "reason": "LinkedIn redirected outside the public profile URL path.",
                "fetched_at": fetched_at,
            }
        try:
            with requests.get(
                current_url,
                headers={"User-Agent": "TalentAgent public profile verifier/1.0"},
                timeout=(5, 20),
                allow_redirects=False,
                stream=True,
            ) as response:
                if response.is_redirect:
                    destination = urljoin(current_url, response.headers.get("Location", ""))
                    if not destination or not _valid_linkedin_profile_url(destination):
                        return {
                            "url": url,
                            "status": "unavailable",
                            "text": "",
                            "reason": "LinkedIn redirected to a sign-in or non-profile page.",
                            "fetched_at": fetched_at,
                        }
                    current_url = destination
                    continue
                response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    remaining = MAX_LINKEDIN_PROFILE_BYTES - len(body)
                    body.extend(chunk[:remaining])
                    if len(body) >= MAX_LINKEDIN_PROFILE_BYTES:
                        break
                encoding = response.encoding or "utf-8"
                html = bytes(body).decode(encoding, errors="replace")
                parser = _ProfileTextParser()
                parser.feed(html)
                profile_text = parser.result()
                if not profile_text or "sign in" in profile_text[:500].lower():
                    return {
                        "url": url,
                        "status": "unavailable",
                        "text": "",
                        "reason": "LinkedIn did not return readable public profile content.",
                        "fetched_at": fetched_at,
                    }
                return {
                    "url": url,
                    "status": "available",
                    "text": profile_text,
                    "reason": "",
                    "fetched_at": fetched_at,
                }
        except requests.RequestException as exc:
            return {
                "url": url,
                "status": "unavailable",
                "text": "",
                "reason": f"Could not fetch the public LinkedIn profile: {exc}",
                "fetched_at": fetched_at,
            }

    return {
        "url": url,
        "status": "unavailable",
        "text": "",
        "reason": f"Too many redirects while fetching LinkedIn profile {parsed.path}.",
        "fetched_at": fetched_at,
    }


class DocumentReviewerAgent:
    def __init__(self, model: str | None = None) -> None:
        self.agent = OpenRouterAgent("Document Reviewer", model)

    def review(
        self,
        cv: str,
        cover_letter: str,
        master_cv: str,
        job: dict[str, Any],
        linkedin_profile: dict[str, str],
        *,
        github_url: str = "",
    ) -> dict[str, Any]:
        linkedin_status = linkedin_profile.get("status", "unavailable")
        linkedin_text = linkedin_profile.get("text", "")
        response = self.agent.complete(
            "Review factual accuracy only. Missing qualifications, experience, or role fit are not errors and "
            "must not block drafts. Never invent or imply unsupported candidate experience. Flag only material "
            "unsupported/contradictory claims, misleading employment history, or wrong role/company details. "
            "The master CV is authoritative; LinkedIn is supplementary and may be incomplete. Return compact "
            "JSON only.",
            f"""Review application drafts for {job.get("title", "")} at {job.get("company", "")}.

MASTER CV:
{master_cv}

LINKEDIN ({linkedin_status}; supplementary evidence):
{linkedin_text}

CONFIGURED GitHub URL (authorized contact): {github_url or "not configured"}

CV DRAFT:
{cv}

COVER LETTER DRAFT:
{cover_letter}

Return exactly this compact JSON shape:
{{"cv_findings": ["blocking factual issue"], "cover_letter_findings": ["blocking factual issue"], "non_blocking_notes": ["optional role-fit gap"], "summary": "brief result"}}
The findings arrays block approval and may contain only material factual errors. Missing or weak evidence for any job requirement, generic tailoring, or lack of a qualification is non-blocking; mention it only in non_blocking_notes and never ask the writer to imply unsupported experience. Keep each array to at most three concise items; each item and summary must be under 20 words. Only flag an omitted role, title, or date if explicitly present in the supplied evidence and its omission materially misrepresents experience. Never infer roles or dates from timeline gaps. A configured GitHub URL is authorized contact information. Do not flag missing LinkedIn evidence when unavailable.""",
            json_output=True,
            max_tokens=10000,
        )
        result = json.loads(response)
        if not isinstance(result, dict):
            raise ValueError("Document Reviewer response must be a JSON object")
        for key in ("cv_findings", "cover_letter_findings"):
            findings = result.get(key)
            if not isinstance(findings, list) or not all(isinstance(item, str) for item in findings):
                raise ValueError(f"Document Reviewer response must contain a string array for '{key}'")
        notes = result.get("non_blocking_notes", [])
        if not isinstance(notes, list) or not all(isinstance(item, str) for item in notes):
            raise ValueError("Document Reviewer response must contain a string array for 'non_blocking_notes'")
        result["non_blocking_notes"] = notes
        if not isinstance(result.get("summary"), str):
            raise ValueError("Document Reviewer response must contain a string 'summary'")
        result["approved"] = not result["cv_findings"] and not result["cover_letter_findings"]
        result["linkedin_status"] = linkedin_status
        result["linkedin_note"] = linkedin_profile.get("reason", "")
        return result
