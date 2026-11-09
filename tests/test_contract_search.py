from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from talent_agent.filters import (
    _compensation_period,
    application_gate,
    classify_role_family,
)
from talent_agent.job_sources import fetch_adzuna_jobs, fetch_reed_jobs
from talent_agent.ledger import (
    application_key,
    load_ledger,
    record_application,
    should_skip,
)
from talent_agent.researcher import _employment_type, normalize_jobs


def _response(payload):
    response = MagicMock(status_code=200)
    response.json.return_value = payload
    return response


class ContractClassificationTests(unittest.TestCase):
    def test_explicit_contract_evidence_overrides_generic_full_time_label(self) -> None:
        self.assertEqual(
            _employment_type(
                "Full-time",
                "Data Engineer",
                "This is an outside IR35 contract role paying £475 per day.",
            ),
            "contract",
        )
        self.assertEqual(
            _employment_type(
                "Full-time",
                "Senior Engineer - Distribution Platforms",
                "Six month engagement. Daily rate: inside IR35.",
            ),
            "contract",
        )
        self.assertEqual(
            _employment_type("", "D365 CE Developers - Outside IR35", ""),
            "contract",
        )

    def test_contract_words_in_legal_or_company_text_do_not_trigger_contract(self) -> None:
        self.assertEqual(
            _employment_type(
                "Full-time",
                "Backend Engineer",
                "The contracting authority requires pre-contractual measures.",
            ),
            "permanent",
        )
        self.assertEqual(
            _employment_type(
                "Full-time",
                "Platform Engineer",
                "Permanent role with an annual salary and full-time benefits.",
            ),
            "permanent",
        )

    def test_bare_reed_amounts_are_inferred_as_day_rates_or_annual_salaries(self) -> None:
        self.assertEqual(_compensation_period("GBP 400-425"), "daily")
        self.assertEqual(_compensation_period("GBP 40000-45000"), "annual")
        self.assertEqual(_compensation_period("GBP 500-500 per year"), "annual")
        self.assertEqual(_compensation_period("£650 per day"), "daily")
        self.assertEqual(_compensation_period("£75 per hour"), "hourly")
        self.assertIsNone(_compensation_period("Not specified"))


class RoleFamilyGateTests(unittest.TestCase):
    def test_core_devops_families_are_recognised(self) -> None:
        for title in (
            "DevOps Engineer",
            "Platform Engineer",
            "Site Reliability Engineer",
            "SRE",
            "Cloud Infrastructure Engineer",
            "Python Automation Engineer",
            "MLOps Engineer",
        ):
            with self.subTest(title=title):
                self.assertTrue(classify_role_family({"title": title}))

    def test_backend_roles_are_not_application_family_matches(self) -> None:
        job = {"title": "Backend Engineer", "tech_overlap": 80}
        passed, reason = application_gate(job, 60, True)
        self.assertFalse(passed)
        self.assertEqual(reason, "filtered_role_family")

    def test_unrelated_platform_and_automation_products_are_not_family_matches(self) -> None:
        for title in ("Lead Power Platform Developer", "API Automation Tester"):
            with self.subTest(title=title):
                self.assertEqual(classify_role_family({"title": title}), "")

    def test_low_technical_overlap_is_filtered_but_missing_scores_are_tolerated(self) -> None:
        job = {"title": "Platform Engineer", "tech_overlap": 55}
        passed, reason = application_gate(job, 60, True)
        self.assertFalse(passed)
        self.assertEqual(reason, "filtered_tech_overlap")

        passed, reason = application_gate({"title": "Platform Engineer"}, 60, True)
        self.assertTrue(passed)
        self.assertEqual(reason, "")

    def test_gate_can_be_disabled_for_backward_compatibility(self) -> None:
        passed, reason = application_gate({"title": "Backend Engineer", "tech_overlap": 10}, 60, False)
        self.assertTrue(passed)
        self.assertEqual(reason, "")


