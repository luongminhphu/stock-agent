"""Unit tests for AIClient public API contract.

Wave 12.1 rewrite: module was refactored from PerplexityClient to AIClient
(see src/ai/client.py). Previous tests asserted the old constructor /
context-manager contract; the current contract is:

- chat_completion() is available immediately after __init__() — no
  explicit `async with` is required.
- extract_text() raises AIError on malformed response shape.
- Calling chat_completion() after aclose() raises RuntimeError.
"""

import pytest

from src.ai.client import AIClient, AIError


@pytest.fixture
async def client() -> AIClient:
    c = AIClient(api_key="pplx-test")
    yield c
    await c.aclose()


async def test_extract_text_valid(client: AIClient) -> None:
    response = {"choices": [{"message": {"content": "hello"}}]}
    assert client.extract_text(response) == "hello"


async def test_extract_text_invalid_empty_choices(client: AIClient) -> None:
    with pytest.raises(AIError):
        client.extract_text({"choices": []})


async def test_extract_text_invalid_missing_choices(client: AIClient) -> None:
    with pytest.raises(AIError):
        client.extract_text({})


async def test_chat_completion_raises_after_close(client: AIClient) -> None:
    """Contract: closed client must reject further calls instead of
    sending a request on a dead transport."""
    await client.aclose()
    with pytest.raises(RuntimeError, match="closed"):
        await client.chat_completion(messages=[{"role": "user", "content": "hi"}])
