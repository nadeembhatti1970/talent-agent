from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, Mock, patch

from talent_agent.reviewer import DocumentReviewerAgent, fetch_linkedin_profile


class LinkedinProfileFetchTests(unittest.TestCase):
    def test_fetches_public_linkedin_profile_text(self) -> None:
        response = MagicMock()
        response.__enter__.return_value = response
        response.is_redirect = False
        response.encoding = "utf-8"
        response.iter_content.return_value = [
            b"<html><head><meta property='og:title' content='Jane Example | LinkedIn'></head>"
            b"<body><h1>Jane Example</h1><p>Platform Engineer</p>"
            b"<script>ignored secret</script></body></html>"
        ]

        with patch("talent_agent.reviewer.requests.get", return_value=response) as get:
            result = fetch_linkedin_profile("https://www.linkedin.com/in/jane-example/")

        self.assertEqual(result["status"], "available")
        self.assertIn("Jane Example", result["text"])
        self.assertIn("Platform Engineer", result["text"])
        self.assertNotIn("ignored secret", result["text"])
        get.assert_called_once()
        self.assertFalse(get.call_args.kwargs["allow_redirects"])

    def test_rejects_non_linkedin_profile_urls_without_network_request(self) -> None:
        with patch("talent_agent.reviewer.requests.get") as get:
            result = fetch_linkedin_profile("https://example.com/in/person")

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["text"], "")
        get.assert_not_called()


class DocumentReviewerTests(unittest.TestCase):
    def test_review_validates_json_shape_and_sets_approval(self) -> None:
        agent = DocumentReviewerAgent.__new__(DocumentReviewerAgent)
        agent.agent = Mock()
        agent.agent.complete.return_value = json.dumps({
            "cv_findings": [],
            "cover_letter_findings": ["Unsupported metric."],
            "non_blocking_notes": ["No direct SRE experience is listed."],
            "summary": "One claim needs review.",
        })

        result = agent.review(
            "generated cv",
            "generated letter",
            "master cv",
            {"title": "Platform Engineer", "company": "Example Ltd"},
            {"status": "available", "text": "LinkedIn evidence"},
            github_url="https://github.com/example-user",
        )

        self.assertFalse(result["approved"])
        self.assertEqual(result["non_blocking_notes"], ["No direct SRE experience is listed."])
        self.assertEqual(result["linkedin_status"], "available")
        prompt = agent.agent.complete.call_args.args[1]
        self.assertIn("master cv", prompt)
        self.assertIn("LinkedIn evidence", prompt)
        self.assertIn("Example Ltd", prompt)
        self.assertIn("https://github.com/example-user", prompt)
        self.assertIn("authorized contact", prompt)
        self.assertIn("Never infer roles or dates from timeline gaps", prompt)
        self.assertTrue(agent.agent.complete.call_args.kwargs["json_output"])
        self.assertEqual(agent.agent.complete.call_args.kwargs["max_tokens"], 10000)

    def test_missing_job_requirements_are_non_blocking(self) -> None:
        agent = DocumentReviewerAgent.__new__(DocumentReviewerAgent)
        agent.agent = Mock()
        agent.agent.complete.return_value = json.dumps({
            "cv_findings": [],
            "cover_letter_findings": [],
            "non_blocking_notes": ["No direct OpenTelemetry experience is listed."],
            "summary": "Drafts are factually supported; one role requirement is not evidenced.",
        })

        result = agent.review(
            "CV with related platform experience",
            "Letter honestly describing related experience",
            "master CV",
            {"title": "Observability Engineer", "company": "Example Ltd"},
            {"status": "unavailable", "text": ""},
        )

        self.assertTrue(result["approved"])
        self.assertEqual(len(result["non_blocking_notes"]), 1)
        prompt = agent.agent.complete.call_args.args[0]
        self.assertIn("Missing qualifications, experience, or role fit are not errors", prompt)


if __name__ == "__main__":
    unittest.main()
