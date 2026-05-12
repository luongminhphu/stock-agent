"""Prompt pack for SignalEngineAgent — cross-watchlist × thesis × portfolio orchestrator.

Owner: ai segment.
Used by: SignalEngineAgent (ai/agents/signal_engine.py).

Responsibility boundary:
  This module owns ONLY the system prompt and user-prompt builder.
  Ranking logic, fallback, portfolio context extraction live in the agent.
  BriefingService injects SignalEngineOutput into BriefingAgent context.

Design intent:
  AI does NOT re-analyze each ticker from scratch.
  It receives pre-computed watchdog + stress_test outputs and synthesizes
  a cross-portfolio ranked signal list with thesis alignment context.
"""

from __future__ import annotations

from src.ai.prompts._spec import PromptSpec, schema_block
from src.ai.schemas import SignalEngineOutput

SYSTEM_PROMPT = """\
Bạn là Signal Engine — AI orchestrator phân tích danh mục đầu tư toàn diện cho thị trường chứng khoán Việt Nam.

Nhiệm vụ: Tổng hợp kết quả từ Watchdog và Stress Test đã chạy sẵn, cross-check với thesis đang active và context portfolio, rồi xuất ra danh sách tín hiệu đã được rank theo độ khẩn cấp.

Quy tắc bắt buộc:
1. KHÔNG tự phân tích lại từng ticker từ đầu — chỉ tổng hợp từ dữ liệu đã cung cấp.
2. Rank signal theo urgency: CRITICAL (cần action ngay hôm nay) → HIGH (1-2 ngày) → MEDIUM (trong tuần) → LOW (background watch).
3. CRITICAL chỉ dùng khi: stop-loss đang bị đe dọa, thesis bị invalidate, hoặc có risk alert đặc biệt nghiêm trọng.
4. `thesis_aligned`: true nếu signal này CONFIRM thesis hiện tại (VD: watchdog nói CRITICAL nhưng thesis là HOLD → false).
5. `causal_sources`: liệt kê rõ nguồn gốc signal. VD: ["watchdog:VCB", "stress_test:VCB"].
6. `action`: phải là hành động cụ thể, có thể đo được. KHÔNG viết chung chung.
7. `thesis_review_triggers`: list thesis_id cần schedule review ngay — chỉ khi verdict là BEARISH hoặc assumption bị invalidate.
8. `portfolio_risk_note`: 1-2 câu súc tích về concentration hoặc alignment risk từ portfolio_risk_context. None nếu không có risk đáng chú ý.
9. `confidence`: float 0.0-1.0 phản ánh chất lượng dữ liệu đầu vào tổng thể.
10. Tối đa 10 signals. Ưu tiên CRITICAL và HIGH trước.
11. Trả về raw JSON — không markdown, không giải thích ngoài JSON.
""" + schema_block(SignalEngineOutput)

SPEC = PromptSpec(
    agent_name="SignalEngineAgent",
    system_prompt=SYSTEM_PROMPT,
    output_schema=SignalEngineOutput,
)


def build_user_prompt(
    watchdog_outputs: list[dict],
    stress_outputs: list[dict],
    active_theses: list[dict],
    portfolio_risk_context: dict,
    generated_at: str,
) -> str:
    """Build user-turn prompt for SignalEngineAgent.

    Args:
        watchdog_outputs:      list of WatchdogOutput.model_dump() per ticker.
        stress_outputs:        list of StressTestOutput.model_dump() per ticker.
        active_theses:         list of thesis summary dicts {id, ticker, title, status, score}.
        portfolio_risk_context: PortfolioRiskNote.model_dump() — rule-based concentration data.
        generated_at:          ISO timestamp string.
    """
    import json

    watchdog_section = (
        json.dumps(watchdog_outputs, ensure_ascii=False, indent=2)
        if watchdog_outputs
        else "(Không có watchdog data)"
    )
    stress_section = (
        json.dumps(stress_outputs, ensure_ascii=False, indent=2)
        if stress_outputs
        else "(Không có stress test data)"
    )
    thesis_section = (
        json.dumps(active_theses, ensure_ascii=False, indent=2)
        if active_theses
        else "(Không có thesis active)"
    )
    portfolio_section = json.dumps(portfolio_risk_context, ensure_ascii=False, indent=2)

    return f"""Thời điểm phân tích: {generated_at}

## Watchdog Outputs ({len(watchdog_outputs)} tickers)
{watchdog_section}

## Stress Test Outputs ({len(stress_outputs)} tickers)
{stress_section}

## Active Theses ({len(active_theses)} theses)
{thesis_section}

## Portfolio Risk Context
{portfolio_section}

Tổng hợp và rank signals theo urgency. Trả về JSON theo schema."""
