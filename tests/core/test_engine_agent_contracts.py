"""mypy M2 — IntelligenceEngine gọi agents đúng contract thật (bug: import/chữ ký sai).

Fake AI client luôn raise → mọi agent rơi về fallback rule-based; test chỉ cần chứng minh
không còn ImportError/TypeError khi build + chạy task, và synthesizer đọc được output.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.core.engine import IntelligenceEngine, _run_with_timeout
from src.core.schemas import (
    InvalidationBatchOutput,
    MarketContext,
    PortfolioContext,
    SystemSnapshot,
    ThesisRef,
)


class _BrokenAIClient:
    async def chat_completion(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
        raise RuntimeError("ai down")

    def extract_text(self, resp):  # noqa: ANN001, ANN201
        raise RuntimeError("ai down")


def _snapshot() -> SystemSnapshot:
    return SystemSnapshot(
        thesis_due_review=[
            ThesisRef(thesis_id=1, ticker="HPG", days_overdue=40),
            ThesisRef(thesis_id=2, ticker="VCB", days_overdue=3),
        ],
        portfolio=PortfolioContext(total_positions=2, top_exposed_tickers=["HPG", "VCB"]),
        market=MarketContext(),
        captured_at=datetime.now(UTC),
    )


@pytest.mark.anyio
async def test_agent_tasks_build_and_run_without_contract_errors() -> None:
    engine = IntelligenceEngine(session=None, user_id="u-eng", ai_client=_BrokenAIClient())  # type: ignore[arg-type]
    snap = _snapshot()
    tasks = await engine._build_agent_tasks(snap, signals=[])
    names = [n for n, _ in tasks]
    assert {"thesis_judge", "invalidation_detector", "portfolio_risk_narrator"} <= set(names)

    results = {
        n: (r, err) for n, r, err in [await _run_with_timeout(c, n, timeout=10) for n, c in tasks]
    }
    # Không được lỗi kiểu "unexpected keyword argument" / ImportError — chỉ fallback hoặc None
    for name, (_, err) in results.items():
        assert not err or ("argument" not in err and "import" not in err.lower()), (name, err)

    judge, _ = results["thesis_judge"]
    assert judge is None or hasattr(judge, "verdict")
    inv, _ = results["invalidation_detector"]
    assert inv is None or isinstance(inv, InvalidationBatchOutput)
