"""SectorRotationAgent — analyse macro + sector momentum and emit a ranked rotation signal.

Owner: ai segment.
Callers: briefing segment (BriefingAgent context injection),
         bot/scheduler (SectorRotationScheduler).

Boundary rules:
- Accepts raw market data dicts (no domain models imported).
- Returns SectorRotationOutput (Pydantic schema, ai segment owns it).
- Does NOT read from DB, does NOT call watchlist/thesis services.

Memory logging (Wave 6):
  - analyze() accepts optional session + user_id params.
  - On success, rotation result is logged as an episodic entry.
  - trigger=sector_rotation:<regime> groups by market regime for pattern detection.
  - Caller owns session — agent never opens DB directly (boundary preserved).
  - Backward-compat: session=None skips logging silently.
  - Memory log runs before re-raise on AIError so inflection points are not lost.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from src.ai.client import AIClient, AIError
from src.platform.logging import get_logger

logger = get_logger(__name__)

# Normalize verbose/non-canonical market_regime values from model → canonical enum.
# Canonical: RISK_ON | RISK_OFF | TRANSITIONING | UNCLEAR
_REGIME_MAP: dict[str, str] = {
    "LATE_CYCLE_TRANSITION": "TRANSITIONING",
    "EARLY_RECOVERY": "RISK_ON",
    "MODERATE_GROWTH": "RISK_ON",
    "MODERATE_GROWTH_EASING_INFLATION": "RISK_ON",
    "EXPANSION": "RISK_ON",
    "CONTRACTION": "RISK_OFF",
    "RECESSION": "RISK_OFF",
    "STAGFLATION": "RISK_OFF",
    "RECOVERY": "RISK_ON",
    "SLOWDOWN": "TRANSITIONING",
}

# sonar-pro response regularly reaches 1400+ tokens for 12-sector analysis.
# 4096 gives comfortable headroom without hitting rate-limit cost threshold.
_MAX_TOKENS = 900   # calibrated: SectorRotationOutput ~8 fields


class SectorSignal(BaseModel):
    sector: str
    signal: str = Field(..., description="ROTATE_IN | ROTATE_OUT | HOLD | WATCH")
    momentum_score: float = Field(default=0.5, ge=0.0, le=1.0)
    rationale: str
    key_tickers: list[str] = Field(default_factory=list)

    @field_validator("rationale", mode="before")
    @classmethod
    def coerce_rationale(cls, v: Any) -> str:
        if isinstance(v, list):
            return " | ".join(str(i) for i in v)
        return str(v) if v is not None else ""

    @field_validator("momentum_score", mode="before")
    @classmethod
    def coerce_momentum(cls, v: Any) -> float:
        """Normalize to 0-1 scale. Model may return 0-10 scale."""
        if v is None:
            return 0.5
        try:
            score = float(v)
            if score > 1.0:
                score = score / 10.0
            return max(0.0, min(1.0, score))
        except (TypeError, ValueError):
            return 0.5


class SectorRotationOutput(BaseModel):
    """Structured output from SectorRotationAgent.

    Validators absorb common model output variations so the schema
    stays stable regardless of how sonar-pro names its fields.
    """

    market_regime: str = Field(..., description="RISK_ON | RISK_OFF | TRANSITIONING | UNCLEAR")
    top_rotate_in: list[str] = Field(default_factory=list)
    top_rotate_out: list[str] = Field(default_factory=list)
    sector_signals: list[SectorSignal]
    macro_summary: str
    key_risk: str = Field(default="")
    confidence: str = Field(default="MEDIUM", description="HIGH | MEDIUM | LOW")
    next_watch: str = Field(default="")

    @field_validator("next_watch", "key_risk", "macro_summary", mode="before")
    @classmethod
    def coerce_str_fields(cls, v: Any) -> str:
        if isinstance(v, list):
            return " | ".join(str(i) for i in v)
        if isinstance(v, dict):
            return json.dumps(v, ensure_ascii=False)
        return str(v) if v is not None else ""

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_confidence(cls, v: Any) -> str:
        if isinstance(v, float):
            if v >= 0.7:
                return "HIGH"
            if v >= 0.5:
                return "MEDIUM"
            return "LOW"
        return str(v) if v else "MEDIUM"

    @model_validator(mode="before")
    @classmethod
    def normalize_model_output(cls, data: Any) -> Any:
        """Normalize divergent model field names into the canonical schema."""
        if not isinstance(data, dict):
            return data

        raw_regime = str(data.get("market_regime", "")).upper()
        data["market_regime"] = _REGIME_MAP.get(raw_regime, raw_regime) or "UNCLEAR"

        if not data.get("macro_summary"):
            ma = data.get("macro_assessment", {})
            regime = ma.get("regime", "") if isinstance(ma, dict) else ""
            desc = (
                data.get("regime_rationale")
                or data.get("regime_description", "")
            )
            data["macro_summary"] = f"{regime}: {desc}".strip(": ") or "N/A"

        if not data.get("key_risk"):
            risks = data.get("key_risks", [])
            if isinstance(risks, list) and risks:
                parts = []
                for r in risks[:3]:
                    parts.append(r.get("risk", str(r)) if isinstance(r, dict) else str(r))
                data["key_risk"] = " | ".join(parts)
            else:
                data["key_risk"] = str(risks) if risks else "N/A"

        if isinstance(data.get("next_watch"), list):
            data["next_watch"] = " | ".join(str(i) for i in data["next_watch"])

        if not data.get("next_watch"):
            signals = data.get("sector_signals", [])
            watch_sectors = [
                s["sector"] for s in signals
                if isinstance(s, dict) and s.get("signal") == "WATCH"
            ][:3]
            data["next_watch"] = " | ".join(watch_sectors) if watch_sectors else "N/A"

        raw_signals = data.get("sector_signals")
        if isinstance(raw_signals, dict):
            sector_analysis: list[dict] = data.get("sector_analysis", [])
            analysis_map: dict[str, dict] = {
                s["sector"]: s
                for s in sector_analysis
                if isinstance(s, dict) and "sector" in s
            }
            normalized: list[dict] = []
            for signal_type, sectors in raw_signals.items():
                if not isinstance(sectors, list):
                    continue
                for sector_name in sectors:
                    detail = analysis_map.get(str(sector_name), {})
                    normalized.append({
                        "sector": str(sector_name),
                        "signal": str(signal_type),
                        "momentum_score": detail.get("momentum_score", 0.5),
                        "rationale": detail.get("rationale", ""),
                        "key_tickers": detail.get("top_movers", detail.get("key_tickers", [])),
                    })
            data["sector_signals"] = normalized

        signals = data.get("sector_signals", [])
        if not data.get("top_rotate_in"):
            data["top_rotate_in"] = [
                s["sector"] for s in signals
                if isinstance(s, dict) and s.get("signal") == "ROTATE_IN"
            ][:3]
        if not data.get("top_rotate_out"):
            data["top_rotate_out"] = [
                s["sector"] for s in signals
                if isinstance(s, dict) and s.get("signal") == "ROTATE_OUT"
            ][:3]

        if not data.get("confidence"):
            scores = [
                s.get("confidence", 0.5)
                for s in signals
                if isinstance(s, dict) and isinstance(s.get("confidence"), (int, float))
            ]
            avg = sum(scores) / len(scores) if scores else 0.5
            data["confidence"] = "HIGH" if avg >= 0.7 else "MEDIUM" if avg >= 0.5 else "LOW"

        return data


_SYSTEM_PROMPT = """
Bạn là chuyên gia phân tích quay vòng ngành (sector rotation) thị trường chứng khoán Việt Nam (HOSE, HNX, UPCoM).

