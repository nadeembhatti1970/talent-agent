from __future__ import annotations

import json
import os
import unittest
from unittest.mock import Mock, patch

from talent_agent.researcher import ResearcherAgent, fetch_apify_jobs


class ResearcherTests(unittest.TestCase):
    def test_relevance_review_batches_listings_and_returns_original_records(self) -> None:
        records = [
            {
                "id": f"job-{index}",
                "title": f"Platform Engineer {index}",
                "company": "Example Ltd",
                "location": "London",
                "url": f"https://example.test/jobs/{index}",
                "description": "Kubernetes and platform reliability. " * 100,
                "salary": "£650 per day",
                "employment_type": "contract",
            }
            for index in range(23)
        ]
        researcher = ResearcherAgent.__new__(ResearcherAgent)
        researcher.agent = Mock()
        reviewed_batches: list[list[dict[str, object]]] = []

        def keep_first_job(system: str, prompt: str, *, json_output: bool) -> str:
            self.assertIn("Output only the requested JSON object", system)
            self.assertTrue(json_output)
            listings_text = prompt.split("Listings: ", maxsplit=1)[1]
            batch = json.loads(listings_text)
            reviewed_batches.append(batch)
            return json.dumps({"keep_job_ids": [batch[0]["job_id"]]})

        researcher.agent.complete.side_effect = keep_first_job

        result = researcher.normalize(records, "Platform Engineer", "London")

        self.assertEqual([len(batch) for batch in reviewed_batches], [10, 10, 3])
        self.assertEqual(
            [job["job_id"] for job in result],
            ["job-0", "job-10", "job-20"],
        )
        self.assertEqual(result[0]["description"], records[0]["description"].strip())
        self.assertLessEqual(len(reviewed_batches[0][0]["description"]), 1600)

    def test_unlimited_apify_request_omits_result_limit_parameters(self) -> None:
        run_response = Mock()
        run_response.status_code = 201
        run_response.json.return_value = {
            "data": {
                "id": "run-1",
                "status": "SUCCEEDED",
                "defaultDatasetId": "dataset-1",
            }
        }
        dataset_response = Mock()
        dataset_response.json.return_value = [{"id": "job-1"}]
        with (
            patch.dict(os.environ, {"APIFY_API_TOKEN": "test-token", "APIFY_ACTOR_ID": "owner/actor"}),
            patch("talent_agent.researcher.requests.post", return_value=run_response) as post,
            patch("talent_agent.researcher.requests.get", return_value=dataset_response) as get,
        ):
            result = fetch_apify_jobs("Platform Engineer", "London", "past_week", None)

        self.assertEqual(result, [{"id": "job-1"}])
        self.assertTrue(post.call_args.args[0].endswith("/acts/owner~actor/runs"))
        self.assertNotIn("limitPerSource", post.call_args.kwargs["json"])
        self.assertNotIn("limit", get.call_args.kwargs["params"])

    def test_limited_apify_request_passes_result_limit_parameters(self) -> None:
        run_response = Mock()
        run_response.status_code = 201
        run_response.json.return_value = {
            "data": {
                "id": "run-1",
                "status": "SUCCEEDED",
                "defaultDatasetId": "dataset-1",
            }
        }
        dataset_response = Mock()
        dataset_response.json.return_value = []
        with (
            patch.dict(os.environ, {"APIFY_API_TOKEN": "test-token", "APIFY_ACTOR_ID": "owner/actor"}),
            patch("talent_agent.researcher.requests.post", return_value=run_response) as post,
            patch("talent_agent.researcher.requests.get", return_value=dataset_response) as get,
        ):
            fetch_apify_jobs("Platform Engineer", "London", "past_week", 50)

        self.assertEqual(post.call_args.kwargs["json"]["limitPerSource"], 50)
        self.assertEqual(get.call_args.kwargs["params"]["limit"], 50)

    def test_date_posted_options_map_to_apify_values(self) -> None:
        expected = {
            "past_24_hours": "past24Hours",
            "past_week": "pastWeek",
            "past_month": "pastMonth",
            "any_time": "anyTime",
        }
        for option, api_value in expected.items():
            with self.subTest(option=option):
                run_response = Mock()
                run_response.status_code = 201
                run_response.json.return_value = {
                    "data": {
                        "id": "run-1",
                        "status": "SUCCEEDED",
                        "defaultDatasetId": "dataset-1",
                    }
                }
                dataset_response = Mock()
                dataset_response.json.return_value = []
                with (
                    patch.dict(
                        os.environ,
                        {"APIFY_API_TOKEN": "test-token", "APIFY_ACTOR_ID": "owner/actor"},
                    ),
                    patch("talent_agent.researcher.requests.post", return_value=run_response) as post,
                    patch("talent_agent.researcher.requests.get", return_value=dataset_response),
                ):
                    fetch_apify_jobs("Platform Engineer", "London", option, 10)

                self.assertEqual(post.call_args.kwargs["json"]["datePosted"], api_value)

    def test_invalid_date_posted_option_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "date_posted must be"):
            fetch_apify_jobs("Platform Engineer", "London", "past_year", 10)

    def test_apify_run_is_polled_until_completion_before_loading_dataset(self) -> None:
        run_response = Mock()
        run_response.status_code = 201
        run_response.json.return_value = {
            "data": {"id": "run-1", "status": "RUNNING"}
        }
        running_response = Mock()
        running_response.json.return_value = {"data": {"id": "run-1", "status": "RUNNING"}}
        completed_response = Mock()
        completed_response.json.return_value = {
            "data": {
                "id": "run-1",
                "status": "SUCCEEDED",
                "defaultDatasetId": "dataset-1",
            }
        }
        dataset_response = Mock()
        dataset_response.json.return_value = [{"id": "job-1"}]
        with (
            patch.dict(os.environ, {"APIFY_API_TOKEN": "test-token", "APIFY_ACTOR_ID": "owner/actor"}),
            patch("talent_agent.researcher.requests.post", return_value=run_response),
            patch(
                "talent_agent.researcher.requests.get",
                side_effect=[running_response, completed_response, dataset_response],
            ) as get,
            patch("talent_agent.researcher.time.sleep") as sleep,
        ):
            result = fetch_apify_jobs("Platform Engineer", "London", "past_week", None)

        self.assertEqual(result, [{"id": "job-1"}])
        self.assertEqual(get.call_count, 3)
        self.assertEqual(sleep.call_count, 2)


if __name__ == "__main__":
    unittest.main()
