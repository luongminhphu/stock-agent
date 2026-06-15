"""RRGRotationAgent — AI analysis of a single ticker's RRG position + trail.

Owner: ai segment.
Caller: api/routes/rrg.py  GET /api/v1/rrg/rotation/{ticker}

Input:
    - ticker, quadrant, rs_ratio, rs_momentum  (current position)
    - trail: list of {rs_ratio, rs_momentum}   (oldest → newest)
    - sector, company_name                     (from SymbolRegistry)
    - lookback_weeks                           (context for trail length)

Output: RRGRotationSignal

Boundary rules:
    - No DB access. No market data fetch. Pure AI reasoning on pre-computed RRG data.
    - Receives primitive dicts, not domain models.
    - Falls back to rule-based signal when LLM fails (non-blocking).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.ai.client import AIClient, AIError
from src.ai.schemas.rrg_rotation import RRGRotationSignal

logger = logging.getLogger(__name__)

_MAX_TOKENS = 512

_SYSTEM_PROMPT = """\
Bạn là chuyên gia phân tích kỹ thuật thị trường chứng khoán Việt Nam, chuyên về Relative Rotation Graph (RRG).

Nhiệm vụ: Phân tích vị trí và trajectory của một mã cổ phiếu trên RRG, phát hiện rotation pattern và đưa ra signal hành động.

## RRG Quadrant logic
- Leading   (RS-Ratio>100, RS-Mom>100): mạnh hơn thị trường, momentum tăng → tích cực
- Weakening (RS-Ratio>100, RS-Mom<100): mạnh hơn nhưng momentum đang giảm → cảnh báo
- Lagging   (RS-Ratio<100, RS-Mom<100): yếu hơn thị trường, momentum giảm → tránh
- Improving (RS-Ratio<100, RS-Mom>100): yếu hơn nhưng momentum đang hồi phục → cơ hội

## Pattern cần phát hiện
- ENTERING_LEADING:  trail vừa cross từ Improving sang Leading → entry signal mạnh
- EXITING_LEADING:   trail vừa cross từ Leading sang Weakening → cảnh báo chốt lời
- ENTERING_IMPROVING: trail vừa cross từ Lagging sang Improving → early recovery
- DEEP_LAGGING:      trail nằm sâu trong Lagging, không có dấu hiệu hồi → tránh
- WEAKENING_FAST:    trong Weakening, RS-Mom giảm nhanh → thoát sớm
- RECOVERY:          từ Lagging đang quay về Improving rõ ràng
- ROTATING:          trail đang di chuyển qua nhiều quadrant liên tiếp
- STABLE:            trail ổn định trong 1 quadrant, không có dấu hiệu chuyển

## Quy tắc output
- signal_reason: tối đa 120 ký tự, tiếng Việt, không nhắc giá cụ thể
- opportunity: chỉ điền khi có pattern rõ ràng (ENTERING_LEADING, ENTERING_IMPROVING, RECOVERY), để "" nếu không có
- confidence tối đa 0.85
- Không dùng markdown, không prose thêm

