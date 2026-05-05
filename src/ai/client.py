from __future__ import annotations

import json
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

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

T = TypeVar("T", bound=BaseModel)


class AIError(Exception):
    """Base error for AI client."""


class AIRateLimitError(AIError):
    """HTTP 429 — back off and retry."""


class AIUnavailableError(AIError):
    """HTTP 5xx — upstream unavailable."""


# Legacy aliases — kept for backward compatibility
PerplexityError = AIError
PerplexityRateLimitError = AIRateLimitError
PerplexityUnavailableError = AIUnavailableError


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, (AIRateLimitError, AIUnavailableError))


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
    """

    BASE_URL = "https://api.perplexity.ai"
    # sonar-pro: advanced search model with grounding (current as of 2026-04)
    # Replaces deprecated llama-3.1-sonar-large-128k-online
    # Ref: https://docs.perplexity.ai/docs/getting-started/models
    DEFAULT_MODEL = "sonar-pro"

    def __init__(self, api_key: str, timeout: float = 60.0) -> None:
        self._api_key = api_key
        self._timeout = timeout
        # Eager init — safe for singleton use without async with
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
        max_tokens: int = 2048,
        response_format: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Low-level chat completion — returns raw API response dict.

        Args:
            messages: List of {role, content} dicts.
            model: Override default model.
            temperature: Sampling temperature (lower = more deterministic).
            max_tokens: Max tokens in response.
            response_format: e.g. {"type": "json_object"} — only 'text',
                             'json_object', 'json_schema', 'regex' are
                             supported by Perplexity API.

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
        if response.status_code in _RETRYABLE_STATUS:
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
        max_tokens: int = 2048,
    ) -> T:
        """High-level chat — builds messages, calls API, parses into Pydantic schema.

        This is the primary interface for all agents. Agents should use this
        instead of calling chat_completion() directly.

        Args:
            system_prompt: System instruction string.
            user_prompt: User message string.
            response_schema: Pydantic BaseModel class to parse response into.
            model: Override default model.
            temperature: Sampling temperature.
            max_tokens: Max tokens in response.

        Returns:
            Parsed instance of response_schema.

        Raises:
            AIError: If response cannot be parsed into response_schema.
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        raw = await self.chat_completion(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        content = self.extract_text(raw)
        try:
            return response_schema.model_validate(json.loads(content))
        except Exception as exc:
            raise AIError(
                f"Failed to parse response into {response_schema.__name__}: {exc}\nRaw: {content}"
            ) from exc

    def extract_text(self, response: dict[str, Any]) -> str:
        """Extract the assistant message content from a chat completion response."""
        try:
            return str(response["choices"][0]["message"]["content"])
        except (KeyError, IndexError) as exc:
            raise AIError(f"Unexpected response shape: {response}") from exc


# Backward-compat alias — bootstrap.py and any legacy callers still work
PerplexityClient = AIClient
