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

Changelog:
  - Added feedback_summary block: AI calibrates urgency/confidence against
    user's historical acted/ignored/disagreed patterns.
  - Deepened thesis schema in build_user_prompt: now includes assumptions,
    catalysts, invalidation_conditions for true thesis × watchdog cross-check.
  - Rule 7: upgraded thesis_review_triggers from list[str] to list[object]
    with thesis_id, ticker, reason, urgency fields — aligns with
    ThesisReviewTrigger schema and fixes prompt/schema mismatch.
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
7. `thesis_review_triggers`: list object — mỗi item có các trường:
   - `thesis_id`: string ID của thesis (lấy từ active_theses[].id). Bắt buộc khi biết.
   - `ticker`: ticker liên quan (VD: "VCB").
   - `reason`: lý do cụ thể tại sao cần review ngay — phải actionable, không chung chung.
   - `urgency`: "CRITICAL" hoặc "HIGH" — phản ánh mức độ khẩn cấp của conflict.
   Chỉ emit khi verdict BEARISH hoặc assumption bị invalidate. KHÔNG emit nếu thesis vẫn on-track.
8. `portfolio_risk_note`: 1-2 câu súc tích về concentration hoặc alignment risk từ portfolio_risk_context. None nếu không có risk đáng chú ý.
9. `confidence`: float 0.0-1.0 phản ánh chất lượng dữ liệu đầu vào tổng thể.
10. Tối đa 10 signals. Ưu tiên CRITICAL và HIGH trước.
11. Trả về raw JSON — không markdown, không giải thích ngoài JSON.
12. THESIS CROSS-CHECK (quan trọng): Với mỗi signal, hãy so sánh watchdog verdict với thesis của ticker đó:
    - Nếu watchdog=BEARISH nhưng thesis còn active → đây là conflict → urgency tăng lên ít nhất HIGH, thesis_aligned=false, thêm object vào thesis_review_triggers với reason rõ ràng.
    - Nếu watchdog=BULLISH và thesis nói "ợ catalyst X" mà X chưa xảy ra → ghi nhận trong trigger_reason là "thesis chưa kích hoạt dù giá di chuyển đúng hướng".
    - Nếu một assumption trong thesis bị thực tế phủ nhận (dựa trên watchdog/stress data) → bắt buộc thêm object vào thesis_review_triggers với thesis_id, ticker, reason cụ thể.
13. FEEDBACK CALIBRATION: Nếu có feedback_history, hãy điều chỉnh:
    - Nếu user có pattern "thường ignore signals về ngành X" → hạ urgency của signals ngành đó xuống 1 bậc (trừ CRITICAL).
    - Nếu user có pattern "ignore rồi hối hận" (acted_rate thấp + outcome negative) → KHÔNG hạ urgency, thay vào đó thêm ghi chú vào trigger_reason.
    - Nếu user thường act nhanh với CRITICAL → giữ nguyên, không cần nhắc thêm.
    - Chỉ calibrate khi có đủ dữ liệu (ít nhất 3 feedback events). Nếu không đủ → bỏ qua calibration.
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
    feedback_summary: str = "",
) -> str:
    """Build user-turn prompt for SignalEngineAgent.

    Args:
        watchdog_outputs:       list of WatchdogOutput.model_dump() per ticker.
        stress_outputs:         list of StressTestOutput.model_dump() per ticker.
        active_theses:          list of thesis summary dicts. Ideally includes:
                                {id, ticker, title, status, score, assumptions,
                                 catalysts, invalidation_conditions}
                                — richer fields enable deep thesis cross-check (rule 12).
                                Older callers passing {id, ticker, title, status, score}
                                still work; cross-check will be shallower.
        portfolio_risk_context: PortfolioRiskNote.model_dump() — rule-based concentration data.
        generated_at:           ISO timestamp string.
        feedback_summary:       Optional pre-rendered feedback calibration string from
                                FeedbackService. Format example:
                                "acted_rate=0.3 | ignored_sectors=[banking, realestate] |
                                 regret_ignores=2 | total_events=8"
                                Empty string = skip calibration (rule 13).
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

    # Thesis field hint — helps AI know how deep it can cross-check
    sample_keys = set(active_theses[0].keys()) if active_theses else set()
    has_deep_fields = {"assumptions", "catalysts", "invalidation_conditions"} & sample_keys
    thesis_depth_hint = (
        "(bao gồm assumptions, catalysts, invalidation_conditions — cross-check sâu khả dụng)"
        if has_deep_fields
        else "(chỉ có metadata cơ bản — cross-check ở mức shallow)"
    )

    feedback_block = (
        f"\n## Feedback History (User Calibration)\n{feedback_summary}\n"
        if feedback_summary.strip()
        else ""
    )

    return f"""Thời điểm phân tích: {generated_at}

## Watchdog Outputs ({len(watchdog_outputs)} tickers)
{watchdog_section}

## Stress Test Outputs ({len(stress_outputs)} tickers)
{stress_section}

## Active Theses ({len(active_theses)} theses) {thesis_depth_hint}
{thesis_section}

## Portfolio Risk Context
{portfolio_section}
{feedback_block}
Tổng hợp và rank signals theo urgency. Áp dụng thesis cross-check (rule 12){' và feedback calibration (rule 13)' if feedback_summary.strip() else ''}. Trả về JSON theo schema."""
