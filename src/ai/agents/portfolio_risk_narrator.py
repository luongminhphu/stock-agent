"""
Portfolio Risk Narrator Agent.

Owner: ai segment.
Consumed by:
  - BriefingService (attaches output to BriefOutput.portfolio_narrative)
  - Bot /portfolio command (on-demand portfolio risk narrative)

Responsibility boundary:
  - Accepts a PortfolioRiskNarratorContext (rule-based inputs).
  - Calls AI to produce a structured narrative (PortfolioRiskNarrativeOutput).
  - Does NOT write to DB, does NOT modify portfolio state, does NOT trigger alerts.
  - Caller is responsible for attaching result to BriefOutput.portfolio_narrative.

Graceful degrade:
  - Returns None on any AI/parse failure — briefing pipeline is unaffected.
  - Caller checks `result is None` and falls back to portfolio_summary flat list.

Input contract:
  - PortfolioRiskNote       → rule-based pre-computed portfolio context
  - SignalEngineOutput      → ranked_signals + risk_alerts for narrative context
  - stress_impact_note      → optional string from StressTestOutput.portfolio_impact_note
  - portfolio_date          → YYYY-MM-DD, for snapshot labelling

Output: PortfolioRiskNarrativeOutput
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from src.ai.client import AIClient
from src.ai.schemas._base import PortfolioRiskNote
from src.ai.schemas.portfolio_risk import PortfolioRiskNarrativeOutput
from src.ai.schemas.signal_engine import RankedSignal, RiskAlert
from src.platform.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Input context
# ---------------------------------------------------------------------------


@dataclass
class PortfolioRiskNarratorContext:
    """Structured input for PortfolioRiskNarratorAgent.

    All fields are rule-based / pre-computed. AI reads this context
    and writes the narrative layer on top.
    """

    portfolio_note: PortfolioRiskNote
    """Rule-based concentration / loss / misalignment summary."""

    ranked_signals: list[RankedSignal] = field(default_factory=list)
    """Top signals from SignalEngineOutput.ranked_signals (pass top 5 max)."""

    risk_alerts: list[RiskAlert] = field(default_factory=list)
    """Cross-segment risk alerts from SignalEngineOutput.risk_alerts."""

    stress_impact_note: str = ""
    """Optional: StressTestOutput.portfolio_impact_note if available."""

    portfolio_date: str = ""
    """YYYY-MM-DD. Used for snapshot labelling in the narrative."""


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
Bạn là Portfolio Risk Narrator — chuyên gia đọc dữ liệu danh mục và viết narrative risk có cấu trúc.

Nhiệm vụ:
1. Đọc dữ liệu portfolio đã được tính toán sẵn (rule-based).
2. Tổ chức rủi ro thành tối đa 4 chapters, mỗi chapter = 1 risk theme.
3. Viết opening_line 1 câu standalone, actionable.
4. Đề xuất immediate_actions cụ thể (2-3 việc hôm nay).
5. Liệt kê watch_next_session (tối đa 3 mốc cụ thể cần quan sát phiên tới).

Risk themes hợp lệ:
  CONCENTRATION, THESIS_DRIFT, DRAWDOWN, SECTOR_OVEREXPOSE, CASH_DRAG, SIGNAL_CONFLICT

Trả về JSON theo schema sau (không thêm markdown, không thêm giải thích ngoài JSON):
{
  "overall_risk_level": "LOW | MEDIUM | HIGH | CRITICAL",
  "risk_score": <int 0-100>,
  "opening_line": "<1 câu tiếng Việt, standalone, actionable>",
  "portfolio_verdict": "BULLISH | BEARISH | NEUTRAL",
  "chapters": [
    {
      "theme": "<RISK_THEME>",
      "severity": "LOW | MEDIUM | HIGH | CRITICAL",
      "headline": "<1 câu mô tả risk>",
      "affected_tickers": ["<TICKER>"],
      "evidence": "<dữ liệu cụ thể hỗ trợ headline>",
      "suggested_action": "<hành động gợi ý ngắn gọn>"
    }
  ],
  "immediate_actions": ["<việc cần làm hôm nay>"],
  "watch_next_session": ["<mốc cần quan sát phiên tới>"],
  "confidence": <float 0.0-1.0>
}

Nguyên tắc:
- Nếu không có rủi ro đáng kể: chapters = [], risk_score <= 20, overall_risk_level = LOW.
- Chapters sorted severity DESC (CRITICAL → HIGH → MEDIUM → LOW).
- Không bịa tín hiệu. Chỉ dùng dữ liệu được cung cấp.
- Ngắn gọn, sắc nét, actionable. Không viết chung chung.
"""


