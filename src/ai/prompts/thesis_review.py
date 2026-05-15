"""
Thesis Review Prompt Pack — ai segment.

Owner: ai segment.
Boundary:
  - Defines SYSTEM_PROMPT and build_user_prompt() for ThesisReviewAgent.
  - Pure string/schema — no DB, no external I/O.
  - ThesisReviewOutput schema is the structured output contract used by
    AIClient.chat() and returned to ReviewService.

This module co-locates the prompt engineering with the schema so every change
to the output structure is reflected in the prompt in the same diff.
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Structured output schema (Pydantic-compatible TypedDicts kept as dataclasses
# for zero dependency — consumers cast to Pydantic if needed).
# The canonical Pydantic version lives in src/ai/schemas.py;
# this module re-exports the prompt constants only.
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
Bạn là chuyên gia phân tích đầu tư chứng khoán Việt Nam (HOSE / HNX / UPCoM).
Nhiệm vụ của bạn là review một investment thesis và đánh giá mức độ còn hiệu lực của nó.

Bối cảnh thị trường:
- Biên độ dao động: HOSE ±7%, HNX ±10%, UPCoM ±15% mỗi phiên.
- Múi giờ: ICT (UTC+7). Phiên giao dịch: 09:00–14:30 ICT.
- Đơn vị giá: VNĐ. Khối lượng tính bằng cổ phiếu.
- Thị trường mới nổi — thanh khoản, tâm lý đám đông và chính sách vĩ mô VN
  ảnh hưởng mạnh hơn các thị trường phát triển.

Nguyên tắc review:
1. Ưu tiên BẢO TOÀN VỐN trước lợi nhuận.
2. Chỉ chuyển sang BEARISH/WATCHLIST khi có bằng chứng rõ ràng, không phải chỉ vì giá giảm ngắn hạn.
3. Phân biệt "thesis sai" vs "thesis đúng nhưng timing sai".
4. Xét cả yếu tố định tính (quản trị, ngành) lẫn định lượng (giá, volume, tài chính).
5. Mỗi key_risk phải là một câu mô tả rủi ro cụ thể (plain string).
6. summary là 2-3 câu tóm tắt tình trạng thesis.

Nguyên tắc nhất quán (bắt buộc tuân thủ):
7. Nếu có dữ liệu "Review lần trước" trong prompt, phải đọc kỹ verdict và lý do
   trước khi đưa ra kết luận — không bắt đầu phân tích như lần đầu tiên.
8. Chỉ flip verdict (ví dụ BULLISH→BEARISH, NEUTRAL→BEARISH) khi có bằng chứng
   MỚI và RÕ RÀNG trong lần review này: giá phá support, assumption bị invalidate,
   catalyst thất bại, hoặc thay đổi macro nghiêm trọng. Không flip chỉ vì cảm nhận
   chung hoặc thiếu thông tin mới.
9. Nếu verdict THAY ĐỔI so với lần trước, BẮT BUỘC mở đầu trường "summary" bằng
   lý do thay đổi — ví dụ: "Chuyển từ NEUTRAL sang BEARISH vì giá phá vỡ support
   45,000 trong phiên 15/05...". Không được thay đổi verdict mà không giải thích.
10. Nếu verdict KHÔNG THAY ĐỔI, summary nên xác nhận ngắn gọn tại sao nhận định
    vẫn còn hiệu lực — tránh lặp lại y chang lần trước mà không có nội dung mới.

Verdicts (chỉ dùng đúng 4 giá trị này, viết HOA):
- BULLISH   : Thesis còn nguyên vẹn, momentum tốt.
- NEUTRAL   : Thesis chưa invalidate nhưng cần theo dõi thêm.
- BEARISH   : Thesis đang bị đe dọa nghiêm trọng, cân nhắc reduce/exit.
- WATCHLIST : Chưa đủ dữ liệu để kết luận, giữ trên watchlist.

Output phải là JSON hợp lệ theo cấu trúc đã mô tả. Không thêm nội dung ngoài JSON.
"""

