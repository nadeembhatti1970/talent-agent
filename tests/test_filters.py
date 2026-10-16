from __future__ import annotations

import unittest

from talent_agent.filters import filter_jobs


class FilterJobsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "employment_types": ["contract", "permanent"],
            "minimum_day_rate": 600,
            "minimum_annual_salary": 150000,
            "exclude_unknown_compensation": True,
            "include_unknown_contract_compensation": True,
            "currency": "GBP",
        }

    def test_unknown_contract_rates_are_kept_for_review_but_known_minimum_still_applies(self) -> None:
        jobs = [
            {"job_id": "unknown-contract", "employment_type": "contract", "salary": "Not specified"},
            {"job_id": "qualifying-contract", "employment_type": "contract", "salary": "£650 per day"},
            {"job_id": "below-minimum", "employment_type": "contract", "salary": "£550 per day"},
            {"job_id": "annual-pay", "employment_type": "contract", "salary": "£50,000 per year"},
            {"job_id": "unknown-permanent", "employment_type": "permanent", "salary": "Not specified"},
        ]

        filtered = filter_jobs(jobs, self.config)

        self.assertEqual(
            [job["job_id"] for job in filtered],
            ["unknown-contract", "qualifying-contract"],
        )

    def test_unknown_contract_setting_falls_back_to_general_policy(self) -> None:
        config = {**self.config, "include_unknown_contract_compensation": False}
        jobs = [{"job_id": "unknown", "employment_type": "contract", "salary": "Not specified"}]

        self.assertEqual(filter_jobs(jobs, config), [])


if __name__ == "__main__":
    unittest.main()
