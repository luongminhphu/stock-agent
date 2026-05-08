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
5. Mỗi risk_signal phải là một câu mô tả rủi ro cụ thể (plain string).
6. next_watch_items là các điều kiện cụ thể cần theo dõi trong 2–4 tuần tới (plain string).

Verdicts (chỉ dùng đúng 4 giá trị này):
- BULLISH   : Thesis còn nguyên vẹn, momentum tốt.
- NEUTRAL   : Thesis chưa invalidate nhưng cần theo dõi thêm.
- BEARISH   : Thesis đang bị đe dọa nghiêm trọng, cân nhắc reduce/exit.
- WATCHLIST : Chưa đủ dữ liệu để kết luận, giữ trên watchlist.

Output phải là JSON hợp lệ theo cấu trúc đã mô tả. Không thêm nội dung ngoài JSON.
"""

# ---------------------------------------------------------------------------
# Explicit JSON schema shown to the model — keeps prompt in sync with
# ThesisReviewOutput in src/ai/schemas.py.
# ---------------------------------------------------------------------------
_OUTPUT_SCHEMA = """\
### Output Schema (JSON chính xác — không thêm/bớt field)
```json
{
  "verdict": "BULLISH",
  "confidence": 0.75,
  "risk_signals": [
    "Giá đang test support 120,000 — nếu mất sẽ trigger stop-loss"
  ],
  "next_watch_items": [
    "Q2 earnings FPT dự kiến 15/5 — xem NIM có giữ được 28% không"
  ],
  "reasoning": "Thesis còn nguyên vẹn vì... (tối đa 300 từ)",
  "assumption_recommendations": [
    {
      "target_id": 42,
      "description": "Mô tả ngắn assumption để user nhận diện",
      "recommended_status": "valid",
      "reason": "Lý do AI đề xuất status này"
    }
  ],
  "catalyst_recommendations": [
    {
      "target_id": 46,
      "description": "Mô tả ngắn catalyst để user nhận diện",
      "recommended_status": "triggered",
      "reason": "Lý do AI đề xuất status này"
    }
  ]
}
```
Lưu ý:
- `confidence`: float từ 0.0 đến 1.0 (VD: 0.75 = 75% tin cậy).
- `assumption_recommendations`: chỉ list các assumption cần thay đổi status; bỏ qua nếu không có thay đổi.
- `catalyst_recommendations`: chỉ list các catalyst cần thay đổi status; bỏ qua nếu không có thay đổi.
- `recommended_status` cho assumption: "valid" | "invalid" | "uncertain".
- `recommended_status` cho catalyst: "triggered" | "expired" | "cancelled".
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
        Formatted user prompt string.
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

    lines += [
        _OUTPUT_SCHEMA,
        "### Yêu cầu",
        "Dựa trên thông tin trên, hãy:",
        "1. Đánh giá từng assumption còn giá trị hay không — điền vào assumption_recommendations.",
        "2. Cập nhật trạng thái catalyst nếu cần — điền vào catalyst_recommendations.",
        "3. Đưa ra verdict tổng thể (BULLISH / NEUTRAL / BEARISH / WATCHLIST).",
        "4. Liệt kê risk_signals dưới dạng plain string (không phải dict).",
        "5. Đưa ra next_watch_items cụ thể cho 2–4 tuần tới (plain string).",
        "6. Reasoning ngắn gọn (tối đa 300 từ) giải thích verdict.",
        "",
        "Trả về JSON theo đúng Output Schema ở trên. Không giải thích ngoài JSON.",
    ]

    return "\n".join(lines)


def build_review_prompt(
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
) -> str:
    """Alias of build_user_prompt with optional memory_context injection.

    Used by ThesisReviewAgent which injects investor memory into the prompt.
    memory_context is appended as a separate section when non-empty.
    """
    base = build_user_prompt(
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
    if memory_context:
        base += f"\n\n### Bối cảnh nhà đầu tư (memory)\n{memory_context}"
    return base