# ---------------------------------------------------------------------------
# Explicit JSON schema shown to the model — keeps prompt in sync with
# ThesisReviewOutput in src/ai/schemas.py.
#
# IMPORTANT: key names here MUST match ThesisReviewOutput field names exactly.
# If you change ThesisReviewOutput, update this schema in the same diff.
# ---------------------------------------------------------------------------
_OUTPUT_SCHEMA = """\
### Output Schema (JSON chính xác — không thêm/bớt field)
```json
{
  "overall_verdict": "BULLISH",
  "conviction_score": 0.75,
  "confidence": 0.75,
  "key_risks": [
    "Giá đang test support 120,000 — nếu mất sẽ trigger stop-loss"
  ],
  "action_recommendation": "HOLD",
  "summary": "Thesis còn nguyên vẹn vì... (2-3 câu ngắn gọn)",
  "assumption_recommendations": [
    {
      "assumption_id": 42,
      "status": "VALID",
      "evidence": "Bằng chứng hỗ trợ đánh giá này",
      "confidence": 0.8
    }
  ],
  "catalyst_recommendations": [
    {
      "catalyst_id": 46,
      "status": "ACTIVE",
      "notes": "Ghi chú thêm về trạng thái catalyst",
      "confidence": 0.7
    }
  ]
}
```
Lưu ý quan trọng:
- `overall_verdict`: BULLISH | NEUTRAL | BEARISH | WATCHLIST (viết HOA).
- `conviction_score`: float 0.0–1.0 — mức độ tin tưởng vào thesis.
- `confidence`: float 0.0–1.0 — độ chắc chắn của AI với review này.
- `action_recommendation`: HOLD | ADD | REDUCE | EXIT | WAIT_FOR_CATALYST.
- `key_risks`: list plain string, mỗi string mô tả 1 rủi ro cụ thể.
- `assumption_recommendations[].assumption_id`: ID số nguyên của assumption (dùng ID từ danh sách trên).
- `assumption_recommendations[].status`: VALID | WEAKENED | INVALIDATED | NEEDS_MONITORING.
- `assumption_recommendations[].evidence`: string mô tả bằng chứng hỗ trợ.
- `catalyst_recommendations[].catalyst_id`: ID số nguyên của catalyst (dùng ID từ danh sách trên).
- `catalyst_recommendations[].status`: ACTIVE | TRIGGERED | DELAYED | CANCELLED.
- Chỉ list assumption/catalyst cần thay đổi trạng thái; bỏ qua nếu không có thay đổi.
"""


def build_user_prompt(
    ticker: str,
    thesis_title: str,
    thesis_summary: str,
    assumptions_with_ids: list[dict[str, Any]],
    catalysts_with_ids: list[dict[str, Any]],
    triggered_catalysts_with_ids: list[dict[str, Any]],
    current_price: float | None = None,
    entry_price: float | None = None,
    target_price: float | None = None,
) -> str:
    """
    Build the user-turn prompt for a thesis review.

    Args:
        ticker:                       Mã cổ phiếu (VD: "VCB", "VNM").
        thesis_title:                 Tiêu đề thesis.
        thesis_summary:               Tóm tắt luận điểm đầu tư.
        assumptions_with_ids:         List [{"id": int, "description": str}] — assumptions chưa INVALID.
        catalysts_with_ids:           List [{"id": int, "description": str}] — catalysts PENDING.
        triggered_catalysts_with_ids: List [{"id": int, "description": str}] — catalysts đã TRIGGERED.
        current_price:                Giá hiện tại (VNĐ). None nếu không có.
        entry_price:                  Giá vào lệnh (VNĐ). None nếu chưa có.
        target_price:                 Giá mục tiêu (VNĐ). None nếu chưa set.

    Returns:
        Formatted user prompt string (without memory or previous_review blocks).
        Use build_review_prompt() for the full agent-facing prompt.
    """
    lines: list[str] = [
        f"## Thesis Review Request: {ticker}",
        "",
        f"**Ticker:** {ticker}",
        f"**Title:** {thesis_title}",
        "",
        "### Thesis Summary",
        thesis_summary or "(không có tóm tắt)",
        "",
    ]

    # Price context
    price_parts: list[str] = []
    if current_price is not None:
        price_parts.append(f"Giá hiện tại: {current_price:,.0f} VNĐ")
    if entry_price is not None:
        price_parts.append(f"Giá vào lệnh: {entry_price:,.0f} VNĐ")
        if current_price is not None:
            pnl_pct = (current_price - entry_price) / entry_price * 100
            sign = "+" if pnl_pct >= 0 else ""
            price_parts.append(f"P&L chưa thực hiện: {sign}{pnl_pct:.1f}%")
    if target_price is not None:
        price_parts.append(f"Giá mục tiêu: {target_price:,.0f} VNĐ")
        if current_price is not None:
            upside = (target_price - current_price) / current_price * 100
            price_parts.append(f"Upside còn lại: {upside:.1f}%")

    if price_parts:
        lines += ["### Thông tin giá", *price_parts, ""]

    # Assumptions
    if assumptions_with_ids:
        lines.append("### Assumptions (đang theo dõi)")
        for a in assumptions_with_ids:
            lines.append(f"- [ID {a['id']}] {a['description']}")
        lines.append("")
    else:
        lines += ["### Assumptions", "(không có assumption nào đang active)", ""]

    # Pending catalysts
    if catalysts_with_ids:
        lines.append("### Catalysts (chờ xảy ra)")
        for c in catalysts_with_ids:
            lines.append(f"- [ID {c['id']}] {c['description']}")
        lines.append("")

    # Triggered catalysts
    if triggered_catalysts_with_ids:
        lines.append("### Catalysts đã xảy ra")
        for c in triggered_catalysts_with_ids:
            lines.append(f"- [ID {c['id']}] {c['description']}")
        lines.append("")

    return "\n".join(lines)


