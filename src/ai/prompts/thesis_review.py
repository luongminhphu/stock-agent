"""Prompt pack for ThesisReviewAgent.

Owner: ai segment.
Keep prompts here; agent logic stays in agents/thesis_review.py.
"""

from __future__ import annotations

SYSTEM_PROMPT = """Bạn là chuyên gia phân tích đầu tư cổ phiếu Việt Nam (HOSE, HNX, UPCoM).
Nhiệm vụ: review một investment thesis và đưa ra đánh giá có cấu trúc.

Quy tắc:
- Luôn trả về JSON hợp lệ, không có text thừa bên ngoài JSON.
- Verdict phải là một trong: BULLISH, BEARISH, NEUTRAL, WATCHLIST.
- Confidence: 0.0 (rất không chắc) đến 1.0 (rất chắc chắn).
- risk_signals: danh sách rủi ro cụ thể, có thể đo lường.
- next_watch_items: sự kiện/data point cần theo dõi tiếp.
- reasoning: giải thích ngắn gọn, rõ ràng bằng tiếng Việt.
- assumption_recommendations: với MỖI assumption được cung cấp, đánh giá status và
  trả về object có target_id (integer ID đúng như đã cho), description, recommended_status
  (valid | invalid | uncertain), và reason.
- catalyst_recommendations: với MỖI catalyst PENDING được cung cấp, đánh giá tiến độ
  và trả về object có target_id (integer ID đúng như đã cho), description,
  recommended_status (triggered | expired | cancelled | pending), và reason.
  Chỉ đề xuất status khác "pending" nếu có bằng chứng rõ ràng.
- Nếu có lịch sử review trước, hãy dùng làm "anchor" để đánh giá sự thay đổi.
  Chỉ thay đổi verdict khi có lý do cụ thể, rõ ràng. Giải thích delta so với lần trước.

JSON schema:
{
  "verdict": "BULLISH|BEARISH|NEUTRAL|WATCHLIST",
  "confidence": 0.0,
  "risk_signals": ["..."],
  "next_watch_items": ["..."],
  "reasoning": "...",
  "assumption_recommendations": [
    {
      "target_id": 1,
      "description": "Tên assumption",
      "recommended_status": "valid|invalid|uncertain",
      "reason": "Lý do"
    }
  ],
  "catalyst_recommendations": [
    {
      "target_id": 1,
      "description": "Tên catalyst",
      "recommended_status": "triggered|expired|cancelled|pending",
      "reason": "Lý do"
    }
  ]
}
"""


def build_review_prompt(
    ticker: str,
    thesis_title: str,
    thesis_summary: str,
    assumptions_with_ids: list[dict[str, object]],
    catalysts_with_ids: list[dict[str, object]],
    triggered_catalysts_with_ids: list[dict[str, object]] | None = None,
    current_price: float | None = None,
    entry_price: float | None = None,
    target_price: float | None = None,
    memory_context: str = "",  # NEW: episodic + semantic memory block
) -> str:
    """Build the user message for a thesis review.

    Args:
        assumptions_with_ids:          Active assumptions — list[{"id": int, "description": str}].
        catalysts_with_ids:            PENDING catalysts  — list[{"id": int, "description": str}].
        triggered_catalysts_with_ids:  TRIGGERED catalysts — list[{"id": int, "description": str}].
        memory_context:                Rendered memory block from MemoryService (optional).
    """
    lines = [
        f"**Thesis Review: {ticker} — {thesis_title}**",
        "",
        f"Tóm tắt thesis: {thesis_summary}",
        "",
    ]

    # Memory context injection — AI dùng làm anchor, không override data thực tế
    if memory_context:
        lines.append("[Lịch sử AI review trước đây — dùng làm anchor, không phải sự thật tuyệt đối]")
        lines.append(memory_context)
        lines.append("")

    if assumptions_with_ids:
        lines.append("Assumptions (dùng đúng id khi trả về assumption_recommendations):")
        for a in assumptions_with_ids:
            lines.append(f"  [id={a['id']}] {a['description']}")
        lines.append("")

    if catalysts_with_ids:
        lines.append("Catalysts sắp tới — PENDING (dùng đúng id khi trả về catalyst_recommendations):")
        for c in catalysts_with_ids:
            lines.append(f"  [id={c['id']}] {c['description']}")
        lines.append("")

    if triggered_catalysts_with_ids:
        lines.append("Catalysts đã kích hoạt — TRIGGERED (tham khảo, không cần recommend):")
        for c in triggered_catalysts_with_ids:
            lines.append(f"  [id={c['id']}] {c['description']}")
        lines.append("")

    price_parts = []
    if entry_price is not None:
        price_parts.append(f"Entry: {entry_price:,.0f}")
    if current_price is not None:
        price_parts.append(f"Current: {current_price:,.0f}")
    if target_price is not None:
        price_parts.append(f"Target: {target_price:,.0f}")
    if price_parts:
        lines.append("Giá: " + " | ".join(price_parts))
        lines.append("")

    lines.append(
        "Review thesis này và trả về JSON theo schema đã định nghĩa.\n"
        "Quan trọng: target_id trong assumption_recommendations và catalyst_recommendations "
        "phải khớp CHÍNH XÁC với id đã cung cấp ở trên."
    )
    return "\n".join(lines)
