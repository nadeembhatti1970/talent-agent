import os
import unittest
from unittest.mock import MagicMock, patch

from talent_agent.job_sources import fetch_adzuna_jobs, fetch_jobs, fetch_reed_jobs
from talent_agent.researcher import normalize_jobs


def _response(payload):
    response = MagicMock(status_code=200)
    response.json.return_value = payload
    return response


class JobSourceTests(unittest.TestCase):
    def test_unknown_source_is_rejected(self) -> None:
        with patch.dict(os.environ, {"JOB_SOURCE": "nope"}):
            with self.assertRaises(ValueError):
                fetch_jobs("x", "London", "past_week", 5)

    def test_dispatches_to_selected_source(self) -> None:
        with (
            patch.dict(os.environ, {"JOB_SOURCE": "reed"}),
            patch("talent_agent.job_sources.fetch_reed_jobs", return_value=[{"id": 1}]) as reed,
        ):
            self.assertEqual(fetch_jobs("x", "London", "past_week", 5), [{"id": 1}])
        reed.assert_called_once_with("x", "London", "past_week", 5)

    def test_reed_results_normalize(self) -> None:
        payload = {"results": [{
            "jobId": 7, "jobTitle": "Platform Engineer", "employerName": "Acme",
            "locationName": "London", "jobUrl": "https://reed.co.uk/jobs/7",
            "jobDescription": "Contract role", "minimumSalary": 500, "maximumSalary": 600,
            "date": "01/01/2000",
        }]}
        with (
            patch.dict(os.environ, {"REED_API_KEY": "k"}),
            patch(
                "talent_agent.job_sources.requests.get",
                side_effect=[_response(payload), _response(payload), _response({"results": []}), _response(payload)],
            ),
        ):
            self.assertEqual(fetch_reed_jobs("pe", "London", "any_time", 1)[0]["id"], "reed-7")
            # The listing is dated 2000, so the one-week filter drops it.
            self.assertEqual(fetch_reed_jobs("pe", "London", "past_week", 1), [])
            jobs = normalize_jobs(fetch_reed_jobs("pe", "London", "any_time", 1))
        self.assertEqual(jobs[0]["company"], "Acme")

    def test_adzuna_results_normalize(self) -> None:
        payload = {"results": [{
            "id": "9", "title": "SRE", "company": {"display_name": "Beta"},
            "location": {"display_name": "Leeds"}, "redirect_url": "https://x/9",
            "description": "d", "salary_min": 50000, "salary_max": 60000, "contract_type": "permanent",
        }]}
        with (
            patch.dict(os.environ, {"ADZUNA_APP_ID": "a", "ADZUNA_APP_KEY": "b"}),
            patch("talent_agent.job_sources.requests.get", side_effect=[_response(payload), _response({"results": []})]),
        ):
            jobs = normalize_jobs(fetch_adzuna_jobs("sre", "Leeds", "past_week", 5))
        self.assertEqual((jobs[0]["company"], jobs[0]["employment_type"]), ("Beta", "permanent"))


if __name__ == "__main__":
    unittest.main()
