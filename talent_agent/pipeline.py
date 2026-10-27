from __future__ import annotations

import hashlib
import json
import os
from datetime import date
from pathlib import Path
from typing import Any

from .analyst import CareerAnalystAgent
from .documents import (
    APPLICATION_PROMPT_VERSION,
    DocumentReviewRejectedError,
    generate_application_docs,
    read_cv,
)
from .filters import filter_jobs
from .llm import ModelResponseError, is_safety_metadata_only, usage_tracker
from .memory import read_json_object, write_json
from .reporter import SummaryReportWriterAgent
from .researcher import (
    RESEARCHER_OUTPUT_VERSION,
    ResearcherAgent,
    fetch_apify_jobs,
    normalize_jobs,
)
from .reviewer import (
    DOCUMENT_REVIEW_VERSION,
    DocumentReviewerAgent,
    fetch_linkedin_profile,
)


def run_pipeline(
    *, jobs_path: Path | None, search: bool, cv_path: Path,
    resume_from: Path | None = None,
    candidate_path: Path | None = None, keywords: list[str] | None = None,
    location: str = "", locations: list[str] | None = None,
    date_posted: str = "past_week", max_results: int | None = 50,
    minimum_score: float = 70.0,
    employment_types: list[str] | None = None, currency: str = "GBP",
    workplace_arrangements: list[str] | None = None,
    minimum_day_rate: float | None = None, minimum_annual_salary: float | None = None,
    exclude_unknown_compensation: bool = False, exclude_security_clearance: bool = False,
    include_unknown_contract_compensation: bool | None = None,
    linkedin_url: str = "", github_url: str = "",
    review_documents: bool = True, review_linkedin_profile: bool = True,
    output_root: Path = Path("outputs"), run_id: str = "",
    dated_subfolders: bool = True, date_format: str = "%Y-%m-%d",
    raw_jobs_filename: str = "raw_jobs.json", prioritized_jobs_filename: str = "prioritized_jobs.json",
    tracker_html_filename: str = "tracker.html", tracker_csv_filename: str = "tracker.csv",
    tracker_formats: list[str] | None = None, application_format: str = "docx",
    model: str | None = None,
    allow_model_change_on_resume: bool = False,
) -> Path:
    def say(message: str) -> None:
        print(message, flush=True)

    if not cv_path.is_file():
        raise FileNotFoundError(f"CV file not found: {cv_path}")
    selected_sources = int(search) + int(jobs_path is not None) + int(resume_from is not None)
    if selected_sources != 1:
        raise ValueError("Choose exactly one of --search, --jobs, or --resume")
    cv = read_cv(cv_path)
    if not cv.strip():
        raise ValueError("CV file is empty")
    search_locations = locations if locations is not None else ([location] if location.strip() else [""])
    search_locations = [value.strip() for value in search_locations if value.strip()] or [""]
    model_name = model or os.getenv("OPENROUTER_MODEL", "openrouter/free")
    usage_tracker.reset()
    say(f"\n🚀 Talent Agent is starting with {model_name}.")
    say(f"📄 Candidate CV loaded from {cv_path}.")
    candidate: dict[str, Any] = {
        "name": "", "linkedin_url": linkedin_url, "github_url": "",
        "minimum_match_score": minimum_score, "summary": "",
    }
    if candidate_path:
        candidate.update(json.loads(candidate_path.read_text(encoding="utf-8")))
    if linkedin_url:
        candidate["linkedin_url"] = linkedin_url
    if github_url.strip():
        candidate["github_url"] = github_url.strip()
    github_url = str(candidate.get("github_url", "")).strip()
    candidate["review_documents"] = review_documents
    candidate["review_linkedin_profile"] = review_linkedin_profile
    candidate["locations"] = search_locations
    if minimum_day_rate is not None:
        candidate["minimum_day_rate"] = minimum_day_rate
    if minimum_annual_salary is not None:
        candidate["minimum_annual_salary"] = minimum_annual_salary
    candidate["currency"] = currency
    candidate["employment_types"] = employment_types or ["contract", "permanent"]
    candidate["workplace_arrangements"] = workplace_arrangements or ["hybrid", "onsite", "remote"]
    state_path: Path | None = None
    if resume_from is not None:
        output_dir = resume_from.expanduser().resolve()
        if not output_dir.is_dir():
            raise FileNotFoundError(f"Resume directory not found: {output_dir}")
        state_path = output_dir / "agent_outputs" / "run_state.json"
        if state_path.is_file():
            run_state = read_json_object(state_path)
            if run_state.get("version") != 1:
                raise ValueError(f"Unsupported resume state version in {state_path}")
            artifacts = run_state.get("artifacts", {})
            if not isinstance(artifacts, dict):
                raise ValueError(f"Invalid artifact settings in {state_path}")
            raw_jobs_filename = str(artifacts.get("raw_jobs_filename", raw_jobs_filename))
            prioritized_jobs_filename = str(artifacts.get("prioritized_jobs_filename", prioritized_jobs_filename))
            tracker_html_filename = str(artifacts.get("tracker_html_filename", tracker_html_filename))
            tracker_csv_filename = str(artifacts.get("tracker_csv_filename", tracker_csv_filename))
            tracker_formats = artifacts.get("tracker_formats", tracker_formats)
            application_format = str(artifacts.get("application_format", application_format))
        else:
            run_state = {}
    else:
        run_name = date.today().strftime(date_format)
        if run_id:
            run_name = f"{run_name}_{run_id}"
        output_dir = output_root / run_name if dated_subfolders else output_root
        if run_id and not dated_subfolders:
            output_dir = output_dir / run_id
        run_state = {}
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "applications").mkdir(exist_ok=True)
    memory_dir = output_dir / "agent_outputs"
    memory_dir.mkdir(exist_ok=True)
    if resume_from is not None:
        raw_path = output_dir / raw_jobs_filename
        if not raw_path.is_file():
            raise FileNotFoundError(f"Cannot resume: saved raw jobs file not found: {raw_path}")
        raw_jobs = json.loads(raw_path.read_text(encoding="utf-8"))
        if not isinstance(raw_jobs, list) or not all(isinstance(item, dict) for item in raw_jobs):
            raise ValueError(f"Saved jobs must be a JSON array of objects: {raw_path}")
        say(f"\n⏯️ Resuming saved run in {output_dir}…")
    else:
        if search:
            location_results: list[list[dict[str, Any]]] = []
            for search_location in search_locations:
                limit_text = (
                    f"up to {max_results} results"
                    if max_results is not None
                    else "without a configured result limit"
                )
                say(
                    f"\n🔎 Researcher is searching Apify for {', '.join(keywords or [])} "
                    f"in {search_location or 'any location'} ({limit_text})…"
                )
                location_jobs = fetch_apify_jobs(
                    ", ".join(keywords or []), search_location, date_posted, max_results
                )
                location_results.append(
                    location_jobs if max_results is None else location_jobs[:max_results]
                )
            raw = [
                jobs[index]
                for index in range(
                    max((len(jobs) for jobs in location_results), default=0)
                    if max_results is None
                    else max_results
                )
                for jobs in location_results
                if index < len(jobs)
            ]
        else:
            assert jobs_path is not None
            say(f"\n🔎 Researcher is loading job listings from {jobs_path}…")
            raw = json.loads(jobs_path.read_text(encoding="utf-8"))
            if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
                raise ValueError("Jobs input must be a JSON array of objects")
        if max_results is not None and not search and len(raw) > max_results:
            raw = raw[:max_results]
        raw_jobs = normalize_jobs(raw)
        if max_results is not None and len(raw_jobs) > max_results:
            raw_jobs = raw_jobs[:max_results]
        raw_path = output_dir / raw_jobs_filename
        write_json(raw_path, raw_jobs)
    say(f"   Found {len(raw_jobs)} unique job listing(s).")
    say(f"   🗂️ Raw listings saved to {output_dir / raw_jobs_filename}")

    researcher_path = memory_dir / "researcher.json"
    analyst_path = memory_dir / "career_analyst.json"
    cv_editor_path = memory_dir / "cv_editor.json"
    cover_letter_path = memory_dir / "cover_letter_writer.json"
    document_reviewer_path = memory_dir / "document_reviewer.json"
    linkedin_profile_path = memory_dir / "linkedin_profile.json"
    profile_url = str(candidate.get("linkedin_url", "")).strip()
    if review_linkedin_profile and profile_url:
        saved_profile: dict[str, Any] | None = None
        if linkedin_profile_path.is_file():
            cached_profile = read_json_object(linkedin_profile_path)
            if cached_profile.get("url") == profile_url:
                saved_profile = cached_profile
        linkedin_profile = saved_profile or fetch_linkedin_profile(profile_url)
        if saved_profile is None:
            write_json(linkedin_profile_path, linkedin_profile)
        if linkedin_profile.get("status") != "available":
            say(
                "   ⚠ LinkedIn profile could not be verified from public page content: "
                f"{linkedin_profile.get('reason', 'no profile content available')}"
            )
    else:
        linkedin_profile = {
            "url": profile_url,
            "status": "disabled" if not review_linkedin_profile else "not_provided",
            "text": "",
            "reason": (
                "LinkedIn profile fetching is disabled."
                if not review_linkedin_profile
                else "No LinkedIn profile URL was provided."
            ),
            "fetched_at": "",
        }
    linkedin_profile_sha256 = hashlib.sha256(
        json.dumps(
            {
                "status": linkedin_profile.get("status"),
                "text": linkedin_profile.get("text", ""),
                "reason": linkedin_profile.get("reason", ""),
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    expected_context = {
        "cv_sha256": hashlib.sha256(cv.encode("utf-8")).hexdigest(),
        "candidate_sha256": hashlib.sha256(
            json.dumps(candidate, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest(),
        "raw_jobs_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
        "linkedin_profile_sha256": linkedin_profile_sha256,
    }
    if resume_from is not None and run_state:
        saved_context = run_state.get("context")
        if not isinstance(saved_context, dict):
            raise ValueError(f"Invalid saved run context in {state_path}")
        if (
            saved_context.get("cv_sha256") != expected_context["cv_sha256"]
            or saved_context.get("raw_jobs_sha256") != expected_context["raw_jobs_sha256"]
        ):
            raise ValueError(
                "Cannot resume because the saved CV or raw jobs differ from this run. "
                "Use the original inputs or start a new run."
            )
        if run_state.get("model") != model_name:
            if not allow_model_change_on_resume:
                raise ValueError(
                    f"Cannot resume with model '{model_name}'; the saved run used '{run_state.get('model')}'. "
                    "Pass --allow-model-change-on-resume to rerun model-backed stages from saved listings."
                )
            say(
                f"   Model changed from {run_state.get('model')} to {model_name}; "
                "rerunning model-backed stages from the saved listings."
            )
            for checkpoint_path, checkpoint in (
                (
                    researcher_path,
                    {"version": RESEARCHER_OUTPUT_VERSION, "status": "pending", "jobs": []},
                ),
                (analyst_path, {"version": 1, "status": "in_progress", "evaluations": []}),
                (cv_editor_path, {
                    "version": 1, "prompt_version": APPLICATION_PROMPT_VERSION, "outputs": {},
                }),
                (cover_letter_path, {
                    "version": 1, "prompt_version": APPLICATION_PROMPT_VERSION, "outputs": {},
                }),
                (document_reviewer_path, {
                    "version": 1, "prompt_version": DOCUMENT_REVIEW_VERSION, "outputs": {},
                }),
            ):
                write_json(checkpoint_path, checkpoint)
            stages = run_state.setdefault("stages", {})
            if not isinstance(stages, dict):
                raise ValueError(f"Invalid stage progress in {state_path}")
            for stage in (
                "researcher", "career_analyst", "cv_editor", "cover_letter_writer",
                "document_reviewer", "report",
            ):
                stages[stage] = "pending"
            run_state["model"] = model_name
            write_json(state_path, run_state)
        candidate_changed = (
            saved_context.get("candidate_sha256") != expected_context["candidate_sha256"]
        )
        profile_changed = (
            saved_context.get("linkedin_profile_sha256")
            != expected_context["linkedin_profile_sha256"]
        )
        if candidate_changed:
            say(
                "   Candidate preferences changed; re-running Career Analyst and regenerating "
                "application documents from the saved job listings."
            )
            write_json(
                analyst_path,
                {"version": 1, "status": "in_progress", "evaluations": []},
            )
            for checkpoint_path in (cv_editor_path, cover_letter_path):
                write_json(
                    checkpoint_path,
                    {
                        "version": 1,
                        "prompt_version": APPLICATION_PROMPT_VERSION,
                        "outputs": {},
                    },
                )
            write_json(
                document_reviewer_path,
                {
                    "version": 1,
                    "prompt_version": DOCUMENT_REVIEW_VERSION,
                    "outputs": {},
                },
            )
            stages = run_state.get("stages")
            if isinstance(stages, dict):
                for stage in (
                    "career_analyst", "cv_editor", "cover_letter_writer",
                    "document_reviewer", "report",
                ):
                    stages[stage] = "pending"
            run_state["context"] = expected_context
            write_json(state_path, run_state)
        elif profile_changed:
            say(
                "   LinkedIn evidence changed; regenerating application documents and "
                "re-running the document review from saved job listings."
            )
            for checkpoint_path, prompt_version in (
                (cv_editor_path, APPLICATION_PROMPT_VERSION),
                (cover_letter_path, APPLICATION_PROMPT_VERSION),
                (document_reviewer_path, DOCUMENT_REVIEW_VERSION),
            ):
                write_json(
                    checkpoint_path,
                    {"version": 1, "prompt_version": prompt_version, "outputs": {}},
                )
            stages = run_state.get("stages")
            if isinstance(stages, dict):
                for stage in ("cv_editor", "cover_letter_writer", "document_reviewer", "report"):
                    stages[stage] = "pending"
            run_state["context"] = expected_context
            write_json(state_path, run_state)
    if not run_state:
        run_state = {
            "version": 1,
            "model": model_name,
            "context": expected_context,
            "artifacts": {
                "raw_jobs_filename": raw_jobs_filename,
                "prioritized_jobs_filename": prioritized_jobs_filename,
                "tracker_html_filename": tracker_html_filename,
                "tracker_csv_filename": tracker_csv_filename,
                "tracker_formats": tracker_formats,
                "application_format": application_format,
            },
            "stages": {},
        }
    stages = run_state.setdefault("stages", {})
    if not isinstance(stages, dict):
        raise ValueError(f"Invalid stage progress in {state_path or memory_dir / 'run_state.json'}")

    def set_stage(name: str, status: str) -> None:
        stages[name] = status
        write_json(state_path or memory_dir / "run_state.json", run_state)

    if resume_from is None:
        write_json(
            researcher_path,
            {"version": RESEARCHER_OUTPUT_VERSION, "status": "pending", "jobs": []},
        )
        write_json(analyst_path, {"version": 1, "status": "in_progress", "evaluations": []})
        write_json(cv_editor_path, {
            "version": 1, "prompt_version": APPLICATION_PROMPT_VERSION, "outputs": {},
        })
        write_json(cover_letter_path, {
            "version": 1, "prompt_version": APPLICATION_PROMPT_VERSION, "outputs": {},
        })
        write_json(document_reviewer_path, {
            "version": 1, "prompt_version": DOCUMENT_REVIEW_VERSION, "outputs": {},
        })
    else:
        for checkpoint_path, default in (
            (
                researcher_path,
                {"version": RESEARCHER_OUTPUT_VERSION, "status": "pending", "jobs": []},
            ),
            (analyst_path, {"version": 1, "status": "in_progress", "evaluations": []}),
            (cv_editor_path, {
                "version": 1, "prompt_version": APPLICATION_PROMPT_VERSION, "outputs": {},
            }),
            (cover_letter_path, {
                "version": 1, "prompt_version": APPLICATION_PROMPT_VERSION, "outputs": {},
            }),
            (document_reviewer_path, {
                "version": 1, "prompt_version": DOCUMENT_REVIEW_VERSION, "outputs": {},
            }),
        ):
            if not checkpoint_path.exists():
                write_json(checkpoint_path, default)
    write_json(state_path or memory_dir / "run_state.json", run_state)

    researcher_data = read_json_object(researcher_path)
    cached_relevant = researcher_data.get("jobs")
    if (
        researcher_data.get("status") == "complete"
        and researcher_data.get("version") == RESEARCHER_OUTPUT_VERSION
    ):
        if not isinstance(cached_relevant, list) or not all(isinstance(job, dict) for job in cached_relevant):
            raise ValueError(f"Invalid saved Researcher output: {researcher_path}")
        relevant_jobs = cached_relevant
        say(f"\n🧭 Researcher restored {len(relevant_jobs)} listing(s) from saved output.")
    else:
        set_stage("researcher", "in_progress")
        say("\n🧭 Researcher is checking listing quality and search relevance…")
        researcher = ResearcherAgent(model)
        relevant_jobs = researcher.normalize(
            raw_jobs,
            ", ".join(keywords or []) or "all provided roles",
            ", ".join(search_locations) or "any location",
        )
        write_json(
            researcher_path,
            {
                "version": RESEARCHER_OUTPUT_VERSION,
                "status": "complete",
                "jobs": relevant_jobs,
            },
        )
        set_stage("researcher", "complete")
    say(f"   {len(relevant_jobs)} listing(s) passed the relevance review.")
    filter_config = {
        "employment_types": employment_types or ["contract", "permanent"],
        "workplace_arrangements": workplace_arrangements or ["hybrid", "onsite", "remote"],
        "minimum_day_rate": minimum_day_rate,
        "minimum_annual_salary": minimum_annual_salary,
        "exclude_unknown_compensation": exclude_unknown_compensation,
        "include_unknown_contract_compensation": (
            include_unknown_contract_compensation
            if include_unknown_contract_compensation is not None
            else not exclude_unknown_compensation
        ),
        "exclude_security_clearance": exclude_security_clearance,
        "currency": currency,
    }
    relevant_jobs = filter_jobs(relevant_jobs, filter_config)
    say(
        f"   {len(relevant_jobs)} listing(s) remain after employment, compensation, "
        "and security-clearance filters."
    )
    analyst_data = read_json_object(analyst_path)
    previous_results = analyst_data.get("evaluations", [])
    if not isinstance(previous_results, list) or not all(isinstance(item, dict) for item in previous_results):
        raise ValueError(f"Invalid saved Career Analyst output: {analyst_path}")
    set_stage("career_analyst", "in_progress")
    analyst = CareerAnalystAgent(model)
    say(f"\n🧠 Career Analyst is scoring {len(relevant_jobs)} listing(s) against your CV (minimum score {minimum_score:.0f})…")
    prioritized = analyst.evaluate(
        relevant_jobs,
        cv,
        candidate,
        minimum_score,
        previous_results=previous_results,
        on_checkpoint=lambda evaluations: write_json(
            analyst_path,
            {"version": 1, "status": "in_progress", "evaluations": evaluations},
        ),
    )
    analyst_data["status"] = "complete"
    analyst_data["evaluations"] = analyst_data.get("evaluations", previous_results)
    # The callback writes each completed batch; persist the completion marker even for empty runs.
    if analyst_path.is_file():
        analyst_data = read_json_object(analyst_path)
    analyst_data = read_json_object(analyst_path)
    analyst_data["status"] = "complete"
    write_json(analyst_path, analyst_data)
    set_stage("career_analyst", "complete")
    say(f"   ✅ {len(prioritized)} role(s) qualified.")
    prioritized_path = output_dir / prioritized_jobs_filename
    write_json(prioritized_path, prioritized)

    cv_editor_data = read_json_object(cv_editor_path)
    cv_outputs = cv_editor_data.get("outputs", {})
    cover_letter_data = read_json_object(cover_letter_path)
    cover_letter_outputs = cover_letter_data.get("outputs", {})
    reviewer_data = read_json_object(document_reviewer_path)
    reviewer_outputs = reviewer_data.get("outputs", {})
    if (
        not isinstance(cv_outputs, dict)
        or not isinstance(cover_letter_outputs, dict)
        or not isinstance(reviewer_outputs, dict)
    ):
        raise ValueError("Saved application-agent outputs must be JSON objects keyed by job ID")
    if cv_editor_data.get("prompt_version") != APPLICATION_PROMPT_VERSION:
        cv_outputs = {}
    if cover_letter_data.get("prompt_version") != APPLICATION_PROMPT_VERSION:
        cover_letter_outputs = {}
    if reviewer_data.get("prompt_version") != DOCUMENT_REVIEW_VERSION:
        reviewer_outputs = {}
    set_stage("cv_editor", "in_progress")
    set_stage("cover_letter_writer", "in_progress")
    if review_documents:
        set_stage("document_reviewer", "in_progress")
    else:
        set_stage("document_reviewer", "complete")
    document_reviewer: DocumentReviewerAgent | None = None
    unapproved_applications = 0
    incomplete_applications = 0
    for index, job in enumerate(prioritized, start=1):
        say(f"\n💼 Application {index}/{len(prioritized)} · {job['title']} — {job['company']} (match {job['score']:.1f})")
        job_id = str(job["job_id"])

        def save_cv(text: str, *, key: str = job_id) -> None:
            cv_outputs[key] = {"text": text}
            write_json(cv_editor_path, {
                "version": 1,
                "prompt_version": APPLICATION_PROMPT_VERSION,
                "outputs": cv_outputs,
            })

        def save_letter(text: str, *, key: str = job_id) -> None:
            cover_letter_outputs[key] = {"text": text}
            write_json(cover_letter_path, {
                "version": 1,
                "prompt_version": APPLICATION_PROMPT_VERSION,
                "outputs": cover_letter_outputs,
            })

        def save_review(record: dict[str, Any], *, key: str = job_id) -> None:
            reviewer_outputs[key] = record
            write_json(document_reviewer_path, {
                "version": 1,
                "prompt_version": DOCUMENT_REVIEW_VERSION,
                "outputs": reviewer_outputs,
            })

        cv_record = cv_outputs.get(job_id, {})
        letter_record = cover_letter_outputs.get(job_id, {})
        review_record = reviewer_outputs.get(job_id, {})
        cv_text = cv_record.get("text") if isinstance(cv_record, dict) else None
        letter_text = letter_record.get("text") if isinstance(letter_record, dict) else None
        if not isinstance(review_record, dict):
            review_record = None
        if (
            review_documents
            and (review_record is None or review_record.get("status") != "complete")
            and document_reviewer is None
        ):
            document_reviewer = DocumentReviewerAgent(model)
        if not isinstance(cv_text, str) or not cv_text.strip() or is_safety_metadata_only(cv_text):
            cv_text = None
        if (
            not isinstance(letter_text, str)
            or not letter_text.strip()
            or is_safety_metadata_only(letter_text)
        ):
            letter_text = None
        try:
            job.update(
                generate_application_docs(
                    cv, job, candidate, output_dir / "applications", model, application_format,
                    github_url=github_url,
                    cv_text=cv_text, cover_letter_text=letter_text,
                    on_cv_generated=save_cv, on_cover_letter_generated=save_letter,
                    reviewer=document_reviewer if review_documents else None,
                    linkedin_profile=linkedin_profile,
                    review_record=review_record,
                    on_review_progress=save_review,
                )
            )
        except DocumentReviewRejectedError as exc:
            saved_review = reviewer_outputs.get(job_id)
            if (
                not isinstance(saved_review, dict)
                or saved_review.get("status") != "revision_pending"
                or not isinstance(saved_review.get("final_report"), dict)
            ):
                raise
            job["document_review"] = saved_review["final_report"]
            unapproved_applications += 1
            say(
                f"      ⚠ Reviewer did not approve this application; documents are withheld. "
                f"Continuing to the next role. {exc}"
            )
        except ModelResponseError as exc:
            incomplete_applications += 1
            say(
                f"      ⚠ {exc} This application remains checkpointed; "
                "continuing to the next role."
            )
        write_json(prioritized_path, prioritized)
    cv_editor_data["outputs"] = cv_outputs
    cv_editor_data["status"] = "complete"
    cv_editor_data["prompt_version"] = APPLICATION_PROMPT_VERSION
    cover_letter_data["outputs"] = cover_letter_outputs
    cover_letter_data["status"] = "complete"
    cover_letter_data["prompt_version"] = APPLICATION_PROMPT_VERSION
    reviewer_data["outputs"] = reviewer_outputs
    reviewer_pending = review_documents and (
        unapproved_applications or incomplete_applications
    )
    reviewer_data["status"] = "in_progress" if reviewer_pending else "complete"
    reviewer_data["prompt_version"] = DOCUMENT_REVIEW_VERSION
    write_json(cv_editor_path, cv_editor_data)
    write_json(cover_letter_path, cover_letter_data)
    write_json(document_reviewer_path, reviewer_data)
    application_stages_complete = incomplete_applications == 0
    set_stage("cv_editor", "complete" if application_stages_complete else "in_progress")
    set_stage(
        "cover_letter_writer",
        "complete" if application_stages_complete else "in_progress",
    )
    set_stage(
        "document_reviewer",
        "in_progress" if reviewer_pending else "complete",
    )
    set_stage("report", "in_progress")
    say(f"\n📊 Summary Report Writer is building {', '.join(tracker_formats or ['html', 'csv']).upper()} tracker(s)…")
    SummaryReportWriterAgent().write(
        prioritized, output_dir, len(raw_jobs), tracker_formats,
        tracker_html_filename, tracker_csv_filename,
    )
    set_stage("report", "complete")
    if not prioritized:
        say("   No roles met the configured filters and score threshold; tracker files still contain the run summary.")
    if unapproved_applications:
        say(
            f"   ⚠ {unapproved_applications} application(s) remain unapproved and have no final documents. "
            "The run is checkpointed; resume it to apply the saved Reviewer feedback."
        )
    if incomplete_applications:
        say(
            f"   ⚠ {incomplete_applications} application(s) could not finish because a model response failed. "
            "Completed drafts are checkpointed; resume the run to retry the incomplete applications."
        )
    say(f"\n💰 Model usage this run: {usage_tracker.summary()}.")
    say(f"\n🎉 All done. Results are in {output_dir.resolve()}")
    return output_dir
