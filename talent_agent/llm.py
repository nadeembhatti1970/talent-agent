from __future__ import annotations

import json
import os
import re
import time

from openai import OpenAI


class ModelResponseError(RuntimeError):
    def __init__(self, message: str, finish_reason: str) -> None:
        super().__init__(message)
        self.finish_reason = finish_reason


def is_safety_metadata_only(text: str) -> bool:
    return text.strip().casefold() == "user safety: safe"


class OpenRouterAgent:
    """Small role-agent wrapper using OpenRouter's OpenAI-compatible endpoint."""

    def __init__(self, role: str, model: str | None = None) -> None:
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY is missing. Set it in your environment or .env file.")
        self.role = role
        self.model = model or os.getenv("OPENROUTER_MODEL", "openrouter/free")
        self.client = OpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            default_headers={
                "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "http://localhost"),
                "X-Title": os.getenv("OPENROUTER_APP_NAME", "Talent Agent"),
            },
        )

    def complete(self, system: str, prompt: str, *, json_output: bool = False) -> str:
        response = None
        text = None
        json_error: ValueError | None = None
        safety_metadata_only = False
        response_problem = "empty responses"
        for attempt in range(3):
            retry_prompt = prompt
            if json_error is not None:
                retry_prompt += (
                    "\n\nYour previous response was not valid JSON. "
                    "Return only one complete, valid JSON value."
                )
            elif safety_metadata_only:
                retry_prompt += (
                    "\n\nYour previous response contained only safety metadata. "
                    "Return the requested content itself, without safety metadata."
                )
            text = None
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": retry_prompt}],
                temperature=0.2,
                max_tokens=5000,
            )
            if response.choices:
                text = response.choices[0].message.content
            if not text or not text.strip() or (not json_output and is_safety_metadata_only(text)):
                safety_metadata_only = bool(text and is_safety_metadata_only(text))
                response_problem = (
                    "only safety metadata" if safety_metadata_only else "empty responses"
                )
                if attempt < 2:
                    print(
                        f"      ↻ {self.role} received {response_problem}; retrying ({attempt + 1}/2)…",
                        flush=True,
                    )
                    time.sleep(attempt + 1)
                    continue
                break
            if not json_output:
                return text.strip()
            try:
                return self._extract_json(text)
            except ValueError as exc:
                json_error = exc
                if attempt < 2:
                    print(
                        f"      ↻ {self.role} received an invalid JSON response; retrying ({attempt + 1}/2)…",
                        flush=True,
                    )
                    time.sleep(attempt + 1)
                    continue
                finish_reason = response.choices[0].finish_reason if response and response.choices else "no choices"
                served_model = response.model if response else self.model
                raise ModelResponseError(
                    f"{self.role} did not receive valid JSON after 3 attempts "
                    f"(model={served_model}, finish_reason={finish_reason}).",
                    finish_reason,
                ) from json_error
        finish_reason = response.choices[0].finish_reason if response and response.choices else "no choices"
        served_model = response.model if response else self.model
        raise ModelResponseError(
            f"{self.role} received {response_problem} after 3 attempts "
            f"(model={served_model}, finish_reason={finish_reason}). Try another OpenRouter model or rerun.",
            finish_reason,
        )

    @staticmethod
    def _extract_json(text: str) -> str:
        cleaned = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text.strip(), flags=re.IGNORECASE)
        start = cleaned.find("{")
        array_start = cleaned.find("[")
        if start < 0 or (array_start >= 0 and array_start < start):
            start = array_start
        if start < 0:
            raise ValueError("Model response did not contain JSON")
        opener = cleaned[start]
        closer = "}" if opener == "{" else "]"
        depth = 0
        in_string = escaped = False
        for index in range(start, len(cleaned)):
            char = cleaned[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == opener:
                depth += 1
            elif char == closer:
                depth -= 1
                if depth == 0:
                    result = cleaned[start : index + 1]
                    json.loads(result)
                    return result
        raise ValueError("Model response contained incomplete JSON")
