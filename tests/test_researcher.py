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
        response = Mock()
        response.status_code = 200
        response.json.return_value = [{"id": "job-1"}]
        with (
            patch.dict(os.environ, {"APIFY_API_TOKEN": "test-token", "APIFY_ACTOR_ID": "owner/actor"}),
            patch("talent_agent.researcher.requests.post", return_value=response) as post,
        ):
            result = fetch_apify_jobs("Platform Engineer", "London", "past_week", None)

        self.assertEqual(result, [{"id": "job-1"}])
        self.assertNotIn("limit", post.call_args.kwargs["params"])
        self.assertNotIn("maxItems", post.call_args.kwargs["params"])
        self.assertNotIn("limitPerSource", post.call_args.kwargs["json"])

    def test_limited_apify_request_passes_result_limit_parameters(self) -> None:
        response = Mock()
        response.status_code = 200
        response.json.return_value = []
        with (
            patch.dict(os.environ, {"APIFY_API_TOKEN": "test-token", "APIFY_ACTOR_ID": "owner/actor"}),
            patch("talent_agent.researcher.requests.post", return_value=response) as post,
        ):
            fetch_apify_jobs("Platform Engineer", "London", "past_week", 50)

        self.assertEqual(post.call_args.kwargs["params"]["limit"], 50)
        self.assertEqual(post.call_args.kwargs["params"]["maxItems"], 50)
        self.assertEqual(post.call_args.kwargs["json"]["limitPerSource"], 50)


if __name__ == "__main__":
    unittest.main()