def build_review_prompt(  # noqa: PLR0913
    ticker: str,
    thesis_title: str,
    thesis_summary: str,
    assumptions_with_ids: list[dict[str, Any]],
    catalysts_with_ids: list[dict[str, Any]],
    triggered_catalysts_with_ids: list[dict[str, Any]],
    current_price: float | None = None,
    entry_price: float | None = None,
    target_price: float | None = None,
    memory_context: str = "",
    previous_review: dict | None = None,
) -> str:
    """Build the full agent-facing prompt with memory and previous verdict context.

    Used by ThesisReviewAgent. Assembles sections in this order:
      1. Core thesis block (ticker, title, summary, price, assumptions, catalysts)
      2. Previous review anchor (P2) — verdict from last review for consistency
      3. Investor memory block (P1) — episodic + semantic context
      4. Output schema + instructions (always last)

    Args:
        ...                (same as build_user_prompt)
        memory_context:    Rendered MemoryContext string from MemoryService.
                           Empty string → section omitted.
        previous_review:   Dict with keys: reviewed_at, verdict, confidence,
                           summary, key_risks. None → section omitted.
                           Extracted by ThesisReviewAgent from memory episodes.

    Order rationale:
        Memory and previous_review are placed BEFORE the output schema so the
        LLM processes historical context before encountering formatting rules.
        Appending memory AFTER "Trả về JSON..." caused it to be treated as
        low-priority afterthought context.
    """
    # Section 1: core thesis content
    core = build_user_prompt(
        ticker=ticker,
        thesis_title=thesis_title,
        thesis_summary=thesis_summary,
        assumptions_with_ids=assumptions_with_ids,
        catalysts_with_ids=catalysts_with_ids,
        triggered_catalysts_with_ids=triggered_catalysts_with_ids,
        current_price=current_price,
        entry_price=entry_price,
        target_price=target_price,
    )
    sections: list[str] = [core]

    # Section 2: previous verdict anchor (P2)
    # Informs LLM what it said last time so it reasons about continuity.
    if previous_review:
        prev_date = previous_review.get("reviewed_at", "không rõ ngày")
        prev_verdict = previous_review.get("verdict", "N/A")
        prev_conf = previous_review.get("confidence")
        prev_summary = previous_review.get("summary", "")
        prev_risks: list[str] = previous_review.get("key_risks") or []
        conf_str = f"{prev_conf:.0%}" if prev_conf is not None else "N/A"

        prev_lines: list[str] = [
            f"### Review lần trước ({prev_date})",
            f"- Verdict: **{prev_verdict}**  |  Confidence: {conf_str}",
        ]
        if prev_summary:
            prev_lines.append(f"- Nhận định: {prev_summary}")
        if prev_risks:
            risks_str = "; ".join(str(r) for r in prev_risks[:3])
            prev_lines.append(f"- Key risks đã ghi nhận: {risks_str}")
        prev_lines.append(
            "⚠️ Nếu verdict thay đổi, phải giải thích trigger thay đổi "
            "ở đầu trường summary."
        )
        sections.append("\n".join(prev_lines))

    # Section 3: investor memory block (P1)
    # Placed here — after previous_review but still before output schema —
    # so LLM reads behavioural context before encountering JSON formatting rules.
    if memory_context:
        sections.append(
            f"### Bối cảnh nhà đầu tư (memory)\n{memory_context}"
        )

    # Section 4: output schema + task instructions (always last)
    sections.append(_OUTPUT_SCHEMA)
    sections += [
        "### Yêu cầu",
        "Dựa trên thông tin trên, hãy:",
        "1. Đánh giá từng assumption còn giá trị hay không — điền assumption_id (số nguyên từ danh sách) và status vào assumption_recommendations.",
        "2. Cập nhật trạng thái catalyst nếu cần — điền catalyst_id (số nguyên từ danh sách) và status vào catalyst_recommendations.",
        "3. Đưa ra overall_verdict tổng thể (BULLISH / NEUTRAL / BEARISH / WATCHLIST).",
        "4. Liệt kê key_risks dưới dạng plain string (không phải dict).",
        "5. Chọn action_recommendation phù hợp (HOLD / ADD / REDUCE / EXIT / WAIT_FOR_CATALYST).",
        "6. Viết summary 2-3 câu giải thích tình trạng thesis.",
        "",
        "Trả về JSON theo đúng Output Schema ở trên. Không giải thích ngoài JSON.",
    ]

    return "\n\n".join(sections)
