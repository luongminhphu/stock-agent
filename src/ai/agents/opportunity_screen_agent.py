"""OpportunityScreenAgent — ai segment.

Owner: ai segment.
Called by: IntelligenceEngine._build_agent_tasks() when
           snapshot.market.opportunity_count > 0.

Responsibility:
  Cross-check market screen candidates against the investor's PORTFOLIO
  (open positions + sector weights) to detect:
    - Overlap: candidate already held → sizing / averaging opportunity.
    - Sector concentration: adding candidate would push a sector above 50%.
    - Portfolio conflict: candidate in same sector as a losing position.

  This agent COMPLEMENTS OpportunityAnalysisHandler (which cross-checks
  vs watchlist+thesis via event bus). This agent runs INSIDE the engine
  loop and produces a structured output that feeds IntelligenceReport.

Non-responsibilities:
  - Does NOT subscribe to events — it is called directly by the engine.
  - Does NOT send Discord messages — bot adapter handles delivery.
  - Does NOT re-run the market screen — it receives candidates_payload
    from snapshot.market (populated by SystemSnapshotBuilder).

Input: OpportunityScreenContext (see dataclass below)
Output: OpportunityScreenOutput (Pydantic, engine-compatible)

Graceful degrade:
  Returns None on any AI/parse failure — engine pipeline is unaffected.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from src.platform.logging import get_logger

if TYPE_CHECKING:
    from src.ai.client import AIClient

logger = get_logger(__name__)

# ── concentration thresholds (mirror briefing service) ───────────────────────
_CONCENTRATION_WARN_PCT = 35.0   # cross-check hint
_CONCENTRATION_ALERT_PCT = 50.0  # hard warning


# ── input context ─────────────────────────────────────────────────────────────

@dataclass
class OpportunityScreenContext:
    """Structured input for OpportunityScreenAgent.

    Built by IntelligenceEngine._build_agent_tasks() from snapshot data.
    """

    candidates_payload: tuple[str, ...] = field(default_factory=tuple)
    """Compact candidate lines from ScreenCandidate.format_for_prompt()."""

    screen_criteria: str = ""
    """Comma-joined unique criteria across candidates."""

    trading_date: str = ""
    """YYYY-MM-DD of the scan."""

    # Portfolio context
    open_tickers: list[str] = field(default_factory=list)
    """Tickers with open positions."""

    sector_weights: dict[str, float] = field(default_factory=dict)
    """sector → weight % (based on cost basis or market value)."""

    sector_per_ticker: dict[str, str] = field(default_factory=dict)
    """ticker → sector_name for open positions."""


# ── output schema ─────────────────────────────────────────────────────────────

class OpportunityScreenOutput(BaseModel):
    """Structured output from OpportunityScreenAgent.

    Consumed by:
      - IntelligenceEngine._synthesize_agent_outputs() → IntelligenceReport
      - Bot / readmodel (via IntelligenceEngineCompletedEvent)
    """

    verdict: str = Field(
        default="",
        description="1 câu tóm tắt kết quả cross-check (Vietnamese)",
        max_length=200,
    )
    top_candidates: list[str] = Field(
        default_factory=list,
        description="Tickers đáng chú ý nhất (tối đa 5), ưu tiên theo mức độ liên quan",
    )
    portfolio_overlap: list[str] = Field(
        default_factory=list,
        description="Candidates đã có trong danh mục hiện tại",
    )
    concentration_warnings: list[str] = Field(
        default_factory=list,
        description="Cảnh báo nếu thêm candidate sẽ gây tập trung ngành vượt ngưỡng",
    )
    action: str = Field(
        default="",
        description="Hành động cụ thể tiếp theo (Vietnamese, dưới 150 ký tự)",
        max_length=300,
    )
    reasoning_summary: str = Field(
        default="",
        description="2-3 câu giải thích logic cross-check (Vietnamese)",
        max_length=600,
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


# ── system / user prompts ─────────────────────────────────────────────────────

_SYSTEM_PROMPT = """Bạn là AI phân tích đầu tư cho nhà đầu tư chứng khoán Việt Nam.

Nhiệm vụ: Cross-check danh sách cơ hội từ market screen với danh mục hiện tại của nhà đầu tư.
Phát hiện:
  1. OVERLAP: Candidate đã có trong danh mục → cơ hội tăng vị thế hoặc averaging.
  2. CONCENTRATION: Thêm candidate vào sẽ đẩy tỷ trọng ngành vượt 35% (cảnh báo) hoặc 50% (nguy hiểm).
  3. CONFLICT: Candidate cùng ngành với vị thế đang lỗ — rủi ro tập trung thua lỗ.

Output PHẢI là JSON hợp lệ theo đúng schema sau (không thêm field lạ):
{
  "verdict": "string — 1 câu tóm tắt (Vietnamese)",
  "top_candidates": ["list ticker, tối đa 5"],
  "portfolio_overlap": ["ticker đã có trong danh mục"],
  "concentration_warnings": ["chuỗi cảnh báo nếu có, e.g. 'Thêm VCB: Banking lên 65.3%'"],
  "action": "string — hành động cụ thể (Vietnamese)",
  "reasoning_summary": "string — 2-3 câu giải thích",
  "confidence": 0.0
}

