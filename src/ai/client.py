from typing import Any

import httpx
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.platform.logging import get_logger

logger = get_logger(__name__)

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class PerplexityError(Exception):
    """Base error for Perplexity client."""


class PerplexityRateLimitError(PerplexityError):
    """HTTP 429 — back off and retry."""


class PerplexityUnavailableError(PerplexityError):
    """HTTP 5xx — upstream unavailable."""


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, (PerplexityRateLimitError, PerplexityUnavailableError))


class PerplexityClient:
    """Async Perplexity AI client with retry and structured JSON output support.

    Owner: ai segment only. No other segment instantiates this directly;
    they call agent facades in src/ai/agents/.
    """

    BASE_URL = "https://api.perplexity.ai"
    DEFAULT_MODEL = "llama-3.1-sonar-large-128k-online"

    def __init__(self, api_key: str, timeout: float = 30.0) -> None:
        self._api_key = api_key
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "PerplexityClient":
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            timeout=self._timeout,
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @retry(
        retry=retry_if_exception_type((PerplexityRateLimitError, PerplexityUnavailableError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
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
        """Send a chat completion request.

        Args:
            messages: List of {role, content} dicts.
            model: Override default model.
            temperature: Sampling temperature (lower = more deterministic).
            max_tokens: Max tokens in response.
            response_format: e.g. {"type": "json_object"} for structured output.

        Returns:
            Raw API response dict.

        Raises:
            PerplexityRateLimitError: On HTTP 429.
            PerplexityUnavailableError: On HTTP 5xx.
            PerplexityError: On other API errors.
        """
        assert self._client is not None, "Use as async context manager"

        payload: dict[str, Any] = {
            "model": model or self.DEFAULT_MODEL,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            payload["response_format"] = response_format

        logger.debug("perplexity.request", model=payload["model"], message_count=len(messages))

        try:
            response = await self._client.post("/chat/completions", json=payload)
        except httpx.TimeoutException as exc:
            raise PerplexityUnavailableError("Request timed out") from exc
        except httpx.RequestError as exc:
            raise PerplexityError(f"Network error: {exc}") from exc

        if response.status_code == 429:
            raise PerplexityRateLimitError("Rate limited")
        if response.status_code in _RETRYABLE_STATUS:
            raise PerplexityUnavailableError(f"HTTP {response.status_code}")
        if response.status_code >= 400:
            raise PerplexityError(f"API error {response.status_code}: {response.text}")

        result: dict[str, Any] = response.json()
        logger.debug(
            "perplexity.response",
            model=result.get("model"),
            usage=result.get("usage"),
        )
        return result

    def extract_text(self, response: dict[str, Any]) -> str:
        """Extract the assistant message content from a chat completion response."""
        try:
            return str(response["choices"][0]["message"]["content"])
        except (KeyError, IndexError) as exc:
            raise PerplexityError(f"Unexpected response shape: {response}") from exc
