"""Prompt pack for InvestorAgent.

Owner: ai segment.
Contains system prompt and user message builder for general-purpose
stock analysis. Business logic and output parsing stay in the agent.
"""

SYSTEM_PROMPT = """\
Bạn là chuyên gia phân tích cổ phiếu Việt Nam (HOSE, HNX, UPCoM).
Phân tích cổ phiếu được hỏi và trả về JSON với verdict, confidence, risk_level,
các điểm tích cực/tiêu cực, và tóm tắt ngắn gọn.
Chỉ trả về JSON, không có text thừa.
"""

_RESPONSE_SHAPE = """\
{
  "ticker": "...",
  "verdict": "BULLISH|BEARISH|NEUTRAL|WATCHLIST",
  "confidence": 0.0-1.0,
  "risk_level": "LOW|MEDIUM|HIGH|CRITICAL",
  "price_target_note": "...",
  "key_positives": ["..."],
  "key_negatives": ["..."],
  "summary": "..."
}"""


def build_user_prompt(ticker: str, context: str = "") -> str:
    """Build the user message for a single-ticker analysis request.

    Args:
        ticker: Stock symbol, e.g. "VNM", "HPG".
        context: Optional extra context supplied by the caller.

    Returns:
        Formatted user message string ready to send as {"role": "user", ...}.
    """
    msg = f"Phân tích cổ phiếu {ticker} cho thị trường chứng khoán Việt Nam."
    if context:
        msg += f"\nContext bổ sung: {context}"
    msg += f"\n\nTrả về JSON:\n{_RESPONSE_SHAPE}\n"
    return msg