def _build_user_prompt(ctx: PortfolioRiskNarratorContext) -> str:
    note = ctx.portfolio_note

    lines: list[str] = [
        f"## Portfolio Risk Context — {ctx.portfolio_date or 'N/A'}",
        "",
        "### Rule-based portfolio note",
        f"- Position count      : {note.position_count}",
        f"- Total PnL %         : {note.total_pnl_pct if note.total_pnl_pct is not None else 'N/A'}",
        f"- Concentration risk  : {', '.join(note.top_concentration) or 'none'}",
        f"- Losing positions    : {', '.join(note.losing_positions) or 'none'}",
        f"- Misaligned (BEARISH verdict but held): {', '.join(note.misaligned_positions) or 'none'}",
    ]

    if ctx.risk_alerts:
        lines += ["", "### Risk alerts from SignalEngine"]
        for a in ctx.risk_alerts[:5]:
            lines.append(f"- [{a.severity}] {a.ticker}: {a.alert_type} — {a.description}")

    if ctx.ranked_signals:
        lines += ["", "### Top signals (portfolio tickers only)"]
        for s in ctx.ranked_signals[:5]:
            conflict = f" | conflict: {s.thesis_conflict_note}" if s.thesis_conflict_note else ""
            lines.append(
                f"- {s.ticker} [{s.urgency}] {s.verdict} — {s.trigger_reason}{conflict}"
            )

    if ctx.stress_impact_note:
        lines += ["", "### Stress test portfolio impact", ctx.stress_impact_note]

    lines += ["", "Dựa trên các dữ liệu trên, viết portfolio risk narrative theo schema yêu cầu."]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class PortfolioRiskNarratorAgent:
    """Produce a structured portfolio risk narrative using AI.

    Usage::

        agent = PortfolioRiskNarratorAgent(ai_client)
        result = await agent.narrate(ctx)
        if result:
            brief_output.portfolio_narrative = result
    """

    def __init__(self, ai_client: AIClient) -> None:
        self._client = ai_client

    async def narrate(
        self, ctx: PortfolioRiskNarratorContext
    ) -> PortfolioRiskNarrativeOutput | None:
        """Run the narrator. Returns None on any failure (graceful degrade)."""
        try:
            user_prompt = _build_user_prompt(ctx)
            api_resp = await self._client.chat_completion(
                messages=[
                    {\"role\": \"system\", \"content\": _SYSTEM_PROMPT},
                    {\"role\": \"user\",   \"content\": user_prompt},
                ],
                temperature=0.3,
            )
            raw = self._client.extract_text(api_resp)
            data = json.loads(raw)
            result = PortfolioRiskNarrativeOutput(**data)
            logger.info(
                "portfolio_risk_narrator.done",
                portfolio_date=ctx.portfolio_date,
                risk_score=result.risk_score,
                overall_risk_level=result.overall_risk_level,
                chapters=len(result.chapters),
                confidence=result.confidence,
            )
            return result
        except Exception as exc:
            logger.warning(
                "portfolio_risk_narrator.failed",
                portfolio_date=ctx.portfolio_date,
                error=str(exc),
            )
            return None