Quy tắc:
- Chỉ dùng ticker từ danh sách candidates cung cấp.
- confidence: 0.0–1.0.
- Nếu không có overlap hoặc rủi ro nào, nêu rõ trong verdict.
- action phải cụ thể, không chung chung.
- Toàn bộ string values phải bằng Tiếng Việt.
"""


def _build_user_prompt(ctx: OpportunityScreenContext) -> str:
    """Render OpportunityScreenContext into an AI-ready prompt."""
    candidates_block = (
        "\n".join(ctx.candidates_payload)
        if ctx.candidates_payload
        else "(không có candidate)"
    )

    # Portfolio block
    if ctx.open_tickers:
        ticker_lines = []
        for t in ctx.open_tickers:
            sector = ctx.sector_per_ticker.get(t, "không phân loại")
            ticker_lines.append(f"  - {t} [{sector}]")
        portfolio_block = "Vị thế đang mở:\n" + "\n".join(ticker_lines)
    else:
        portfolio_block = "Vị thế đang mở: Chưa có vị thế nào."

    # Sector weight block
    if ctx.sector_weights:
        weight_lines = []
        for sector, pct in sorted(ctx.sector_weights.items(), key=lambda x: -x[1]):
            flag = " ⚠ CONCENTRATION" if pct >= _CONCENTRATION_ALERT_PCT else (
                " ← cross-check" if pct >= _CONCENTRATION_WARN_PCT else ""
            )
            weight_lines.append(f"  {sector}: {pct:.1f}%{flag}")
        sector_block = "Tỷ trọng ngành hiện tại:\n" + "\n".join(weight_lines)
    else:
        sector_block = "Tỷ trọng ngành: Chưa có dữ liệu (danh mục trống)."

    return f"""Ngày giao dịch: {ctx.trading_date or "hôm nay"}
Tiêu chí screen: {ctx.screen_criteria or "tiêu chuẩn"}

=== CÁC CƠ HỘI TỪ MARKET SCREEN (xếp theo composite score) ===
{candidates_block}

=== DANH MỤC HIỆN TẠI ===
{portfolio_block}

{sector_block}

Hãy cross-check candidates với danh mục và trả về JSON theo schema đã cho."""


# ── agent ─────────────────────────────────────────────────────────────────────

class OpportunityScreenAgent:
    """Cross-check screen candidates vs open portfolio positions + sector weights.

    Called directly by IntelligenceEngine._build_agent_tasks().

    Usage::

        agent = OpportunityScreenAgent(ai_client)
        output = await agent.run(ctx)
        # output is OpportunityScreenOutput | None
    """

    def __init__(self, ai_client: "AIClient") -> None:
        self._client = ai_client

    async def run(self, ctx: OpportunityScreenContext) -> OpportunityScreenOutput | None:
        """Run cross-check. Returns None on failure (graceful degrade)."""
        if not ctx.candidates_payload:
            logger.debug("opportunity_screen_agent.no_candidates_skip")
            return None

        try:
            user_prompt = _build_user_prompt(ctx)
            api_resp = await self._client.chat_completion(
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user",   "content": user_prompt},
                ],
                temperature=0.2,
            )
            raw = self._client.extract_text(api_resp)
            data = _parse_output(raw)
            output = OpportunityScreenOutput(**data)
            logger.info(
                "opportunity_screen_agent.done",
                verdict=output.verdict,
                top_candidates=output.top_candidates,
                overlap_count=len(output.portfolio_overlap),
                concentration_warnings=len(output.concentration_warnings),
                confidence=output.confidence,
            )
            return output

        except Exception as exc:
            logger.warning(
                "opportunity_screen_agent.failed",
                error=str(exc),
            )
            return None


# ── helpers ───────────────────────────────────────────────────────────────────

def _parse_output(raw: str) -> dict[str, Any]:
    """Parse AI JSON output. Raises ValueError on failure."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise ValueError(f"Cannot parse AI output as JSON: {raw[:200]}")


def build_opportunity_screen_context(
    snapshot_market_opportunity_count: int,
    snapshot_market_top_tickers: list[str],
    candidates_payload: tuple[str, ...],
    screen_criteria: str,
    trading_date: str,
    portfolio_context: Any,  # src.portfolio.models.PortfolioContext
) -> OpportunityScreenContext | None:
    """Build OpportunityScreenContext from engine snapshot + portfolio context.

    Returns None when there are no candidates to cross-check.
    Called by IntelligenceEngine._build_agent_tasks().

    Args:
        snapshot_market_opportunity_count: snapshot.market.opportunity_count
        snapshot_market_top_tickers: snapshot.market.top_opportunity_tickers
        candidates_payload: tuple of format_for_prompt() strings (from snapshot or event)
        screen_criteria: comma-joined criteria string
        trading_date: YYYY-MM-DD
        portfolio_context: PortfolioContext instance (from portfolio segment)
    """
    if snapshot_market_opportunity_count == 0 and not candidates_payload:
        return None

    # Build candidates_payload from top_tickers if not provided directly
    effective_payload = candidates_payload
    if not effective_payload and snapshot_market_top_tickers:
        # Fallback: use ticker list as minimal payload
        effective_payload = tuple(snapshot_market_top_tickers)

    if not effective_payload:
        return None

    # Extract portfolio data
    open_tickers: list[str] = []
    sector_per_ticker: dict[str, str] = {}
    sector_weights: dict[str, float] = {}

    if portfolio_context is not None:
        open_positions = getattr(portfolio_context, "open_positions", []) or []
        for pos in open_positions:
            ticker = getattr(pos, "ticker", "")
            sector = getattr(pos, "sector", None) or "không phân loại"
            if ticker:
                open_tickers.append(ticker)
                sector_per_ticker[ticker] = sector
        sector_weights = dict(getattr(portfolio_context, "sector_weights", {}) or {})

    return OpportunityScreenContext(
        candidates_payload=effective_payload,
        screen_criteria=screen_criteria,
        trading_date=trading_date,
        open_tickers=open_tickers,
        sector_weights=sector_weights,
        sector_per_ticker=sector_per_ticker,
    )
