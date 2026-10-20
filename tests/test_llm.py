from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from talent_agent.llm import OpenRouterAgent


def make_response(content: str, finish_reason: str = "stop") -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason=finish_reason,
            )
        ],
        model="mock/model",
    )


class OpenRouterAgentTests(unittest.TestCase):
    def make_agent(self) -> OpenRouterAgent:
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            return OpenRouterAgent("Test")

    def test_retries_invalid_json_and_returns_valid_response(self) -> None:
        agent = self.make_agent()
        create = Mock(side_effect=[make_response("No JSON here."), make_response('[{"ok": true}]')])
        agent.client.chat.completions.create = create

        with patch("talent_agent.llm.time.sleep"):
            result = agent.complete("system", "prompt", json_output=True)

        self.assertEqual(result, '[{"ok": true}]')
        self.assertEqual(create.call_count, 2)
        retry_prompt = create.call_args_list[1].kwargs["messages"][1]["content"]
        self.assertIn("previous response was not valid JSON", retry_prompt)

    def test_reports_persistent_invalid_json_after_three_attempts(self) -> None:
        agent = self.make_agent()
        create = Mock(
            side_effect=[
                make_response("Not JSON."),
                make_response("Still not JSON."),
                make_response("No JSON here either."),
            ]
        )
        agent.client.chat.completions.create = create

        with patch("talent_agent.llm.time.sleep"):
            with self.assertRaisesRegex(RuntimeError, "did not receive valid JSON after 3 attempts"):
                agent.complete("system", "prompt", json_output=True)

        self.assertEqual(create.call_count, 3)

    def test_retries_safety_metadata_only_text_and_reports_persistent_failure(self) -> None:
        agent = self.make_agent()
        create = Mock(
            side_effect=[
                make_response("User Safety: safe"),
                make_response("User Safety: safe"),
                make_response("User Safety: safe"),
            ]
        )
        agent.client.chat.completions.create = create

        with patch("talent_agent.llm.time.sleep"):
            with self.assertRaisesRegex(RuntimeError, "received only safety metadata after 3 attempts"):
                agent.complete("system", "prompt")

        self.assertEqual(create.call_count, 3)
        retry_prompt = create.call_args_list[1].kwargs["messages"][1]["content"]
        self.assertIn("contained only safety metadata", retry_prompt)

    def test_retries_truncated_explicit_model_without_reasoning(self) -> None:
        agent = self.make_agent()
        agent.model = "deepseek/deepseek-v4.1-flash"
        create = Mock(
            side_effect=[
                make_response("", finish_reason="length"),
                make_response("Finished CV"),
            ]
        )
        agent.client.chat.completions.create = create

        with patch("talent_agent.llm.time.sleep"):
            result = agent.complete("system", "prompt")

        self.assertEqual(result, "Finished CV")
        self.assertNotIn("extra_body", create.call_args_list[0].kwargs)
        self.assertEqual(
            create.call_args_list[1].kwargs["extra_body"],
            {"reasoning": {"effort": "none"}},
        )

    def test_requests_json_object_from_explicit_models(self) -> None:
        agent = self.make_agent()
        agent.model = "deepseek/deepseek-v4.1-flash"
        create = Mock(return_value=make_response('{"ok": true}'))
        agent.client.chat.completions.create = create

        self.assertEqual(agent.complete("system", "prompt", json_output=True), '{"ok": true}')
        self.assertEqual(
            create.call_args.kwargs["response_format"],
            {"type": "json_object"},
        )


if __name__ == "__main__":
    unittest.main()
