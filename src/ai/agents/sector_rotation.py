"""SectorRotationAgent — analyse macro + sector momentum and emit a ranked rotation signal.

Owner: ai segment.
Callers: briefing segment (BriefingAgent context injection),
         bot/scheduler (SectorRotationScheduler).

Boundary rules:
- Accepts raw market data dicts (no domain models imported).
- Returns SectorRotationOutput (Pydantic schema, ai segment owns it).
- Does NOT read from DB, does NOT call watchlist/thesis services.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from src.ai.client import AIClient, AIError
from src.platform.logging import get_logger

logger = get_logger(__name__)


class SectorSignal(BaseModel):
    sector: str
    signal: str = Field(..., description="ROTATE_IN | ROTATE_OUT | HOLD | WATCH")
    momentum_score: float = Field(..., ge=0.0, le=1.0)
    rationale: str
    key_tickers: list[str] = Field(default_factory=list)


class SectorRotationOutput(BaseModel):
    """Structured output from SectorRotationAgent."""

    market_regime: str = Field(
        ..., description="RISK_ON | RISK_OFF | TRANSITIONING | UNCLEAR"
    )
    top_rotate_in: list[str] = Field(
        ..., description="Top 2-3 sectors to rotate into"
    )
    top_rotate_out: list[str] = Field(
        ..., description="Top 2-3 sectors to rotate out of"
    )
    sector_signals: list[SectorSignal]
    macro_summary: str
    key_risk: str
    confidence: str = Field(..., description="HIGH | MEDIUM | LOW")
    next_watch: str


_SYSTEM_PROMPT = """
Bạn là chuyên gia phân tích quay vòng ngành (sector rotation) thị trường chứng khoán Việt Nam (HOSE, HNX, UPCoM).

Nhiệm vụ: Phân tích dữ liệu macro và momentum ngành, đưa ra tín hiệu quay vòng có cấu trúc.

Quy trình phân tích:
1. Đánh giá tình trạng vĩ mô (lãi suất, tỷ giá, dòng vốn ngoại)
2. Xác định market regime hiện tại
3. Tính momentum ngành theo performance tương đối
4. Emit sector signals: ROTATE_IN / ROTATE_OUT / HOLD / WATCH
5. Chỉ ra key risk và next_watch

Output: JSON theo schema SectorRotationOutput. Không có markdown, không có prose thêm.
"""


class SectorRotationAgent:
    """Analyses sector rotation from raw market data.

    Design note — data flow:
        caller builds raw dicts → SectorRotationAgent.analyze() → SectorRotationOutput

    This agent deliberately accepts primitive dicts, not domain models, so the
    ai segment stays decoupled from market/thesis domain types.
    """

    def __init__(
        self,
        ai_client: AIClient,
    ) -> None:
        self._client = ai_client

    async def analyze(
        self,
        sector_performance: list[dict],
        macro_context: str,
        foreign_flow: str = "",
    ) -> SectorRotationOutput:
        """Emit sector rotation signal.

        Args:
            sector_performance: list of {sector, return_1d, return_5d, return_1m, volume_vs_avg}
            macro_context:      free-text macro summary (VN-Index trend, interest rate, FX)
            foreign_flow:       free-text foreign buy/sell summary

        Returns:
            SectorRotationOutput with ranked signals.

        Raises:
            AIError: If API call fails after retries.
            ValueError: If response cannot be parsed.
        """
        user_prompt = (
            f"## Macro Context\n{macro_context}\n\n"
            f"## Foreign Flow\n{foreign_flow or 'No data'}\n\n"
            f"## Sector Performance\n{json.dumps(sector_performance, ensure_ascii=False, indent=2)}"
        )

        logger.info("sector_rotation_agent.start", sector_count=len(sector_performance))

        try:
            # Use client.chat() — sonar-pro does NOT support response_format=json_object.
            # client.chat() enforces JSON via system prompt and parses into Pydantic schema.
            result = await self._client.chat(
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                response_schema=SectorRotationOutput,
                temperature=0.15,
            )
        except AIError:
            logger.error("sector_rotation_agent.api_error")
            raise
        except Exception as exc:
            logger.error("sector_rotation_agent.parse_error", error=str(exc))
            raise ValueError(f"Failed to parse SectorRotationAgent response: {exc}") from exc

        logger.info(
            "sector_rotation_agent.complete",
            regime=result.market_regime,
            top_in=result.top_rotate_in,
            confidence=result.confidence,
        )
        return result
