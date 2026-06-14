"""RRGChartSummaryAgent — AI summary of the full RRG chart.

Owner: ai segment.
Called by: api/routes/rrg.py → GET /rrg/chart-summary
Output: RRGChartSummary — opportunities, risks, portfolio alert, rotate suggestion.

Design:
  - Single AI call per refresh cycle (not per-ticker).
  - Receives: all visible tickers with quadrant + trail velocity summary.
  - Receives: held tickers + P&L context (for portfolio-aware commentary).
  - Falls back to rule-based heuristic if AI fails — never raises.
"""

from __future__ import annotations

import structlog

from src.ai.schemas.rrg_chart_summary import RRGChartSummary, RRGTickerInsight

logger = structlog.get_logger(__name__)

_SYSTEM_PROMPT = """\
Bạn là chuyên gia phân tích kỹ thuật chứng khoán Việt Nam, chuyên về Relative Rotation Graph (RRG).

Nhiệm vụ: Đọc toàn bộ chart RRG và đưa ra nhận định hành động ngắn gọn, dễ hiểu cho nhà đầu tư không chuyên.

Nguyên tắc:
- Ưu tiên tickers đang hold trong danh mục khi đưa ra cảnh báo.
- Ngôn ngữ: tiếng Việt, đơn giản, không dùng thuật ngữ phức tạp.
- Mỗi insight tối đa 1 câu, tối đa 100 ký tự.
- Chỉ đề xuất rotate khi có tín hiệu rõ ràng.
- Trả về JSON theo schema RRGChartSummary.
"""


def _build_prompt(
    tickers_context: list[dict],
    held_tickers: list[str],
) -> str:
    lines = ["## Trạng thái RRG hiện tại\n"]
    for t in tickers_context:
        held_flag = " [ĐANG HOLD]" if t["ticker"] in held_tickers else ""
        vel_str   = f"velocity={t['velocity_dir']}" if t.get("velocity_dir") else ""
        lines.append(
            f"- {t['ticker']}{held_flag}: {t['quadrant'].upper()}"
            f" | RS-Ratio={t['rs_ratio']:.2f} RS-Mom={t['rs_momentum']:.2f}"
            + (f" | {vel_str}" if vel_str else "")
            + (f" | P&L {t['pct']:+.1f}%" if t.get("pct") is not None else "")
        )

    if held_tickers:
        lines.append(f"\n## Tickers đang hold: {', '.join(held_tickers)}")
    else:
        lines.append("\n## Danh mục: Chưa có vị thế")

    lines.append(
        "\nDựa vào dữ liệu trên, hãy phân tích và trả về JSON theo schema RRGChartSummary.\n"
        "Tập trung vào: cơ hội tốt nhất, rủi ro cao nhất, cảnh báo danh mục nếu có, "
        "và gợi ý rotate nếu rõ ràng."
    )
    return "\n".join(lines)


def _heuristic_fallback(
    tickers_context: list[dict],
    held_tickers: list[str],
) -> RRGChartSummary:
    """Rule-based fallback when AI fails."""
    opportunities: list[RRGTickerInsight] = []
    risks:         list[RRGTickerInsight] = []
    portfolio_alert = ""

    held_set = set(held_tickers)
    held_weak = [t for t in tickers_context if t["ticker"] in held_set and t["quadrant"] in ("weakening", "lagging")]

    for t in tickers_context:
        ticker = t["ticker"]
        q      = t["quadrant"]
        if q == "improving" and len(opportunities) < 2:
            opportunities.append(RRGTickerInsight(
                ticker=ticker,
                insight=f"{ticker} đang vào Improving — momentum đang tích cực.",
                action="WATCH",
            ))
        elif q == "leading" and len(opportunities) < 2:
            opportunities.append(RRGTickerInsight(
                ticker=ticker,
                insight=f"{ticker} đang Leading — giữ vị thế nếu momentum còn mạnh.",
                action="HOLD",
            ))
        elif q == "weakening" and len(risks) < 2:
            risks.append(RRGTickerInsight(
                ticker=ticker,
                insight=f"{ticker} đang Weakening — theo dõi chặt, cân nhắc giảm.",
                action="WATCH" if ticker not in held_set else "REDUCE",
            ))
        elif q == "lagging" and len(risks) < 2:
            risks.append(RRGTickerInsight(
                ticker=ticker,
                insight=f"{ticker} đang Lagging — tránh mua thêm, xem xét thoát.",
                action="AVOID",
            ))

    if len(held_weak) >= 2:
        names = ", ".join(t["ticker"] for t in held_weak)
        portfolio_alert = f"{names} cùng Weakening — rủi ro tập trung trong danh mục."

    leading_count   = sum(1 for t in tickers_context if t["quadrant"] == "leading")
    improving_count = sum(1 for t in tickers_context if t["quadrant"] == "improving")
    total           = len(tickers_context) or 1
    if leading_count + improving_count > total * 0.5:
        market_read = "Thị trường đang có xu hướng tích cực — nhiều ticker trong Leading/Improving."
    else:
        market_read = "Thị trường đang phân hóa — cần chọn lọc kỹ từng ticker."

    return RRGChartSummary(
        opportunities=opportunities,
        risks=risks,
        portfolio_alert=portfolio_alert,
        market_read=market_read,
    )


class RRGChartSummaryAgent:
    """Generate a chart-level AI summary for the full RRG."""

    def __init__(self, ai_client: object) -> None:
        self._ai = ai_client

    async def analyze(
        self,
        tickers_context: list[dict],
        held_tickers: list[str],
    ) -> RRGChartSummary:
        """
        Args:
            tickers_context: list of {ticker, quadrant, rs_ratio, rs_momentum,
                             velocity_dir, pct (unrealized_pct or None)}
            held_tickers: list of ticker strings currently in portfolio
        Returns:
            RRGChartSummary — never raises.
        """
        if not tickers_context:
            return RRGChartSummary(market_read="Chưa có dữ liệu ticker để phân tích.")

        user_prompt = _build_prompt(tickers_context, held_tickers)

        try:
            result: RRGChartSummary = await self._ai.complete(  # type: ignore[attr-defined]
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                response_schema=RRGChartSummary,
            )
            logger.info(
                "rrg_chart_summary_agent.completed",
                ticker_count=len(tickers_context),
                held_count=len(held_tickers),
                has_rotate=bool(result.rotate_from),
            )
            return result
        except Exception as exc:
            logger.warning(
                "rrg_chart_summary_agent.ai_failed",
                error=str(exc),
                fallback="heuristic",
            )
            return _heuristic_fallback(tickers_context, held_tickers)