Nhiệm vụ: Phân tích dữ liệu macro và momentum ngành, đưa ra tín hiệu quay vòng có cấu trúc.

Quy trình phân tích:
1. Đánh giá tình trạng vĩ mô (lãi suất, tỷ giá, dòng vốn ngoại)
2. Xác định market regime hiện tại
3. Tính momentum ngành theo performance tương đối
4. Emit sector signals: ROTATE_IN / ROTATE_OUT / HOLD / WATCH
5. Chỉ ra key risk và next_watch

RANG BUỘC OUTPUT (bắt buộc):
- market_regime: CHỈ dùng một trong 4 giá trị: RISK_ON | RISK_OFF | TRANSITIONING | UNCLEAR
- sector_signals: PHẢI là array of objects [{"sector": ..., "signal": ..., "momentum_score": ..., "rationale": ..., "key_tickers": [...]}]
  KHÔNG được trả về dạng dict nhóm theo signal {"ROTATE_IN": [...]}
- macro_summary: string tiếng Việt, tóm tắt ngắn gọn tình hình vĩ mô và market regime
- momentum_score: float từ 0.0 đến 1.0 (không phải 0-10)
- key_risk: string mô tả rủi ro chính cần theo dõi
- next_watch: string mô tả sector/ticker cần quan sát tiếp theo

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
        session: Any = None,
        user_id: str | None = None,
    ) -> SectorRotationOutput:
        """Emit sector rotation signal.

        Args:
            sector_performance: list of {sector, return_1d, return_5d, return_1m, volume_vs_avg}
            macro_context:      free-text macro summary (VN-Index trend, interest rate, FX)
            foreign_flow:       free-text foreign buy/sell summary
            session:            Optional DB session from caller.
                                When provided, rotation result is logged as episodic memory.
            user_id:            Optional user ID for episodic memory logging.

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
            result = await self._client.chat(
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                response_schema=SectorRotationOutput,
                temperature=0.15,
                max_tokens=_MAX_TOKENS,
            )
        except AIError:
            logger.error("sector_rotation_agent.api_error")
            await _log_sector_rotation_interaction(session, user_id, None)
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
        await _log_sector_rotation_interaction(session, user_id, result)
        return result


# ---------------------------------------------------------------------------
# Memory interaction logger — module-level helper (Wave 6)
# ---------------------------------------------------------------------------


async def _log_sector_rotation_interaction(
    session: Any,
    user_id: str | None,
    result: SectorRotationOutput | None,
) -> None:
    """Fire-and-forget memory log for sector rotation analysis events.

    Caller owns the session — sector_rotation never opens DB directly
    (boundary: ai segment, no direct DB access).

    Sector rotation events are the macro-level context signal. Logging them
    lets the memory layer track:
      - Market regime transitions over time (RISK_ON → RISK_OFF → TRANSITIONING)
      - Recurring top_rotate_in sectors (structural bias in macro reads)
      - Correlation between regime and subsequent thesis performance

    trigger=sector_rotation:<regime> uses the regime as a semantic discriminator
    so downstream synthesis can group by macro phase rather than by date.

    When result is None (AIError path), logs with ai_verdict=ERROR so the
    memory layer can track AI availability as a meta-signal.

    Never raises. Silently skips when session is None or user_id unset.
    """
    if session is None or not user_id:
        return
    try:
        from src.ai.memory.memory_service import InteractionEntry, MemoryService

        if result is None:
            entry = InteractionEntry(
                user_id=user_id,
                agent_type="sector_rotation",
                trigger="sector_rotation:ERROR",
                tickers=[],
                ai_verdict="ERROR",
                ai_key_points="ai_unavailable",
            )
        else:
            regime = str(result.market_regime or "UNCLEAR")
            top_in = result.top_rotate_in[:3]
            top_out = result.top_rotate_out[:3]
            confidence = str(result.confidence or "MEDIUM")

            entry = InteractionEntry(
                user_id=user_id,
                agent_type="sector_rotation",
                trigger=f"sector_rotation:{regime}",
                tickers=top_in,
                ai_verdict=regime,
                ai_key_points=(
                    f"top_in={top_in} "
                    f"top_out={top_out} "
                    f"confidence={confidence}"
                ),
            )
        await MemoryService.log_interaction(session, entry)
    except Exception as exc:
        logger.warning("sector_rotation.memory_log_failed", error=str(exc))
