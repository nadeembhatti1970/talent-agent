from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from talent_agent.analyst import CareerAnalystAgent
from talent_agent.cli import build_parser
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
                }),
                encoding="utf-8",
            )
            config = load_search_config(config_path)
            self.assertEqual(config["locations"], ["London", "Manchester"])
            self.assertEqual(config["github_profile_url"], "https://github.com/example-user")

            config_path.write_text(json.dumps({"location": "London"}), encoding="utf-8")
            self.assertEqual(load_search_config(config_path)["locations"], ["London"])

    def test_default_search_config_has_separate_locations_and_contract_review_setting(self) -> None:
        config = load_search_config()

        self.assertGreater(len(config["locations"]), 1)
        self.assertTrue(config["include_unknown_contract_compensation"])

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
                    output_root=root / "outputs",
                    dated_subfolders=False,
                    tracker_formats=["csv"],
                )

            self.assertEqual([call.args[1] for call in fetch.call_args_list], ["London", "Manchester"])
            raw_jobs = json.loads((output_dir / "raw_jobs.json").read_text(encoding="utf-8"))
            self.assertEqual(len(raw_jobs), 1)

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
                    tracker_formats=["csv"], application_format="md",
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
                )
                openrouter.return_value.complete.assert_not_called()

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


if __name__ == "__main__":
    unittest.main()
