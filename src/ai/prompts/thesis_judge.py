"""Prompt pack for ThesisJudgeAgent — automated thesis × signal cross-check.

Owner: ai segment.
Used by: ThesisJudgeAgent (ai/agents/thesis_judge.py).

Responsibility boundary:
  - Owns ONLY system prompt and user-prompt builder for fast verdict generation.
  - Deep analysis lives in ThesisReviewAgent (user-initiated, full context).
  - This agent runs automatically when SignalEngine emits thesis_review_triggers.

Design intent:
  Fast cross-check: given a signal context (watchdog + stress verdict) and
  thesis metadata, produce a structured ThesisJudgeOutput in one LLM call.
  Focus on delta detection (what changed) not full re-analysis.

Wave B changes:
  - last_review_summary is now extracted from signal_context and rendered as a
    dedicated "## Review Lần Trước" section so the LLM treats it as a primary
    anchor, not a buried JSON key.
  - Rule 9 added to SYSTEM_PROMPT: verdict flips require an explicit trigger
    citation in reasoning, preventing contradictory verdicts without reasoning.

Changelog:
  - Wired with_persona(): VETERAN_INVESTOR_PERSONA now prepended to SYSTEM_PROMPT
    so fast cross-check speaks as a seasoned investor, not a neutral judge.
"""

from __future__ import annotations

from typing import Any

from src.ai.prompts._spec import PromptSpec, schema_block, with_persona
from src.ai.schemas import ThesisJudgeOutput

_DOMAIN_RULES = """\
Bạn là Thesis Judge — AI kiểm định nhanh luận điểm đầu tư (thesis) theo tín hiệu thị trường chứng khoán Việt Nam.

Nhiệm vụ: Từ signal context (kết quả watchdog / stress test) và metadata thesis, đưa ra verdict cập nhật nhanh về sức khỏe luận điểm.

Phân biệt rõ:
- Đây KHÔNG phải full thesis review — chỉ là fast cross-check dựa trên dữ liệu đã có.
- Mục tiêu: Phát hiện delta (có gì thay đổi?) không phải phân tích lại từ đầu.
- Kết quả này feed vào briefing narrative và readmodel thesis health score.

Quy tắc bắt buộc:
1. VERDICT: Chỉ dùng 4 giá trị: ON_TRACK | WEAKENING | INVALIDATED | STRENGTHENING.
   - ON_TRACK: Signal không mâu thuẫn thesis, thesis vẫn hợp lệ.
   - WEAKENING: Một hoặc nhiều assumption đang bị đặt dấu hỏi, nhưng chưa invalidate.
   - INVALIDATED: Assumption core bị phủ nhận hoặc watchdog BEARISH + stop-loss bị đe dọa.
   - STRENGTHENING: Signal xác nhận catalyst hoặc assumption đúng hướng.
2. CONVICTION_DELTA: float -1.0 → +1.0.
   - Âm = conviction giảm (thesis yếu hơn so với lần review trước).
   - Dương = conviction tăng.
   - 0.0 = không có thay đổi đáng kể.
   - INVALIDATED: luôn ≤ -0.5. STRENGTHENING: luôn ≥ +0.3.
3. CHALLENGED_ASSUMPTIONS: chỉ liệt kê assumptions đang bị thực tế thách thức.
   - Mỗi item có: assumption_id (int | null), assumption_text, challenge_evidence (cụ thể từ signal data), severity (low | medium | high).
   - Bỏ trống nếu không có assumption nào bị thách thức.
4. NEW_RISKS: list string — rủi ro mới xuất hiện từ signal, chưa có trong thesis.
   - Bỏ trống nếu không có rủi ro mới.
5. ACTION: Hành động đề xuất:
   - hold     : Giữ nguyên, không cần làm gì.
   - reduce   : Cân nhắc giảm tỷ trọng, chưa cần exit hoàn toàn.
   - review   : Cần full thesis review ngay (chuyển sang ThesisReviewAgent).
   - exit_signal: Thesis invalidated — nên cân nhắc exit position.
   - Quy tắc: INVALIDATED → exit_signal hoặc review. WEAKENING → reduce hoặc review.
6. REASONING: Tóm tắt ngắn (tối đa 150 từ) giải thích delta. Tập trung vào cái gì đã thay đổi, không viết lại toàn bộ thesis.
7. CONFIDENCE: float 0.0-1.0 phản ánh chất lượng dữ liệu đầu vào. Thiếu dữ liệu → 0.5 hoặc thấp hơn.
8. Trả về raw JSON — không markdown, không giải thích ngoài JSON.
9. NHẤT QUÁN VỚI REVIEW TRƯỚC: Nếu có "## Review Lần Trước" và verdict của bạn KHÁC với verdict đó,
   bắt buộc nêu rõ trigger thay đổi cụ thể trong reasoning (ví dụ: "dòng tiền ngoại bán ròng 3 phiên liên tiếp").
   Không được flip verdict chỉ vì cảm nhận chung — phải có bằng chứng từ signal_context.
   Nếu signal không thay đổi đáng kể so với review trước, giữ nguyên hướng verdict.
""" + schema_block(ThesisJudgeOutput)

