"""Integration tests for platform.bootstrap wiring.

All tests use ENVIRONMENT=test so build_adapter() returns MockAdapter
and no real HTTP clients are created.
"""

from __future__ import annotations

import pytest

from src.platform import bootstrap as _bs


@pytest.fixture(autouse=True)
def reset_between_tests():
    """Ensure singletons are clean before and after every test."""
    _bs.reset_singletons()
    yield
    _bs.reset_singletons()


# ---------------------------------------------------------------------------
# bootstrap() — initialisation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bootstrap_sets_all_singletons():
    """After bootstrap(), all four core singletons must be non-None."""
    await _bs.bootstrap()

    assert _bs._quote_service is not None
    assert _bs._perplexity_client is not None
    assert _bs._thesis_review_agent is not None
    assert _bs._briefing_agent is not None


@pytest.mark.asyncio
async def test_bootstrap_is_idempotent():
    """Calling bootstrap() twice must not create new instances."""
    await _bs.bootstrap()
    qs_first = _bs._quote_service
    pc_first = _bs._perplexity_client

    await _bs.bootstrap()  # second call

    assert _bs._quote_service is qs_first
    assert _bs._perplexity_client is pc_first


# ---------------------------------------------------------------------------
# get_*() guards — must raise before bootstrap()
# ---------------------------------------------------------------------------


def test_get_quote_service_raises_before_bootstrap():
    with pytest.raises(RuntimeError, match="bootstrap"):
        _bs.get_quote_service()


def test_get_perplexity_client_raises_before_bootstrap():
    with pytest.raises(RuntimeError, match="bootstrap"):
        _bs.get_perplexity_client()


def test_get_thesis_review_agent_raises_before_bootstrap():
    with pytest.raises(RuntimeError, match="bootstrap"):
        _bs.get_thesis_review_agent()


def test_get_briefing_agent_raises_before_bootstrap():
    with pytest.raises(RuntimeError, match="bootstrap"):
        _bs.get_briefing_agent()


# ---------------------------------------------------------------------------
# get_*() — return correct types after bootstrap()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_quote_service_returns_quote_service():
    await _bs.bootstrap()
    from src.market.quote_service import QuoteService

    assert isinstance(_bs.get_quote_service(), QuoteService)


@pytest.mark.asyncio
async def test_get_perplexity_client_returns_client():
    await _bs.bootstrap()
    from src.ai.client import PerplexityClient

    assert isinstance(_bs.get_perplexity_client(), PerplexityClient)


@pytest.mark.asyncio
async def test_get_thesis_review_agent_returns_agent():
    await _bs.bootstrap()
    from src.ai.agents.thesis_review import ThesisReviewAgent

    assert isinstance(_bs.get_thesis_review_agent(), ThesisReviewAgent)


@pytest.mark.asyncio
async def test_get_briefing_agent_returns_agent():
    await _bs.bootstrap()
    from src.ai.agents.briefing import BriefingAgent

    assert isinstance(_bs.get_briefing_agent(), BriefingAgent)


# ---------------------------------------------------------------------------
# reset_singletons()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_clears_all_singletons():
    await _bs.bootstrap()
    _bs.reset_singletons()

    assert _bs._quote_service is None
    assert _bs._perplexity_client is None
    assert _bs._thesis_review_agent is None
    assert _bs._briefing_agent is None
