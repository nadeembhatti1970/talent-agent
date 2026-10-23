from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from talent_agent.analyst import CareerAnalystAgent
from talent_agent.cli import build_parser
from talent_agent.documents import DocumentReviewRejectedError
from talent_agent.llm import ModelResponseError
from talent_agent.pipeline import run_pipeline
from talent_agent.search_config import load_search_config


class ResumeTests(unittest.TestCase):
    def test_search_config_accepts_a_location_list_and_legacy_location(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            config_path = Path(temporary_dir) / "search.json"
            config_path.write_text(
                json.dumps({
                    "locations": ["London", "Manchester"],
                    "github_profile_url": "https://github.com/example-user",
                    "exclude_security_clearance": True,
                    "review_documents": True,
                    "review_linkedin_profile": True,
                    "workplace_arrangements": ["remote", "partly-remote"],
                }),
                encoding="utf-8",
            )
            config = load_search_config(config_path)
            self.assertEqual(config["locations"], ["London", "Manchester"])
            self.assertEqual(config["github_profile_url"], "https://github.com/example-user")
            self.assertTrue(config["exclude_security_clearance"])
            self.assertTrue(config["review_documents"])
            self.assertTrue(config["review_linkedin_profile"])
            self.assertEqual(config["workplace_arrangements"], ["hybrid", "remote"])

            config_path.write_text(json.dumps({"location": "London"}), encoding="utf-8")
            self.assertEqual(load_search_config(config_path)["locations"], ["London"])

            for date_posted in ("past_24_hours", "past_week", "past_month", "any_time"):
                config_path.write_text(
                    json.dumps({"location": "London", "date_posted": date_posted}),
                    encoding="utf-8",
                )
                self.assertEqual(load_search_config(config_path)["date_posted"], date_posted)

            config_path.write_text(
                json.dumps({"location": "London", "date_posted": "past_year"}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "date_posted must be"):
                load_search_config(config_path)

    def test_default_search_config_has_separate_locations_and_contract_review_setting(self) -> None:
        config = load_search_config()

        self.assertGreater(len(config["locations"]), 1)
        self.assertTrue(config["include_unknown_contract_compensation"])
        self.assertTrue(config["review_documents"])
        self.assertTrue(config["review_linkedin_profile"])
        self.assertTrue(config["workplace_arrangements"])
        self.assertTrue(
            set(config["workplace_arrangements"]).issubset({"remote", "hybrid", "onsite"})
        )

    def test_null_max_results_is_accepted_in_search_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            config_path = Path(temporary_dir) / "search.json"
            config_path.write_text(json.dumps({"max_results": None}), encoding="utf-8")

            self.assertIsNone(load_search_config(config_path)["max_results"])

            config_path.write_text(json.dumps({"max_results": 0}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "positive integer or null"):
                load_search_config(config_path)

    def test_search_queries_each_location_and_deduplicates_results(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            cv_path = root / "cv.txt"
            cv_path.write_text("Candidate CV", encoding="utf-8")
            fetch = Mock(return_value=[{
                "id": "role-1",
                "title": "Platform Engineer",
                "company": "Example Ltd",
                "location": "Search result location",
                "url": "https://example.test/jobs/1",
                "employment_type": "permanent",
                "description": "A platform engineering role.",
            }])
            researcher = Mock()
            researcher.normalize.side_effect = lambda jobs, *_: jobs
            analyst = Mock()
            analyst.evaluate.return_value = []
            with (
                patch("talent_agent.pipeline.fetch_apify_jobs", fetch),
                patch("talent_agent.pipeline.ResearcherAgent", return_value=researcher),
                patch("talent_agent.pipeline.CareerAnalystAgent", return_value=analyst),
                patch("talent_agent.pipeline.SummaryReportWriterAgent"),
            ):
                output_dir = run_pipeline(
                    jobs_path=None,
                    search=True,
                    cv_path=cv_path,
                    locations=["London", "Manchester"],
                    keywords=["Platform Engineer"],
                    max_results=4,
                    output_root=root / "outputs",
                    dated_subfolders=False,
                    tracker_formats=["csv"],
                )

            self.assertEqual([call.args[1] for call in fetch.call_args_list], ["London", "Manchester"])
            self.assertEqual([call.args[3] for call in fetch.call_args_list], [4, 4])
            raw_jobs = json.loads((output_dir / "raw_jobs.json").read_text(encoding="utf-8"))
            self.assertEqual(len(raw_jobs), 1)

    def test_search_enforces_global_max_results_even_if_actor_exceeds_requested_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            cv_path = root / "cv.txt"
            cv_path.write_text("Candidate CV", encoding="utf-8")

            def jobs_for(prefix: str) -> list[dict[str, str]]:
                return [
                    {
                        "id": f"{prefix}-{index}",
                        "title": f"Platform Engineer {prefix} {index}",
                        "company": "Example Ltd",
                        "employment_type": "permanent",
                        "description": "Platform engineering role.",
                    }
                    for index in range(5)
                ]

            fetch = Mock(side_effect=[jobs_for("london"), jobs_for("manchester")])
            researcher = Mock()
            researcher.normalize.side_effect = lambda jobs, *_: jobs
            analyst = Mock()
            analyst.evaluate.return_value = []
            with (
                patch("talent_agent.pipeline.fetch_apify_jobs", fetch),
                patch("talent_agent.pipeline.ResearcherAgent", return_value=researcher),
                patch("talent_agent.pipeline.CareerAnalystAgent", return_value=analyst),
                patch("talent_agent.pipeline.SummaryReportWriterAgent"),
            ):
                output_dir = run_pipeline(
                    jobs_path=None, search=True, cv_path=cv_path,
                    locations=["London", "Manchester"], keywords=["Platform Engineer"],
                    max_results=4, output_root=root / "outputs", dated_subfolders=False,
                    tracker_formats=["csv"],
                )

            raw_jobs = json.loads((output_dir / "raw_jobs.json").read_text(encoding="utf-8"))
            self.assertEqual(len(raw_jobs), 4)
            self.assertEqual([call.args[3] for call in fetch.call_args_list], [4, 4])

    def test_search_requests_global_limit_for_each_location_and_caps_interleaved_total(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            cv_path = root / "cv.txt"
            cv_path.write_text("Candidate CV", encoding="utf-8")

            def jobs_for(prefix: str) -> list[dict[str, str]]:
                return [
                    {
                        "id": f"{prefix}-{index}",
                        "title": f"Platform Engineer {prefix} {index}",
                        "company": "Example Ltd",
                        "employment_type": "permanent",
                        "description": "Platform engineering role.",
                    }
                    for index in range(50)
                ]

            fetch = Mock(side_effect=[jobs_for("london"), jobs_for("manchester")])
            researcher = Mock()
            researcher.normalize.side_effect = lambda jobs, *_: jobs
            analyst = Mock()
            analyst.evaluate.return_value = []
            with (
                patch("talent_agent.pipeline.fetch_apify_jobs", fetch),
                patch("talent_agent.pipeline.ResearcherAgent", return_value=researcher),
                patch("talent_agent.pipeline.CareerAnalystAgent", return_value=analyst),
                patch("talent_agent.pipeline.SummaryReportWriterAgent"),
            ):
                output_dir = run_pipeline(
                    jobs_path=None, search=True, cv_path=cv_path,
                    locations=["London", "Manchester"], keywords=["Platform Engineer"],
                    max_results=50, output_root=root / "outputs", dated_subfolders=False,
                    tracker_formats=["csv"],
                )

            raw_jobs = json.loads((output_dir / "raw_jobs.json").read_text(encoding="utf-8"))
            self.assertEqual(len(raw_jobs), 50)
            self.assertEqual([call.args[3] for call in fetch.call_args_list], [50, 50])
            self.assertEqual(sum(job["job_id"].startswith("london-") for job in raw_jobs), 25)
            self.assertEqual(sum(job["job_id"].startswith("manchester-") for job in raw_jobs), 25)

    def test_null_max_results_does_not_cap_each_location_or_combined_results(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            cv_path = root / "cv.txt"
            cv_path.write_text("Candidate CV", encoding="utf-8")

            def jobs_for(prefix: str) -> list[dict[str, str]]:
                return [
                    {
                        "id": f"{prefix}-{index}",
                        "title": f"Platform Engineer {prefix} {index}",
                        "company": "Example Ltd",
                        "employment_type": "permanent",
                        "description": "Platform engineering role.",
                    }
                    for index in range(60)
                ]

            fetch = Mock(side_effect=[jobs_for("london"), jobs_for("manchester")])
            researcher = Mock()
            researcher.normalize.side_effect = lambda jobs, *_: jobs
            analyst = Mock()
            analyst.evaluate.return_value = []
            with (
                patch("talent_agent.pipeline.fetch_apify_jobs", fetch),
                patch("talent_agent.pipeline.ResearcherAgent", return_value=researcher),
                patch("talent_agent.pipeline.CareerAnalystAgent", return_value=analyst),
                patch("talent_agent.pipeline.SummaryReportWriterAgent"),
            ):
                output_dir = run_pipeline(
                    jobs_path=None, search=True, cv_path=cv_path,
                    locations=["London", "Manchester"], keywords=["Platform Engineer"],
                    max_results=None, output_root=root / "outputs", dated_subfolders=False,
                    tracker_formats=["csv"],
                )

            raw_jobs = json.loads((output_dir / "raw_jobs.json").read_text(encoding="utf-8"))
            self.assertEqual(len(raw_jobs), 120)
            self.assertEqual([call.args[3] for call in fetch.call_args_list], [None, None])

    def test_analyst_checkpoints_completed_batches_and_restores_them(self) -> None:
        jobs = [{"job_id": f"job-{index}", "title": f"Role {index}"} for index in range(4)]
        analyst = CareerAnalystAgent.__new__(CareerAnalystAgent)
        analyst.agent = Mock()
        analyst.agent.complete.side_effect = [
            json.dumps([{"job_id": job["job_id"], "score": 80} for job in jobs[:3]]),
            json.dumps([{"job_id": jobs[3]["job_id"], "score": 90}]),
        ]
        checkpoints: list[list[dict[str, object]]] = []

        result = analyst.evaluate(
            jobs, "cv", {}, previous_results=[],
            on_checkpoint=lambda evaluations: checkpoints.append(evaluations),
        )

        self.assertEqual(len(result), 4)
        self.assertEqual(len(checkpoints), 2)
        self.assertEqual(len(checkpoints[0]), 3)
        self.assertEqual(len(checkpoints[1]), 4)

        restored_analyst = CareerAnalystAgent.__new__(CareerAnalystAgent)
        restored_analyst.agent = Mock()
        restored = restored_analyst.evaluate(jobs, "cv", {}, previous_results=checkpoints[-1])

        self.assertEqual([job["job_id"] for job in restored], ["job-3", "job-0", "job-1", "job-2"])
        restored_analyst.agent.complete.assert_not_called()

    def test_analyst_accepts_json_object_with_assessments_array(self) -> None:
        job = {"job_id": "contract-role", "title": "Contract Engineer"}
        analyst = CareerAnalystAgent.__new__(CareerAnalystAgent)
        analyst.agent = Mock()
        analyst.agent.complete.return_value = json.dumps(
            {"assessments": [{"job_id": job["job_id"], "score": 82}]}
        )

        result = analyst.evaluate([job], "cv", {})

        self.assertEqual([item["job_id"] for item in result], ["contract-role"])
        self.assertIn('"assessments"', analyst.agent.complete.call_args.args[1])

    def test_analyst_splits_batch_after_truncated_response(self) -> None:
        jobs = [{"job_id": f"job-{index}", "title": f"Role {index}"} for index in range(3)]
        analyst = CareerAnalystAgent.__new__(CareerAnalystAgent)
        analyst.agent = Mock()
        analyst.agent.complete.side_effect = [
            ModelResponseError("response truncated", "length"),
            json.dumps([{"job_id": jobs[0]["job_id"], "score": 80}]),
            json.dumps([{"job_id": job["job_id"], "score": 80} for job in jobs[1:]]),
        ]
        checkpoints: list[list[dict[str, object]]] = []

        result = analyst.evaluate(
            jobs,
            "cv",
            {},
            on_checkpoint=lambda evaluations: checkpoints.append(evaluations),
        )

        self.assertEqual(len(result), 3)
        self.assertEqual(analyst.agent.complete.call_count, 3)
        self.assertEqual([len(checkpoint) for checkpoint in checkpoints], [1, 3])

    def test_analyst_retries_jobs_omitted_from_response(self) -> None:
        job = {"job_id": "contract-role", "title": "Contract Engineer"}
        analyst = CareerAnalystAgent.__new__(CareerAnalystAgent)
        analyst.agent = Mock()
        analyst.agent.complete.side_effect = [
            json.dumps([]),
            json.dumps([{"job_id": job["job_id"], "score": 82}]),
        ]

        result = analyst.evaluate([job], "cv", {})

        self.assertEqual([item["job_id"] for item in result], ["contract-role"])
        self.assertEqual(analyst.agent.complete.call_count, 2)

    def test_analyst_raises_when_retry_still_omits_job(self) -> None:
        job = {"job_id": "contract-role", "title": "Contract Engineer"}
        analyst = CareerAnalystAgent.__new__(CareerAnalystAgent)
        analyst.agent = Mock()
        analyst.agent.complete.return_value = json.dumps([])

        with self.assertRaisesRegex(ValueError, "did not return valid assessments.*contract-role"):
            analyst.evaluate([job], "cv", {})

        self.assertEqual(analyst.agent.complete.call_count, 2)

    def test_resume_reuses_saved_agent_outputs_and_cli_accepts_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            cv_path = root / "cv.txt"
            cv_path.write_text("Candidate CV", encoding="utf-8")
            jobs_path = root / "jobs.json"
            jobs_path.write_text(
                json.dumps([{
                    "id": "role-1",
                    "title": "Platform Engineer",
                    "company": "Example Ltd",
                    "location": "London",
                    "employment_type": "permanent",
                    "description": "A platform engineering role.",
                }]),
                encoding="utf-8",
            )
            output_dir = root / "outputs"
            research = Mock()
            research.normalize.side_effect = lambda jobs, *_: jobs

            with (
                patch("talent_agent.pipeline.ResearcherAgent", return_value=research),
                patch("talent_agent.analyst.OpenRouterAgent") as openrouter,
                patch("talent_agent.documents.CVEditorAgent") as cv_editor,
                patch("talent_agent.documents.CoverLetterWriterAgent") as letter_writer,
            ):
                openrouter.return_value.complete.return_value = json.dumps([{
                    "job_id": "role-1",
                    "score": 85,
                    "rationale": "Strong match",
                    "missing_requirements": [],
                }])
                cv_editor.return_value.create.return_value = "Tailored CV text"
                letter_writer.return_value.create.return_value = "Cover letter text"
                run_pipeline(
                    jobs_path=jobs_path, search=False, cv_path=cv_path,
                    output_root=output_dir, dated_subfolders=False,
                    tracker_formats=["csv"], application_format="md", review_documents=False,
                )

            memory_dir = output_dir / "agent_outputs"
            self.assertTrue((memory_dir / "researcher.json").is_file())
            self.assertTrue((memory_dir / "career_analyst.json").is_file())
            self.assertTrue((memory_dir / "cv_editor.json").is_file())
            self.assertTrue((memory_dir / "cover_letter_writer.json").is_file())
            self.assertTrue((memory_dir / "run_state.json").is_file())

            with (
                patch("talent_agent.pipeline.ResearcherAgent", side_effect=AssertionError("Researcher reran")),
                patch("talent_agent.analyst.OpenRouterAgent") as openrouter,
                patch("talent_agent.documents.CVEditorAgent", side_effect=AssertionError("CV Editor reran")),
                patch("talent_agent.documents.CoverLetterWriterAgent", side_effect=AssertionError("Letter Writer reran")),
            ):
                run_pipeline(
                    jobs_path=None, search=False, resume_from=output_dir, cv_path=cv_path,
                    output_root=root / "unused",
                    dated_subfolders=False,
                    tracker_formats=["html"],
                    application_format="docx",
                    review_documents=False,
                )
                openrouter.return_value.complete.assert_not_called()

            researcher_path = memory_dir / "researcher.json"
            researcher_data = json.loads(researcher_path.read_text(encoding="utf-8"))
            researcher_data["version"] = 1
            researcher_path.write_text(json.dumps(researcher_data), encoding="utf-8")
            research.normalize.reset_mock()
            with (
                patch("talent_agent.pipeline.ResearcherAgent", return_value=research),
                patch("talent_agent.analyst.OpenRouterAgent") as openrouter,
                patch("talent_agent.documents.CVEditorAgent", side_effect=AssertionError("CV Editor reran")),
                patch("talent_agent.documents.CoverLetterWriterAgent", side_effect=AssertionError("Letter Writer reran")),
            ):
                run_pipeline(
                    jobs_path=None, search=False, resume_from=output_dir, cv_path=cv_path,
                    output_root=root / "unused", dated_subfolders=False,
                    tracker_formats=["html"], application_format="docx",
                    review_documents=False,
                )
                research.normalize.assert_called_once()
                openrouter.return_value.complete.assert_not_called()
            self.assertEqual(
                json.loads(researcher_path.read_text(encoding="utf-8"))["version"],
                3,
            )

            for agent_filename in ("cv_editor.json", "cover_letter_writer.json"):
                agent_path = memory_dir / agent_filename
                agent_data = json.loads(agent_path.read_text(encoding="utf-8"))
                agent_data["prompt_version"] = 1
                agent_path.write_text(json.dumps(agent_data), encoding="utf-8")

            with (
                patch("talent_agent.pipeline.ResearcherAgent", side_effect=AssertionError("Researcher reran")),
                patch("talent_agent.analyst.OpenRouterAgent") as openrouter,
                patch("talent_agent.documents.CVEditorAgent") as cv_editor,
                patch("talent_agent.documents.CoverLetterWriterAgent") as letter_writer,
            ):
                cv_editor.return_value.create.return_value = "Updated tailored CV"
                letter_writer.return_value.create.return_value = "Updated tailored letter"
                run_pipeline(
                    jobs_path=None, search=False, resume_from=output_dir, cv_path=cv_path,
                    output_root=root / "unused", dated_subfolders=False,
                    tracker_formats=["html"], application_format="docx",
                    review_documents=False,
                )
                openrouter.return_value.complete.assert_not_called()
                cv_editor.return_value.create.assert_called_once()
                letter_writer.return_value.create.assert_called_once()

            with (
                patch("talent_agent.pipeline.ResearcherAgent", side_effect=AssertionError("Researcher reran")),
                patch("talent_agent.analyst.OpenRouterAgent") as openrouter,
                patch("talent_agent.documents.CVEditorAgent") as cv_editor,
                patch("talent_agent.documents.CoverLetterWriterAgent") as letter_writer,
            ):
                openrouter.return_value.complete.return_value = json.dumps([{
                    "job_id": "role-1",
                    "score": 85,
                    "rationale": "Strong match",
                    "missing_requirements": [],
                }])
                cv_editor.return_value.create.return_value = "CV with updated candidate details"
                letter_writer.return_value.create.return_value = "Letter with updated candidate details"
                run_pipeline(
                    jobs_path=None, search=False, resume_from=output_dir, cv_path=cv_path,
                    output_root=root / "unused", dated_subfolders=False,
                    tracker_formats=["html"], application_format="docx",
                    github_url="https://github.com/updated-profile",
                    review_documents=False,
                )
                openrouter.return_value.complete.assert_called_once()
                cv_editor.return_value.create.assert_called_once()
                letter_writer.return_value.create.assert_called_once()

            parser = build_parser()
            args = parser.parse_args(["run", "--resume", str(output_dir)])
            self.assertEqual(args.resume, output_dir)
            self.assertIsNone(args.jobs)
            self.assertFalse(args.search)
            search_args = parser.parse_args([
                "run", "--search", "--location", "London", "--location", "Manchester"
            ])
            self.assertEqual(search_args.location, ["London", "Manchester"])
            arrangement_args = parser.parse_args([
                "run", "--jobs", "jobs.json",
                "--workplace-arrangement", "remote",
                "--workplace-arrangement", "partly-remote",
            ])
            self.assertEqual(arrangement_args.workplace_arrangement, ["remote", "partly-remote"])
            date_args = parser.parse_args([
                "run", "--jobs", "jobs.json", "--date-posted", "past_month",
            ])
            self.assertEqual(date_args.date_posted, "past_month")

    def test_resume_with_model_change_restarts_model_backed_stages(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            cv_path = root / "cv.txt"
            cv_path.write_text("Candidate CV", encoding="utf-8")
            jobs_path = root / "jobs.json"
            jobs_path.write_text(
                json.dumps([{
                    "id": "role-1",
                    "title": "Platform Engineer",
                    "company": "Example Ltd",
                    "location": "London",
                    "employment_type": "permanent",
                    "description": "A platform engineering role.",
                }]),
                encoding="utf-8",
            )
            output_dir = root / "outputs"
            research = Mock()
            research.normalize.side_effect = lambda jobs, *_: jobs

            with (
                patch("talent_agent.pipeline.ResearcherAgent", return_value=research),
                patch("talent_agent.analyst.OpenRouterAgent") as openrouter,
                patch("talent_agent.documents.CVEditorAgent") as cv_editor,
                patch("talent_agent.documents.CoverLetterWriterAgent") as letter_writer,
            ):
                openrouter.return_value.complete.return_value = json.dumps([{
                    "job_id": "role-1",
                    "score": 85,
                    "rationale": "Strong match",
                    "missing_requirements": [],
                }])
                cv_editor.return_value.create.return_value = "Tailored CV text"
                letter_writer.return_value.create.return_value = "Cover letter text"
                run_pipeline(
                    jobs_path=jobs_path, search=False, cv_path=cv_path,
                    output_root=output_dir, dated_subfolders=False,
                    tracker_formats=["csv"], application_format="md",
                    review_documents=False, model="model/old",
                )

                research.reset_mock()
                openrouter.reset_mock()
                cv_editor.reset_mock()
                letter_writer.reset_mock()
                run_pipeline(
                    jobs_path=None, search=False, resume_from=output_dir,
                    cv_path=cv_path, output_root=root / "unused",
                    dated_subfolders=False, tracker_formats=["csv"],
                    application_format="md", review_documents=False,
                    model="model/new", allow_model_change_on_resume=True,
                )

            research.normalize.assert_called_once()
            openrouter.return_value.complete.assert_called_once()
            cv_editor.return_value.create.assert_called_once()
            letter_writer.return_value.create.assert_called_once()
            state = json.loads(
                (output_dir / "agent_outputs" / "run_state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state["model"], "model/new")
            self.assertTrue(all(status == "complete" for status in state["stages"].values()))

    def test_unapproved_application_is_withheld_and_pipeline_continues(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            cv_path = root / "cv.txt"
            cv_path.write_text("Candidate CV", encoding="utf-8")
            jobs_path = root / "jobs.json"
            jobs_path.write_text(
                json.dumps([
                    {
                        "id": "role-1",
                        "title": "First Role",
                        "company": "First Ltd",
                        "employment_type": "permanent",
                        "description": "First role description.",
                    },
                    {
                        "id": "role-2",
                        "title": "Second Role",
                        "company": "Second Ltd",
                        "employment_type": "permanent",
                        "description": "Second role description.",
                    },
                ]),
                encoding="utf-8",
            )
            output_dir = root / "outputs"
            research = Mock()
            research.normalize.side_effect = lambda jobs, *_: jobs
            report = {
                "cv_findings": ["CV claims unsupported production ownership."],
                "cover_letter_findings": [],
                "non_blocking_notes": ["Candidate does not list one required skill."],
                "summary": "An unsupported claim remains.",
                "approved": False,
            }

            def generate_docs(*args, **kwargs):
                job = args[1]
                if job["job_id"] == "role-1":
                    kwargs["on_review_progress"]({
                        "version": 7,
                        "status": "revision_pending",
                        "attempts": 2,
                        "history": [report],
                        "final_report": report,
                        "pending_report": report,
                    })
                    raise DocumentReviewRejectedError("Reviewer rejected role 1.")
                return {
                    "cv_document": "Second_Role_CV.md",
                    "cover_letter_document": "Second_Role_CoverLetter.md",
                    "document_review": {
                        "approved": True,
                        "cv_findings": [],
                        "cover_letter_findings": [],
                        "summary": "Approved.",
                    },
                }

            with (
                patch("talent_agent.pipeline.ResearcherAgent", return_value=research),
                patch("talent_agent.analyst.OpenRouterAgent") as openrouter,
                patch("talent_agent.pipeline.DocumentReviewerAgent"),
                patch(
                    "talent_agent.pipeline.generate_application_docs",
                    side_effect=generate_docs,
                ) as generate,
            ):
                openrouter.return_value.complete.return_value = json.dumps([
                    {
                        "job_id": "role-1",
                        "score": 85,
                        "rationale": "Strong match",
                        "missing_requirements": [],
                    },
                    {
                        "job_id": "role-2",
                        "score": 80,
                        "rationale": "Good match",
                        "missing_requirements": [],
                    },
                ])
                run_pipeline(
                    jobs_path=jobs_path,
                    search=False,
                    cv_path=cv_path,
                    output_root=output_dir,
                    dated_subfolders=False,
                    tracker_formats=["csv"],
                    application_format="md",
                    model="model/test",
                )

            self.assertEqual(generate.call_count, 2)
            prioritized = json.loads(
                (output_dir / "prioritized_jobs.json").read_text(encoding="utf-8")
            )
            self.assertFalse(prioritized[0]["document_review"]["approved"])
            self.assertTrue(prioritized[1]["document_review"]["approved"])
            state = json.loads(
                (output_dir / "agent_outputs" / "run_state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state["stages"]["document_reviewer"], "in_progress")
            self.assertEqual(state["stages"]["report"], "complete")

    def test_model_response_failure_is_checkpointed_and_pipeline_continues(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            cv_path = root / "cv.txt"
            cv_path.write_text("Candidate CV", encoding="utf-8")
            jobs_path = root / "jobs.json"
            jobs_path.write_text(
                json.dumps([
                    {
                        "id": "role-1",
                        "title": "First Role",
                        "company": "First Ltd",
                        "employment_type": "permanent",
                        "description": "First role description.",
                    },
                    {
                        "id": "role-2",
                        "title": "Second Role",
                        "company": "Second Ltd",
                        "employment_type": "permanent",
                        "description": "Second role description.",
                    },
                ]),
                encoding="utf-8",
            )
            output_dir = root / "outputs"
            researcher = Mock()
            researcher.normalize.side_effect = lambda jobs, *_: jobs
            def generate_docs(*args, **kwargs):
                job = args[1]
                if job["job_id"] == "role-1":
                    kwargs["on_cv_generated"]("Saved first-role CV")
                    raise ModelResponseError(
                        "Cover Letter Writer received only safety metadata.",
                        "stop",
                    )
                return {
                    "cv_document": "Second_Role_CV.md",
                    "cover_letter_document": "Second_Role_CoverLetter.md",
                }

            with (
                patch("talent_agent.pipeline.ResearcherAgent", return_value=researcher),
                patch("talent_agent.analyst.OpenRouterAgent") as openrouter,
                patch("talent_agent.pipeline.generate_application_docs", side_effect=generate_docs) as generate,
                patch("talent_agent.pipeline.SummaryReportWriterAgent") as report_writer,
            ):
                openrouter.return_value.complete.return_value = json.dumps([
                    {"job_id": "role-1", "score": 85, "rationale": "Strong match"},
                    {"job_id": "role-2", "score": 80, "rationale": "Good match"},
                ])
                run_pipeline(
                    jobs_path=jobs_path,
                    search=False,
                    cv_path=cv_path,
                    output_root=output_dir,
                    dated_subfolders=False,
                    tracker_formats=["csv"],
                    application_format="md",
                    review_documents=False,
                )
                self.assertEqual(generate.call_count, 2)
                report_writer.return_value.write.assert_called_once()

            cv_checkpoint = json.loads(
                (output_dir / "agent_outputs" / "cv_editor.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                cv_checkpoint["outputs"]["role-1"]["text"],
                "Saved first-role CV",
            )
            state = json.loads(
                (output_dir / "agent_outputs" / "run_state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state["stages"]["cv_editor"], "in_progress")
            self.assertEqual(state["stages"]["cover_letter_writer"], "in_progress")
            self.assertEqual(state["stages"]["document_reviewer"], "complete")
            self.assertEqual(state["stages"]["report"], "complete")

    def test_reviewer_output_is_checkpointed_and_reused_on_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            cv_path = root / "cv.txt"
            cv_path.write_text("Candidate CV with Kubernetes experience.", encoding="utf-8")
            jobs_path = root / "jobs.json"
            jobs_path.write_text(
                json.dumps([{
                    "id": "role-1",
                    "title": "Platform Engineer",
                    "company": "Example Ltd",
                    "location": "London",
                    "employment_type": "permanent",
                    "description": "Improve Kubernetes platform reliability.",
                }]),
                encoding="utf-8",
            )
            output_dir = root / "outputs"
            researcher = Mock()
            researcher.normalize.side_effect = lambda jobs, *_: jobs
            review = {
                "approved": True,
                "cv_findings": [],
                "cover_letter_findings": [],
                "summary": "Documents are role-specific and factually supported.",
                "linkedin_status": "disabled",
                "linkedin_note": "LinkedIn profile fetching is disabled.",
            }

            with (
                patch("talent_agent.pipeline.ResearcherAgent", return_value=researcher),
                patch("talent_agent.analyst.OpenRouterAgent") as openrouter,
                patch("talent_agent.documents.CVEditorAgent") as cv_editor,
                patch("talent_agent.documents.CoverLetterWriterAgent") as letter_writer,
                patch("talent_agent.pipeline.DocumentReviewerAgent") as reviewer,
                patch("talent_agent.pipeline.SummaryReportWriterAgent"),
            ):
                openrouter.return_value.complete.return_value = json.dumps([{
                    "job_id": "role-1",
                    "score": 85,
                    "rationale": "Relevant Kubernetes experience.",
                    "missing_requirements": [],
                }])
                cv_editor.return_value.create.return_value = "Platform engineer with Kubernetes experience."
                letter_writer.return_value.create.return_value = "I can improve platform reliability."
                reviewer.return_value.review.return_value = review
                run_pipeline(
                    jobs_path=jobs_path,
                    search=False,
                    cv_path=cv_path,
                    output_root=output_dir,
                    dated_subfolders=False,
                    tracker_formats=["csv"],
                    application_format="md",
                    review_documents=True,
                    review_linkedin_profile=False,
                )
                reviewer.return_value.review.assert_called_once()

            checkpoint_path = output_dir / "agent_outputs" / "document_reviewer.json"
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            self.assertEqual(checkpoint["outputs"]["role-1"]["status"], "complete")
            self.assertTrue(checkpoint["outputs"]["role-1"]["final_report"]["approved"])
            prioritized = json.loads((output_dir / "prioritized_jobs.json").read_text(encoding="utf-8"))
            self.assertTrue(prioritized[0]["document_review"]["approved"])

            with (
                patch("talent_agent.pipeline.ResearcherAgent", side_effect=AssertionError("Researcher reran")),
                patch("talent_agent.analyst.OpenRouterAgent"),
                patch("talent_agent.documents.CVEditorAgent", side_effect=AssertionError("CV Editor reran")),
                patch("talent_agent.documents.CoverLetterWriterAgent", side_effect=AssertionError("Letter Writer reran")),
                patch("talent_agent.pipeline.DocumentReviewerAgent", side_effect=AssertionError("Reviewer reran")),
                patch("talent_agent.pipeline.SummaryReportWriterAgent"),
            ):
                run_pipeline(
                    jobs_path=None,
                    search=False,
                    resume_from=output_dir,
                    cv_path=cv_path,
                    tracker_formats=["csv"],
                    application_format="md",
                    review_documents=True,
                    review_linkedin_profile=False,
                )


if __name__ == "__main__":
    unittest.main()
