from __future__ import annotations

import json
import re as _re
from dataclasses import dataclass, field
from typing import Any, Type, TypeVar

import httpx
from pydantic import BaseModel
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.platform.logging import get_logger

logger = get_logger(__name__)

# HTTP 5xx status codes that trigger AIUnavailableError (and tenacity retry).
# 429 is handled separately — it raises AIRateLimitError before this check.
_5XX_STATUS = {500, 502, 503, 504}

T = TypeVar("T", bound=BaseModel)


class AIError(Exception):
    """Base error for AI client."""


class AIRateLimitError(AIError):
    """HTTP 429 — back off and retry."""


class AIUnavailableError(AIError):
    """HTTP 5xx — upstream unavailable."""


class AIClient:
    """Async AI client (Perplexity backend) with retry and structured JSON output support.

    Owner: ai segment only. No other segment instantiates this directly;
    they call agent facades in src/ai/agents/.

    Lifecycle:
        Designed to be used as a long-lived singleton (bootstrap pattern).
        httpx.AsyncClient is created eagerly in __init__ — no need to wrap
        in `async with` at the call site.

        async with AIClient(...) as c:  # still supported
            ...

        # OR as singleton (preferred for bootstrap):
        client = AIClient(api_key)
        await client.chat(...)
        await client.aclose()  # call once on shutdown

    response_format note (2026-05):
        sonar-pro does NOT support {"type": "json_object"}.
        Supported values: 'text', 'json_schema', 'regex' (or omit entirely).
        chat() enforces JSON output via system_prompt instruction instead.
        chat_completion() still accepts response_format for explicit overrides.

    max_tokens defaults:
        DEFAULT_MAX_TOKENS = 4096  — baseline for most agents.
        COMPLEX_MAX_TOKENS  = 8192 — for agents with many assumptions/fields
            (stress_test, briefing). Pass explicitly via max_tokens param.
    """

    BASE_URL = "https://api.perplexity.ai"
    DEFAULT_MODEL = "sonar-pro"
    DEFAULT_MAX_TOKENS = 4096    # raised from 2048 — prevents truncation on medium outputs
    COMPLEX_MAX_TOKENS = 8192    # for stress_test / briefing with many fields

    def __init__(self, api_key: str, timeout: float = 60.0) -> None:
        self._api_key = api_key
        self._timeout = timeout
        self._http: httpx.AsyncClient | None = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            timeout=self._timeout,
        )

    async def __aenter__(self) -> "AIClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying httpx client. Call once on application shutdown."""
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    @retry(
        retry=retry_if_exception_type((AIRateLimitError, AIUnavailableError)),
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=2, max=8),
        reraise=True,
    )
    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        response_format: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Low-level chat completion — returns raw API response dict.

        Args:
            messages: List of {role, content} dicts.
            model: Override default model.
            temperature: Sampling temperature (lower = more deterministic).
            max_tokens: Max tokens in response. Default: DEFAULT_MAX_TOKENS (4096).
                Use COMPLEX_MAX_TOKENS (8192) for stress_test / briefing agents.
            response_format: Optional override. sonar-pro supports only
                'text', 'json_schema', 'regex'. Do NOT pass
                {"type": "json_object"} — it will cause HTTP 400.
                Omit for standard text/JSON-via-prompt output.

        Returns:
            Raw API response dict.

        Raises:
            RuntimeError: If called after aclose().
            AIRateLimitError: On HTTP 429.
            AIUnavailableError: On HTTP 5xx.
            AIError: On other API errors.
        """
        if self._http is None:
            raise RuntimeError(
                "AIClient has been closed. Do not call chat_completion() after aclose()."
            )

        payload: dict[str, Any] = {
            "model": model or self.DEFAULT_MODEL,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            payload["response_format"] = response_format

        logger.debug("ai_client.request", model=payload["model"], message_count=len(messages))

        try:
            response = await self._http.post("/chat/completions", json=payload)
        except httpx.TimeoutException as exc:
            raise AIUnavailableError("Request timed out") from exc
        except httpx.RequestError as exc:
            raise AIError(f"Network error: {exc}") from exc

        if response.status_code == 429:
            raise AIRateLimitError("Rate limited")
        if response.status_code in _5XX_STATUS:
            raise AIUnavailableError(f"HTTP {response.status_code}")
        if response.status_code >= 400:
            raise AIError(f"API error {response.status_code}: {response.text}")

        result: dict[str, Any] = response.json()
        logger.debug(
            "ai_client.response",
            model=result.get("model"),
            usage=result.get("usage"),
        )
        return result

    async def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        response_schema: Type[T],
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> T:
        """High-level chat — builds messages, calls API, parses into Pydantic schema.

        This is the primary interface for all agents. Agents should use this
        instead of calling chat_completion() directly.

        JSON output is enforced by prepending a JSON instruction to
        system_prompt. No response_format is sent to the API — sonar-pro
        does not support json_object format and will return HTTP 400.

        Args:
            system_prompt: System instruction string.
            user_prompt: User message string.
            response_schema: Pydantic BaseModel class to parse response into.
            model: Override default model.
            temperature: Sampling temperature.
            max_tokens: Max tokens in response. Default: DEFAULT_MAX_TOKENS (4096).
                Pass AIClient.COMPLEX_MAX_TOKENS (8192) for agents with many
                fields: stress_test (5+ assumptions), briefing, sector_rotation.

        Returns:
            Parsed instance of response_schema.

        Raises:
            AIError: If response cannot be parsed into response_schema.
        """
        # Prepend JSON instruction — sonar-pro respects this reliably
        json_instruction = (
            "You MUST respond with valid JSON only. "
            "No markdown, no code fences, no explanation outside JSON."
        )
        full_system = f"{json_instruction}\n\n{system_prompt}"

        messages = [
            {"role": "system", "content": full_system},
            {"role": "user", "content": user_prompt},
        ]
        # Do NOT pass response_format — sonar-pro rejects json_object (HTTP 400)
        raw = await self.chat_completion(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        content = self.extract_text(raw)

        # Strip markdown fences if model wraps output anyway
        content = _strip_json_fences(content)
        # Strip citation markers inserted by web-search models (e.g. [1][2])
        content = _clean_citations(content)

        try:
            return response_schema.model_validate(json.loads(content))
        except Exception as exc:
            raise AIError(
                f"Failed to parse response into {response_schema.__name__}: {exc}\nRaw: {content}"
            ) from exc

    async def structured_call(self, spec: "AISpec", user_prompt: str) -> Any:
        """Convenience wrapper: call chat() using an AISpec bundle.

        Agents that declare a module-level SPEC (AISpec) use this instead of
        calling chat() with individual parameters. Keeps agent call sites clean
        and ensures spec params are always applied consistently.

        Args:
            spec:        AISpec instance declared in the prompt pack.
            user_prompt: User message string built by build_user_prompt().

        Returns:
            Parsed instance of spec.output_schema.

        Raises:
            AIError: propagated from chat().
        """
        return await self.chat(
            system_prompt=spec.system_prompt,
            user_prompt=user_prompt,
            response_schema=spec.output_schema,
            temperature=spec.temperature,
            max_tokens=spec.max_tokens,
        )

    def extract_text(self, response: dict[str, Any]) -> str:
        """Extract the assistant message content from a chat completion response."""
        try:
            return str(response["choices"][0]["message"]["content"])
        except (KeyError, IndexError) as exc:
            raise AIError(f"Unexpected response shape: {response}") from exc


def _strip_json_fences(text: str) -> str:
    """Remove markdown code fences that some models wrap JSON output in."""
    text = text.strip()
    if text.startswith("```"):
        # Remove opening fence (```json or ```)
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1:]
        # Remove closing fence
        if text.endswith("```"):
            text = text[:-3].rstrip()
    return text


_CITATION_RE = _re.compile(r'\[\d+\]')


def _clean_citations(text: str) -> str:
    """Strip inline citation markers inserted by web-search models (e.g. sonar-pro).

    Models like Perplexity sonar-pro append [1], [2][3] etc. inside JSON string
    values.  These are syntactically valid in prose but break JSON parsing.

    Strategy: remove every occurrence of [<digits>] globally.  This is safe
    because no JSON field name or numeric value contains this pattern.
    """
    return _CITATION_RE.sub('', text)


@dataclass(frozen=True)
class AISpec:
    """Bundle prompt spec cho một AI agent task.

    Dùng bởi prompt packs (src/ai/prompts/) để khai báo
    system_prompt + output schema + inference params tập trung.
    Agent gọi client.structured_call(spec, user_prompt) — params
    được áp dụng nhất quán mà không cần repeat tại mỗi call site.

    Example::

        # In prompt pack (src/ai/prompts/thesis_debate.py):
        SPEC = AISpec(
            system_prompt=_SYSTEM,
            output_schema=DebateOutput,
            temperature=0.4,
            max_tokens=1800,
        )

        # In agent:
        result = await self._client.structured_call(
            spec=SPEC,
            user_prompt=build_user_prompt(...),
        )
    """

    system_prompt: str
    output_schema: Type[BaseModel]
    temperature: float = 0.2
    max_tokens: int = field(default=AIClient.DEFAULT_MAX_TOKENS)
