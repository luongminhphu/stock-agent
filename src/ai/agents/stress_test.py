"""StressTestAgent — simulate adverse scenarios against an investment thesis.

Owner: ai segment.
Callers:
    - thesis.review_service (optional enrichment step)
    - bot/commands/stress_test (direct Discord command)

Boundary:
    - Accepts raw thesis data + scenario descriptions (no domain model imports).
    - Returns StressTestOutput (Pydantic schema, owned by ai segment).
    - Does NOT write DB, does NOT call thesis repositories.

Design note:
    ScenarioResult.impact_on_thesis uses a controlled vocabulary
    (INVALIDATES | WEAKENS | NEUTRAL | STRENGTHENS) so callers can gate
    on severity without parsing free-text.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field
from pydantic import ValidationError

from src.ai.client import AIClient, AIError
from src.platform.logging import get_logger

logger = get_logger(__name__)


class ScenarioResult(BaseModel):
    scenario_name: str
    probability: str = Field(..., description="HIGH | MEDIUM | LOW")
    impact_on_thesis: str = Field(
        ..., description="INVALIDATES | WEAKENS | NEUTRAL | STRENGTHENS"
    )
    price_impact_estimate: str
    key_assumption_broken: str | None = None
    mitigation: str


class StressTestOutput(BaseModel):
    ticker: str
    overall_resilience: str = Field(..., description="STRONG | MODERATE | WEAK | FRAGILE")
    scenario_results: list[ScenarioResult]
    most_critical_risk: str
    recommended_hedge: str | None = None
    confidence: str = Field(..., description="HIGH | MEDIUM | LOW")


_SYSTEM_PROMPT = """
Bạn là chuyên gia phân tích rủi ro đầu tư chứng khoán Việt Nam.

Nhiệm vụ: Stress-test một investment thesis bằng cách mô phỏng các kịch bản bất lợi.

Quy trình:
1. Với mỗi kịch bản: đánh giá xác suất, mức độ ảnh hưởng lên thesis, ước tính tác động giá
2. Xác định assumption nào bị phá vỡ nếu kịch bản xảy ra
3. Đề xuất biện pháp hedge
4. Đánh giá overall resilience của thesis

Output: JSON theo schema StressTestOutput. Không có markdown, không có prose thêm.
"""


class StressTestAgent:
    """Simulates adverse scenarios against a thesis.

    Owner: ai segment.
    """

    def __init__(self, client: AIClient) -> None:
        self._client = client

    async def run(
        self,
        ticker: str,
        thesis_summary: str,
        assumptions: list[str],
        scenarios: list[str] | None = None,
    ) -> StressTestOutput:
        """Run stress test scenarios against a thesis.

        Args:
            ticker:          Stock ticker (e.g. "VCB").
            thesis_summary:  Plain-text thesis description.
            assumptions:     Active assumption strings.
            scenarios:       Optional custom scenarios. If None, AI generates defaults.

        Returns:
            StressTestOutput with per-scenario analysis.

        Raises:
            AIError: API call failed after retries.
            ValueError: Response cannot be parsed.
        """
        scenario_block = (
            "\n".join(f"- {s}" for s in scenarios)
            if scenarios
            else "(Tự generate 4-5 kịch bản bất lợi phù hợp nhất)"
        )
        user_prompt = (
            f"Ticker: {ticker}\n\n"
            f"## Thesis\n{thesis_summary}\n\n"
            f"## Assumptions\n" + "\n".join(f"- {a}" for a in assumptions) + "\n\n"
            f"## Scenarios to test\n{scenario_block}"
        )

        logger.info("stress_test_agent.start", ticker=ticker)

        try:
            response = await self._client.chat_completion(
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                response_format={"type": "json_object"},
            )
            raw = self._client.extract_text(response)
            result = StressTestOutput.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as exc:
            logger.error("stress_test_agent.parse_error", ticker=ticker, error=str(exc))
            raise ValueError(f"Failed to parse StressTestAgent response: {exc}") from exc
        except AIError:
            logger.error("stress_test_agent.api_error", ticker=ticker)
            raise

        logger.info(
            "stress_test_agent.complete",
            ticker=ticker,
            resilience=result.overall_resilience,
        )
        return result
