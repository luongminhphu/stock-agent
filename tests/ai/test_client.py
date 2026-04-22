import pytest

from src.ai.client import (
    PerplexityClient,
    PerplexityError,
)


@pytest.fixture
def client() -> PerplexityClient:
    return PerplexityClient(api_key="pplx-test")


async def test_extract_text_valid(client: PerplexityClient) -> None:
    response = {"choices": [{"message": {"content": "hello"}}]}
    assert client.extract_text(response) == "hello"


async def test_extract_text_invalid_shape(client: PerplexityClient) -> None:
    with pytest.raises(PerplexityError):
        client.extract_text({"choices": []})


async def test_chat_completion_raises_without_context_manager(client: PerplexityClient) -> None:
    with pytest.raises(AssertionError):
        await client.chat_completion(messages=[{"role": "user", "content": "hi"}])
