from __future__ import annotations

import json
from typing import Any, Callable

from .llm import ModelResponseError, OpenRouterAgent


class CareerAnalystAgent:
    def __init__(self, model: str | None = None) -> None:
        self.model = model
        self.agent: OpenRouterAgent | None = None

    def evaluate(
        self,
        jobs: list[dict[str, Any]],
        cv: str,
        candidate: dict[str, Any],
        minimum_score: float = 70.0,
        *,
        previous_results: list[dict[str, Any]] | None = None,
        on_checkpoint: Callable[[list[dict[str, Any]]], None] | None = None,
    ) -> list[dict[str, Any]]:
        if not jobs:
            return []
        batch_size = 3
        batches = [jobs[index:index + batch_size] for index in range(0, len(jobs), batch_size)]
        source = {job["job_id"]: job for job in jobs}
        evaluations: dict[str, dict[str, Any]] = {}
        for evaluation in previous_results or []:
            job_id = str(evaluation.get("job_id", ""))
            if job_id not in source:
                continue
            try:
                score = max(0.0, min(100.0, float(evaluation["score"])))
            except (KeyError, TypeError, ValueError):
                continue
            evaluations[job_id] = {**evaluation, "score": round(score, 1)}

        def score_batch(
            batch: list[dict[str, Any]], batch_label: str, retry_missing: bool = True
        ) -> None:
            pending_ids = {job["job_id"] for job in batch}
            compact_jobs = []
            for job in batch:
                compact = {key: job.get(key, "") for key in (
                    "job_id", "title", "company", "location", "salary", "employment_type", "description",
                )}
                description = str(compact["description"])
                if len(description) > 6000:
                    compact["description"] = description[:5000] + "\n…[middle omitted]…\n" + description[-1000:]
                compact_jobs.append(compact)
            prompt = f"""Assess each job against the candidate's verified CV and preferences.
Use a 0-100 score with three weighted dimensions: technical overlap 50%, seniority 25%, compensation alignment 25%. If compensation is absent, score that dimension neutrally at 50. Do not infer candidate skills beyond the CV. Return JSON only as an object with one key, "assessments", whose value is an array of objects. Each assessment must include job_id, score (number), tech_overlap, seniority_match, compensation_alignment (0-100), rationale (at most two short sentences), and missing_requirements (at most five strings). Keep every assessment concise and include all IDs.

Minimum threshold: {minimum_score}. Candidate preferences: {json.dumps(candidate, ensure_ascii=False)}
Candidate CV: {cv}
Jobs: {json.dumps(compact_jobs, ensure_ascii=False)}"""
            try:
                if self.agent is None:
                    self.agent = OpenRouterAgent("Career Analyst", self.model)
                raw = self.agent.complete(
                    "You are a careful career analyst. Return valid JSON only.",
                    prompt,
                    json_output=True,
                )
            except ModelResponseError as exc:
                if len(batch) == 1:
                    raise
                midpoint = len(batch) // 2
                print(
                    f"      ↻ Career Analyst response was truncated or invalid ({exc.finish_reason}); "
                    f"retrying this batch as smaller requests.",
                    flush=True,
                )
                score_batch(batch[:midpoint], f"{batch_label}a")
                score_batch(batch[midpoint:], f"{batch_label}b")
                return

            results = json.loads(raw)
            if isinstance(results, dict):
                results = results.get("assessments")
            if not isinstance(results, list):
                raise ValueError(
                    f"Career Analyst {batch_label} response must contain an assessments array"
                )
            batch_scored = 0
            for evaluation in results:
                if not isinstance(evaluation, dict):
                    continue
                job_id = str(evaluation.get("job_id", ""))
                if job_id not in pending_ids:
                    continue
                try:
                    score = max(0.0, min(100.0, float(evaluation["score"])))
                except (KeyError, TypeError, ValueError):
                    continue
                evaluations[job_id] = {**evaluation, "score": round(score, 1)}
                batch_scored += 1
            print(f"      ✓ Scored {batch_scored}/{len(batch)} roles in batch {batch_label}.", flush=True)
            if on_checkpoint:
                on_checkpoint(list(evaluations.values()))
            missing_jobs = [job for job in batch if job["job_id"] not in evaluations]
            if missing_jobs:
                missing_ids = sorted({job["job_id"] for job in missing_jobs})
                if retry_missing and len(missing_jobs) == len(batch) and len(batch) > 1:
                    midpoint = len(batch) // 2
                    print(
                        f"      ↻ Career Analyst omitted all requested job IDs in batch "
                        f"{batch_label}; retrying as smaller requests.",
                        flush=True,
                    )
                    score_batch(batch[:midpoint], f"{batch_label}a")
                    score_batch(batch[midpoint:], f"{batch_label}b")
                    return
                if retry_missing:
                    print(
                        f"      ↻ Career Analyst omitted {len(missing_jobs)} assessment(s) "
                        f"in batch {batch_label}; retrying those listings.",
                        flush=True,
                    )
                    score_batch(missing_jobs, f"{batch_label}-retry", retry_missing=False)
                    return
                raise ValueError(
                    "Career Analyst did not return valid assessments for job ID(s): "
                    + ", ".join(missing_ids)
                )

        for batch_index, batch in enumerate(batches, start=1):
            pending = [job for job in batch if job["job_id"] not in evaluations]
            if not pending:
                print(f"   🧠 Career Analyst restored batch {batch_index}/{len(batches)} from saved results.", flush=True)
                continue
            print(f"   🧠 Career Analyst is assessing batch {batch_index}/{len(batches)} ({len(pending)} roles)…", flush=True)
            score_batch(pending, str(batch_index))
        scored = [
            {**source[job_id], **evaluation}
            for job_id, evaluation in evaluations.items()
        ]
        return sorted((job for job in scored if job["score"] >= minimum_score), key=lambda job: job["score"], reverse=True)
