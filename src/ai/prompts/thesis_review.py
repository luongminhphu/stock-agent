"""Prompt pack for ThesisReviewAgent.

Owner: ai segment. Business rules about what makes a thesis valid/invalid
live in the thesis segment — these prompts only encode reasoning instructions.
"""

SYSTEM_PROMPT = """Bạn là một chuyên gia phân tích đầu tư chứng khoán Việt Nam (HOSE, HNX, UPCoM).
Nhiệm vụ của bạn là review một investment thesis và đánh giá xem thesis đó còn giá trị không,
dựa trên thông tin thị trường hiện tại.

Yêu cầu output:
- Phân tích khách quan, dựa trên dữ liệu
- Chỉ ra rõ các risk signals cụ thể
- Đưa ra verdict rõ ràng: BULLISH / BEARISH / NEUTRAL / WATCHLIST
- Confidence score từ 0.0 đến 1.0
- Trả về JSON hợp lệ theo schema được cung cấp
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
    """Build the user message for thesis review."""
    price_context = ""
    if current_price is not None:
        price_context = f"\n- Giá hiện tại: {current_price:,.0f} VND"
    if entry_price is not None:
        price_context += f"\n- Giá vào: {entry_price:,.0f} VND"
    if target_price is not None:
        price_context += f"\n- Giá mục tiêu: {target_price:,.0f} VND"

    assumptions_text = "\n".join(f"  - {a}" for a in assumptions) if assumptions else "  (chưa có)"
    catalysts_text = "\n".join(f"  - {c}" for c in catalysts) if catalysts else "  (chưa có)"

    return f"""Review investment thesis sau cho cổ phiếu {ticker}:

**Thesis:** {thesis_title}
**Tóm tắt:** {thesis_summary}{price_context}

**Assumptions:**
{assumptions_text}

**Catalysts:**
{catalysts_text}

Hãy đánh giá thesis này dựa trên thông tin thị trường mới nhất và trả về JSON theo schema:
{{
  "verdict": "BULLISH|BEARISH|NEUTRAL|WATCHLIST",
  "confidence": 0.0-1.0,
  "risk_signals": ["..."],
  "next_watch_items": ["..."],
  "reasoning": "...",
  "assumption_updates": ["..."],
  "catalyst_status": ["..."]
}}
"""
