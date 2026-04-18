"""Prompt pack for ThesisReviewAgent.

Owner: ai segment.
Keep prompts here; agent logic stays in agents/thesis_review.py.
"""
from __future__ import annotations

SYSTEM_PROMPT = """Bạn là chuyên gia phân tích đầu tư cổ phiếu Việt Nam (HOSE, HNX, UPCoM).
Nhiệm vụ: review một investment thesis và đưa ra đánh giá có cấu trúc.

Quy tắc:
- Luôn trả về JSON hợp lệ, không có text thừa.
- Verdict phải là một trong: BULLISH, BEARISH, NEUTRAL, WATCHLIST.
- Confidence: 0.0 (rất không chắc) đến 1.0 (rất chắc chắn).
- risk_signals: danh sách rủi ro cụ thể, có thể đo lường.
- next_watch_items: sự kiện/data point cần theo dõi tiếp.
- assumption_updates: assumptions nào cần được revisit.
- catalyst_status: update về tiến độ của từng catalyst.
- reasoning: giải thích ngắn gọn, rõ ràng bằng tiếng Việt.

JSON schema:
{
  "verdict": "BULLISH|BEARISH|NEUTRAL|WATCHLIST",
  "confidence": 0.0-1.0,
  "risk_signals": ["..."],
  "next_watch_items": ["..."],
  "reasoning": "...",
  "assumption_updates": ["..."],
  "catalyst_status": ["..."]
}
"""


def build_review_prompt(
    ticker: str,
    thesis_title: str,
    thesis_summary: str,
    assumptions: list[str],
    catalysts: list[str],
    current_price: float | None = None,
    entry_price: float | None = None,
    target_price: float | None = None,
) -> str:
    """Build the user message for a thesis review."""
    lines = [
        f"**Thesis Review: {ticker} — {thesis_title}**",
        "",
        f"Tóm tắt thesis: {thesis_summary}",
        "",
    ]

    if assumptions:
        lines.append("Assumptions:")
        for i, a in enumerate(assumptions, 1):
            lines.append(f"  {i}. {a}")
        lines.append("")

    if catalysts:
        lines.append("Catalysts:")
        for i, c in enumerate(catalysts, 1):
            lines.append(f"  {i}. {c}")
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

    lines.append("Review thesis này và trả về JSON theo schema đã định nghĩa.")
    return "\n".join(lines)