SYSTEM_PROMPT = with_persona(_DOMAIN_RULES)

SPEC = PromptSpec(
    agent_name="ThesisJudgeAgent",
    system_prompt=SYSTEM_PROMPT,
    output_schema=ThesisJudgeOutput,
)


def build_user_prompt(
    thesis_id: str | int,
    ticker: str,
    thesis_title: str,
    thesis_summary: str,
    assumptions: list[dict[str, Any]],
    catalysts: list[dict[str, Any]],
    invalidation_conditions: list[str],
    signal_context: dict[str, Any],
    conviction_history: list[dict[str, Any]] | None = None,
    days_since_written: int | None = None,
) -> str:
    """Build user-turn prompt for ThesisJudgeAgent.

    Args:
        thesis_id:               Thesis ID for traceability.
        ticker:                  Mã cổ phiếu.
        thesis_title:            Tiêu đề thesis.
        thesis_summary:          Tóm tắt luận điểm.
        assumptions:             List [{"id": int|None, "description": str, "status": str}].
                                 Only active/uncertain assumptions needed.
        catalysts:               List [{"id": int|None, "description": str, "status": str}].
                                 Pending catalysts only.
        invalidation_conditions: List[str] — explicit conditions that would kill the thesis.
        signal_context:          Dict with keys from SignalEngine / Watchdog output:
                                 {"watchdog_verdict": str, "health_score": int|None,
                                  "stress_verdict": str|None, "risk_flags": list[str],
                                  "trigger_reason": str, "urgency": str,
                                  "signal_summary": str|None,
                                  "last_review_summary": str|None}
                                 last_review_summary is extracted and rendered as a
                                 dedicated section — not included in the JSON dump.
        conviction_history:      Optional list of past judge verdicts for trend context.
                                 [{"date": str, "verdict": str, "conviction_delta": float}]
                                 None = no history (first run).
        days_since_written:      Days elapsed since thesis was created. None if unknown.
    """
    import json

    # Extract last_review_summary before dumping signal_context as JSON.
    # Rendering it as a named section ensures the LLM treats it as a primary
    # anchor rather than a buried key in a raw JSON blob.
    last_review_summary: str | None = signal_context.get("last_review_summary")
    signal_context_clean = {
        k: v for k, v in signal_context.items() if k != "last_review_summary"
    }

    assumptions_str = (
        "\n".join(
            f"  - [ID {a.get('id', 'N/A')}] {a.get('description', '')} (status: {a.get('status', 'active')})"
            for a in assumptions
        )
        if assumptions
        else "  (không có assumption active)"
    )

    catalysts_str = (
        "\n".join(
            f"  - [ID {c.get('id', 'N/A')}] {c.get('description', '')} (status: {c.get('status', 'pending')})"
            for c in catalysts
        )
        if catalysts
        else "  (không có catalyst pending)"
    )

    invalidation_str = (
        "\n".join(f"  - {cond}" for cond in invalidation_conditions)
        if invalidation_conditions
        else "  (không có điều kiện invalidation rõ ràng)"
    )

    signal_str = json.dumps(signal_context_clean, ensure_ascii=False, indent=2)

    age_line = (
        f"Thesis viết cách đây: {days_since_written} ngày"
        if days_since_written is not None
        else "Thời gian viết thesis: không rõ"
    )

    history_block = ""
    if conviction_history:
        history_lines = [
            f"  - {h.get('date', '?')}: {h.get('verdict', '?')} "
            f"(delta={h.get('conviction_delta', 0):+.2f})"
            for h in conviction_history[-5:]  # last 5 only
        ]
        history_block = (
            "\n## Lịch sử Judge Verdicts (gần nhất trước)\n"
            + "\n".join(history_lines)
            + "\n"
        )

    # Render last_review_summary as a dedicated section when present.
    # The ⚠️ marker reinforces Rule 9 from SYSTEM_PROMPT.
    last_review_block = ""
    if last_review_summary:
        last_review_block = (
            "\n## Review Lần Trước (ThesisReviewAgent)\n"
            f"{last_review_summary}\n"
            "⚠️ Nếu verdict của bạn khác với review trên, phải nêu trigger cụ thể trong reasoning.\n"
        )

    return f"""Thesis ID: {thesis_id} | Ticker: {ticker}
{age_line}

## Thesis
**Tiêu đề:** {thesis_title}
**Tóm tắt:** {thesis_summary or '(không có)'}

## Assumptions (đang active)
{assumptions_str}

## Catalysts (pending)
{catalysts_str}

## Điều kiện Invalidation
{invalidation_str}

## Signal Context (trigger đã xảy ra)
{signal_str}
{history_block}{last_review_block}
Dựa trên signal context trên, hãy:
1. Xác định assumption nào đang bị thách thức (nếu có).
2. Kiểm tra xem có điều kiện invalidation nào đang xảy ra không.
3. Đưa ra verdict + conviction_delta + action.
4. Giải thích delta ngắn gọn (tối đa 150 từ, tập trung vào cái gì đã thay đổi).
5. Nếu verdict thay đổi so với "## Review Lần Trước", nêu rõ trigger thay đổi cụ thể.
Trả về raw JSON theo schema."""