Output: JSON thuần, BẮT BUỘC đủ các fields sau:
{
  "ticker": "<TICKER>",
  "quadrant": "leading|weakening|lagging|improving",
  "pattern": "<PATTERN>",
  "signal": "BUY|WATCH|HOLD|REDUCE|AVOID",
  "signal_reason": "<lý do ngắn tiếng Việt>",
  "opportunity": "<mô tả hoặc chuỗi rỗng>",
  "risk": "<rủi ro chính hoặc chuỗi rỗng>",
  "next_watch": "<điều kiện cần theo dõi hoặc chuỗi rỗng>",
  "confidence": 0.0
}
"""


def _build_prompt(
    ticker: str,
    quadrant: str,
    rs_ratio: float,
    rs_momentum: float,
    trail: list[dict[str, float]],
    sector: str,
    company_name: str,
    lookback_weeks: int,
) -> str:
    # Summarise trail movement for the prompt
    trail_summary = []
    for i, pt in enumerate(trail):
        q = _classify_quadrant(pt["rs_ratio"], pt["rs_momentum"])
        trail_summary.append(
            f"  [{i+1}] R={pt['rs_ratio']:.2f} M={pt['rs_momentum']:.2f} → {q}"
        )

    # Detect cross events in trail
    crosses: list[str] = []
    prev_q = None
    for pt in trail:
        q = _classify_quadrant(pt["rs_ratio"], pt["rs_momentum"])
        if prev_q and q != prev_q:
            crosses.append(f"{prev_q}→{q}")
        prev_q = q

    return (
        f"## Mã phân tích: {ticker} ({company_name}, sector: {sector})\n"
        f"Lookback: {lookback_weeks} tuần\n\n"
        f"## Vị trí hiện tại\n"
        f"Quadrant: {quadrant}\n"
        f"RS-Ratio: {rs_ratio:.3f}  |  RS-Momentum: {rs_momentum:.3f}\n\n"
        f"## Trail (oldest → newest, {len(trail)} điểm)\n"
        + "\n".join(trail_summary) + "\n\n"
        f"## Quadrant transitions\n"
        + (", ".join(crosses) if crosses else "Không có cross event") + "\n\n"
        f"## Yêu cầu\n"
        f"Phân tích pattern, xác định signal và cơ hội rotation. "
        f"Trả JSON theo schema RRGRotationSignal."
    )


def _classify_quadrant(r: float, m: float) -> str:
    if r >= 100 and m >= 100:
        return "leading"
    if r >= 100 and m < 100:
        return "weakening"
    if r < 100 and m < 100:
        return "lagging"
    return "improving"


def _rule_based_fallback(
    ticker: str,
    quadrant: str,
    rs_ratio: float,
    rs_momentum: float,
    trail: list[dict[str, float]],
) -> RRGRotationSignal:
    """Derive signal from quadrant + simple trail direction when LLM fails."""
    # Trail direction: compare last 3 points momentum
    mom_trend = 0.0
    if len(trail) >= 3:
        mom_trend = trail[-1]["rs_momentum"] - trail[-3]["rs_momentum"]

    signal_map = {
        "leading":   "BUY"    if mom_trend >= 0 else "WATCH",
        "weakening": "WATCH"  if mom_trend > -1 else "REDUCE",
        "lagging":   "AVOID"  if mom_trend <= 0 else "WATCH",
        "improving": "WATCH"  if mom_trend >= 0 else "HOLD",
    }
    signal = signal_map.get(quadrant, "HOLD")

    pattern_map = {
        ("leading",   True):  "STABLE",
        ("leading",   False): "EXITING_LEADING",
        ("weakening", True):  "ROTATING",
        ("weakening", False): "WEAKENING_FAST",
        ("lagging",   True):  "RECOVERY",
        ("lagging",   False): "DEEP_LAGGING",
        ("improving", True):  "ENTERING_IMPROVING",
        ("improving", False): "ROTATING",
    }
    pattern = pattern_map.get((quadrant, mom_trend >= 0), "STABLE")

    return RRGRotationSignal(
        ticker=ticker,
        quadrant=quadrant,
        pattern=pattern,
        signal=signal,
        signal_reason=f"Rule-based: {quadrant}, momentum trend {'+' if mom_trend>=0 else ''}{mom_trend:.2f}",
        confidence=0.3,
    )


class RRGRotationAgent:
    """Analyse one ticker's RRG position + trail and emit a rotation signal.

    Args:
        ai_client: AIClient singleton from bootstrap.
    """

    def __init__(self, ai_client: AIClient) -> None:
        self._client = ai_client

    async def analyze(
        self,
        ticker: str,
        quadrant: str,
        rs_ratio: float,
        rs_momentum: float,
        trail: list[dict[str, float]],
        sector: str = "",
        company_name: str = "",
        lookback_weeks: int = 26,
    ) -> RRGRotationSignal:
        """Analyse ticker rotation signal.

        Falls back to rule-based result on any LLM / parse failure.
        Never raises — caller always gets a valid RRGRotationSignal.
        """
        user_prompt = _build_prompt(
            ticker=ticker,
            quadrant=quadrant,
            rs_ratio=rs_ratio,
            rs_momentum=rs_momentum,
            trail=trail,
            sector=sector,
            company_name=company_name,
            lookback_weeks=lookback_weeks,
        )

        try:
            result: RRGRotationSignal = await self._client.chat(  # type: ignore[attr-defined]
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                response_schema=RRGRotationSignal,
                temperature=0.15,
                max_tokens=_MAX_TOKENS,
            )
            result.ticker = ticker  # ensure ticker is always set from caller
            logger.info(
                "rrg_rotation_agent.complete ticker=%s pattern=%s signal=%s confidence=%.2f",
                ticker, result.pattern, result.signal, result.confidence,
            )
            return result

        except (AIError, Exception) as exc:
            logger.warning(
                "rrg_rotation_agent.fallback ticker=%s error=%s",
                ticker,
                str(exc),
            )
            return _rule_based_fallback(ticker, quadrant, rs_ratio, rs_momentum, trail)
