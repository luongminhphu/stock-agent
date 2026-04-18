"""Unit tests for platform.health probes."""
from __future__ import annotations

import pytest

from src.platform import bootstrap as _bs
from src.platform.health import HealthStatus, check_liveness, check_readiness


@pytest.fixture(autouse=True)
def reset_between_tests():
    _bs.reset_singletons()
    yield
    _bs.reset_singletons()


# ---------------------------------------------------------------------------
# Liveness probe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_liveness_always_ok():
    report = await check_liveness()
    assert report.status == HealthStatus.OK
    assert report.checks["process"] == HealthStatus.OK


# ---------------------------------------------------------------------------
# Readiness probe — DB (SQLite in-memory via conftest)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_readiness_db_ok_before_bootstrap():
    """DB check passes even before bootstrap; singleton checks show DOWN."""
    report = await check_readiness()

    assert report.checks["database"] == HealthStatus.OK
    # Singletons not yet initialised
    assert report.checks["quote_service"] == HealthStatus.DOWN
    assert report.checks["perplexity_client"] == HealthStatus.DOWN
    assert report.checks["thesis_review_agent"] == HealthStatus.DOWN
    assert report.checks["briefing_agent"] == HealthStatus.DOWN
    assert report.status == HealthStatus.DEGRADED


@pytest.mark.asyncio
async def test_readiness_all_ok_after_bootstrap():
    """After bootstrap(), all checks must be OK → status OK."""
    await _bs.bootstrap()
    report = await check_readiness()

    assert report.checks["database"] == HealthStatus.OK
    assert report.checks["quote_service"] == HealthStatus.OK
    assert report.checks["perplexity_client"] == HealthStatus.OK
    assert report.checks["thesis_review_agent"] == HealthStatus.OK
    assert report.checks["briefing_agent"] == HealthStatus.OK
    assert report.status == HealthStatus.OK


@pytest.mark.asyncio
async def test_readiness_degraded_after_reset():
    """After reset, status reverts to DEGRADED."""
    await _bs.bootstrap()
    _bs.reset_singletons()
    report = await check_readiness()

    assert report.status == HealthStatus.DEGRADED
