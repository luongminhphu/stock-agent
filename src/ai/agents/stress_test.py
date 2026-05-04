"""Stress-Test Agent — adversarial thesis validator.

Owner: ai segment.
Caller: thesis.stress_test_service — passes all thesis context,
receives typed StressTestOutput back.

Prompt strategy: adversarial framing.
The agent is instructed to act as a bearish analyst whose job is to
find every reason the thesis could be wrong — not to confirm it.
This produces stronger counter-arguments than a neutral review.
"""

from __future__ import annotations

import json
import re

from pydantic import ValidationError

from src.ai.client import PerplexityClient, PerplexityError
from src.ai.schemas import StressTestOutput
from src.platform.logging import get_logger

logger = get_logger(__name__)

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)

_SYSTEM_PROMPT = """Bạn là một analyst bi quan, chuyên gia phân tích rủi ro đầu tư chứng khoán Việt Nam.
Nhiệm vụ DUY NHẤT của bạn: tìm mọi lý do một thesis đầu tư có thể SAI trong 3-6 tháng tới.

Quy tắc:
1. Không xác nhận thesis — chỉ tìm điểm yếu và rủi ro.
2. Với mỗi assumption, hãy đặt câu hỏi: "Điều gì sẽ phủ nhận giả định này?"
3. Sử dụng context macro/giá hiện tại để tìm bằng chứng cụ thể, có thể đo được.
4. threat_level: BROKEN nếu đã có bằng chứng rõ ràng, WEAKENED nếu đang bị đe dọa, INTACT nếu chưa.
5. invalidation_probability: tính từ tỷ lệ BROKEN + 0.5*WEAKENED trên tổng assumptions.
6. Trả lời ĐÚNG format JSON theo schema được yêu cầu — không thêm text ngoài JSON."""


def _build_stress_prompt(
    ticker: str,
    thesis_title: str,
    thesis_summary: str,
    assumptions: list[dict],
    catalysts: list[str],
    current_price: float | None,
    entry_price: float | None,
    target_price: float | None,
    stop_loss: float | None,
    macro_context: str,
) -> str:
    lines = [
        f"## Stress-Test Thesis: {ticker}",
        f"**Tiêu đề**: {thesis_title}",
        f"**Tóm tắt**: {thesis_summary}",
        "",
    ]

    price_parts = []
    if current_price:
        price_parts.append(f"Giá hiện tại: {current_price:,.0f}")
    if entry_price:
        price_parts.append(f"Giá vào: {entry_price:,.0f}")
    if target_price:
        price_parts.append(f"Target: {target_price:,.0f}")
    if stop_loss:
        price_parts.append(f"Stop-loss: {stop_loss:,.0f}")
    if price_parts:
        lines.append("**Giá**: " + " | ".join(price_parts))
        lines.append("")

    if assumptions:
        lines.append("**Các assumptions cần stress-test:**")
        for a in assumptions:
            aid = a.get("id", 0)
            desc = a.get("description", "")
            status = a.get("status", "valid")
            lines.append(f"- [ID:{aid}] ({status}) {desc}")
        lines.append("")

    if catalysts:
        lines.append("**Catalysts đang chờ:**")
        for c in catalysts:
            lines.append(f"- {c}")
        lines.append("")

    if macro_context:
        lines.append("**Context macro / giá hiện tại:**")
        lines.append(macro_context)
        lines.append("")

    lines.append("")
    lines.append("Hãy stress-test toàn bộ assumptions trên. Trả về JSON theo schema StressTestOutput:")
    lines.append(json.dumps({
        "ticker": ticker,
        "thesis_title": thesis_title,
        "verdict": "BEARISH | NEUTRAL | BULLISH",
        "invalidation_probability": 0.0,
        "confidence": 0.0,
        "stress_scenario": "Scenario macro AI dùng để test",
        "threatened_assumptions": [
            {
                "assumption_id": 0,
                "description": "...",
                "threat_level": "BROKEN | WEAKENED | INTACT",
                "evidence": "Bằng chứng cụ thể",
                "counter_argument": "Counter-argument mạnh nhất",
            }
        ],
        "surviving_assumptions": ["assumption còn intact..."],
        "macro_risks": ["rủi ro vĩ mô..."],
        "suggested_triggers_to_watch": ["trigger cần theo dõi..."],
        "reasoning": "Lý giải tổng thể",
    }, ensure_ascii=False, indent=2))

    return "\n".join(lines)


def _extract_json(text: str) -> str:
    text = text.strip()
    match = _JSON_FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    return text


class StressTestAgent:
    """Adversarial AI agent for stress-testing investment thesis assumptions.

    Owner: ai segment.
    Caller: thesis.StressTestService.

    This agent does NOT know thesis business rules or DB schema.
    It receives pre-built context strings and returns StressTestOutput.
    """

    def __init__(self, client: PerplexityClient) -> None:
        self._client = client

    async def stress_test(
        self,
        ticker: str,
        thesis_title: str,
        thesis_summary: str,
        assumptions: list[dict],
        catalysts: list[str] | None = None,
        current_price: float | None = None,
        entry_price: float | None = None,
        target_price: float | None = None,
        stop_loss: float | None = None,
        macro_context: str = "",
    ) -> StressTestOutput:
        """Run adversarial stress-test and return structured output.

        Args:
            assumptions: list of dicts with keys: id, description, status.
            catalysts:   list of pending catalyst descriptions.
            macro_context: pre-built string with current price/macro data.

        Raises:
            PerplexityError: API call failed after retries.
            ValueError: Response cannot be parsed into StressTestOutput.
        """
        prompt = _build_stress_prompt(
            ticker=ticker,
            thesis_title=thesis_title,
            thesis_summary=thesis_summary,
            assumptions=assumptions,
            catalysts=catalysts or [],
            current_price=current_price,
            entry_price=entry_price,
            target_price=target_price,
            stop_loss=stop_loss,
            macro_context=macro_context,
        )

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        logger.info("stress_test_agent.start", ticker=ticker, thesis_title=thesis_title)

        raw_text = ""
        try:
            response = await self._client.chat_completion(
                messages=messages,
                temperature=0.2,  # Slightly higher than review for diverse counter-args
            )
            raw_text = self._client.extract_text(response)
            clean_text = _extract_json(raw_text)
            logger.debug(
                "stress_test_agent.raw_response",
                ticker=ticker,
                raw_length=len(raw_text),
            )
            data = json.loads(clean_text)
            result = StressTestOutput.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as exc:
            logger.error(
                "stress_test_agent.parse_error",
                ticker=ticker,
                error=str(exc),
                raw_text=raw_text[:500],
            )
            raise ValueError(f"Failed to parse stress-test response for {ticker}: {exc}") from exc
        except PerplexityError:
            logger.error("stress_test_agent.api_error", ticker=ticker)
            raise

        logger.info(
            "stress_test_agent.complete",
            ticker=ticker,
            verdict=result.verdict,
            invalidation_prob=result.invalidation_probability,
            threatened_count=len(result.threatened_assumptions),
        )
        return result
