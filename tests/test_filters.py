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
            "exclude_security_clearance": True,
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

    def test_excludes_roles_with_required_security_clearance(self) -> None:
        jobs = [
            {
                "job_id": "required-clearance",
                "title": "Cloud Engineer",
                "description": "Active SC clearance required for this role.",
                "employment_type": "contract",
                "salary": "£700 per day",
            },
            {
                "job_id": "must-hold-clearance",
                "title": "DevOps Engineer",
                "description": "Applicants must hold current DV clearance.",
                "employment_type": "contract",
                "salary": "£700 per day",
            },
            {
                "job_id": "eligible-to-obtain",
                "title": "Platform Engineer",
                "description": "Must be eligible for security clearance.",
                "employment_type": "contract",
                "salary": "£700 per day",
            },
            {
                "job_id": "no-clearance-requirement",
                "title": "Platform Engineer",
                "description": "Experience supporting secure cloud services is desirable.",
                "employment_type": "contract",
                "salary": "£700 per day",
            },
            {
                "job_id": "clearance-only-mention",
                "title": "SRE",
                "description": "Previous experience in security-cleared environments is useful.",
                "employment_type": "contract",
                "salary": "£700 per day",
            },
        ]

        filtered = filter_jobs(jobs, self.config)

        self.assertEqual(
            [job["job_id"] for job in filtered],
            ["no-clearance-requirement", "clearance-only-mention"],
        )

    def test_clearance_filter_can_be_disabled(self) -> None:
        config = {**self.config, "exclude_security_clearance": False}
        job = {
            "job_id": "required-clearance",
            "title": "Cloud Engineer",
            "description": "SC clearance required.",
            "employment_type": "contract",
            "salary": "£700 per day",
        }

        self.assertEqual(filter_jobs([job], config), [job])


if __name__ == "__main__":
    unittest.main()
