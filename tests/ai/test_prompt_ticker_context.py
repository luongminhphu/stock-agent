"""Wave C1: section 'Bối cảnh kỹ thuật' trong prompt thesis_review và pretrade.

Đảm bảo: (1) default rỗng → prompt không đổi, (2) có ticker_context → xuất hiện
đúng vị trí, kèm cảnh báo stale/fallback.
"""

from __future__ import annotations

from src.ai.prompts.pretrade import build_pretrade_prompt
from src.ai.prompts.thesis_review import build_review_prompt, build_user_prompt

_CTX = (
    "VNM 70,000 (+0.72%) | MA20 68,500 · MA50 66,200 | RSI14 61.3 · ATR14 1,150 "
    "| Vol/TB20 1.35x | 52w 58,000–74,000 | trend UP · data live"
)

_REVIEW_KW = dict(
    ticker="VNM",
    thesis_title="Hồi phục biên lợi nhuận",
    thesis_summary="Giá sữa bột đầu vào giảm.",
    assumptions_with_ids=[{"id": 1, "description": "Giá nguyên liệu giảm 10%"}],
    catalysts_with_ids=[{"id": 2, "description": "KQKD Q3"}],
    triggered_catalysts_with_ids=[],
    current_price=70_000.0,
    entry_price=65_000.0,
    target_price=80_000.0,
)


class TestThesisReviewPrompt:
    def test_default_omits_section(self) -> None:
        core = build_user_prompt(**_REVIEW_KW)
        assert "Bối cảnh kỹ thuật" not in core
        full = build_review_prompt(**_REVIEW_KW)
        assert "Bối cảnh kỹ thuật" not in full

    def test_section_rendered_after_price_block(self) -> None:
        core = build_user_prompt(**_REVIEW_KW, ticker_context=_CTX)
        assert "### Bối cảnh kỹ thuật" in core
        assert _CTX in core
        assert "stale/fallback" in core
        # Sau khối giá, trước assumptions
        assert core.index("### Thông tin giá") < core.index("### Bối cảnh kỹ thuật")
        assert core.index("### Bối cảnh kỹ thuật") < core.index("### Assumptions")

    def test_full_prompt_forwards_context(self) -> None:
        full = build_review_prompt(**_REVIEW_KW, ticker_context=_CTX)
        assert _CTX in full
        # Vẫn trước output schema
        assert full.index(_CTX) < full.index("JSON")


class TestPretradePrompt:
    _KW = dict(
        ticker="VNM",
        price=70_000.0,
        change_pct=0.72,
        thesis_context="",
        signal_context="",
        brief_context="",
    )

    def test_default_omits_section(self) -> None:
        assert "BỐI CẢNH KỸ THUẬT" not in build_pretrade_prompt(**self._KW)

    def test_section_before_market_regime(self) -> None:
        p = build_pretrade_prompt(
            **self._KW, ticker_context=_CTX, market_context="VN-Index RISK_ON"
        )
        assert "=== BỐI CẢNH KỸ THUẬT ===" in p and _CTX in p
        assert p.index("=== BỐI CẢNH KỸ THUẬT ===") < p.index("=== THỊ TRƯỜNG CHUNG ===")
        assert p.index("=== BRIEF HÔM NAY ===") < p.index("=== BỐI CẢNH KỸ THUẬT ===")