class SourceContractFilterTests(unittest.TestCase):
    def test_reed_uses_the_contract_filter_and_strips_the_query_suffix(self) -> None:
        payload = {"results": [{
            "jobId": 7,
            "jobTitle": "DevOps Engineer",
            "employerName": "Acme",
            "locationName": "London",
            "jobUrl": "https://reed.co.uk/jobs/7",
            "jobDescription": "Contract role",
            "minimumSalary": 500,
            "maximumSalary": 650,
            "date": "01/01/2000",
        }]}
        with (
            patch.dict(os.environ, {"REED_API_KEY": "k"}),
            patch("talent_agent.job_sources.requests.get", return_value=_response(payload)) as get,
        ):
            jobs = fetch_reed_jobs("DevOps Engineer contract", "London", "any_time", 10)

        self.assertEqual(get.call_args.kwargs["params"]["keywords"], "DevOps Engineer")
        self.assertEqual(get.call_args.kwargs["params"]["contract"], "true")
        self.assertEqual(jobs[0]["employment_type"], "contract")
        self.assertEqual(jobs[0]["salary"], "GBP 500-650 per day")
        self.assertEqual(jobs[0]["source"], "reed")

    def test_adzuna_uses_the_contract_filter_and_strips_the_query_suffix(self) -> None:
        payload = {"results": [{
            "id": "9",
            "title": "SRE",
            "company": {"display_name": "Beta"},
            "location": {"display_name": "London"},
            "redirect_url": "https://x/9",
            "description": "d",
            "salary_min": 50000,
            "salary_max": 60000,
            "contract_type": "contract",
        }]}
        with (
            patch.dict(os.environ, {"ADZUNA_APP_ID": "a", "ADZUNA_APP_KEY": "b"}),
            patch("talent_agent.job_sources.requests.get", return_value=_response(payload)) as get,
        ):
            jobs = fetch_adzuna_jobs("SRE contract", "London", "past_week", 5)

        self.assertEqual(get.call_args.kwargs["params"]["what"], "SRE")
        self.assertEqual(get.call_args.kwargs["params"]["contract"], 1)
        self.assertEqual(jobs[0]["source"], "adzuna")


class CrossSourceDeduplicationTests(unittest.TestCase):
    def test_duplicate_role_keeps_the_longer_description_and_records_both_sources(self) -> None:
        records = [
            {
                "id": "linkedin-1",
                "title": "Platform Engineer",
                "company": "Example Ltd",
                "location": "London",
                "url": "https://linkedin.com/jobs/view/1?trackingId=abc",
                "description": "",
                "source": "linkedin",
            },
            {
                "id": "reed-1",
                "title": "Platform Engineer",
                "company": "Example Ltd",
                "location": "London",
                "url": "https://reed.co.uk/jobs/1",
                "description": "Kubernetes platform role with Terraform and AWS.",
                "source": "reed",
            },
        ]

        jobs = normalize_jobs(records)

        self.assertEqual(len(jobs), 1)
        self.assertIn("Kubernetes platform role", jobs[0]["description"])
        self.assertFalse(jobs[0]["description_missing"])
        self.assertEqual(jobs[0]["sources"], ["linkedin", "reed"])


class ApplicationLedgerTests(unittest.TestCase):
    def test_key_is_stable_for_company_and_title_variations(self) -> None:
        self.assertEqual(
            application_key({"company": "Example Ltd", "title": "Platform Engineer"}),
            application_key({"company": "Example Limited", "title": "Platform Engineer"}),
        )

    def test_recent_application_is_skipped_and_old_application_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            path = Path(temporary_dir) / "application_ledger.json"
            job = {"company": "Example Ltd", "title": "Platform Engineer"}
            applied_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
            record_application(path, job, {"cv": "cv.docx"}, applied_at=applied_at)

            ledger = load_ledger(path)
            self.assertIsNotNone(
                should_skip(ledger, job, 30, now=applied_at + timedelta(days=10))
            )
            self.assertIsNone(
                should_skip(ledger, job, 30, now=applied_at + timedelta(days=31))
            )
            self.assertIsNone(
                should_skip(ledger, job, 0, now=applied_at + timedelta(days=1))
            )

    def test_recording_twice_increments_the_application_count(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            path = Path(temporary_dir) / "application_ledger.json"
            job = {"company": "Example Ltd", "title": "Platform Engineer"}
            first = record_application(path, job, {"cv": "a.docx"})
            second = record_application(path, job, {"cv": "b.docx"})

        self.assertEqual(first["count"], 1)
        self.assertEqual(second["count"], 2)
        self.assertEqual(second["documents"], {"cv": "b.docx"})


if __name__ == "__main__":
    unittest.main()
