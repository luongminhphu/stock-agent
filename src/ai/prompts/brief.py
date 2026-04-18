"""Prompt pack for BriefingAgent.

Owner: ai segment.
Keep prompts in this file; agent logic stays in agents/briefing.py.
"""
from __future__ import annotations

SYSTEM_PROMPT = """Bạn là chuyên gia phân tích thị trường chứng khoán Việt Nam (HOSE, HNX, UPCoM).
Nhiệm vụ của bạn là tạo ra bản tóm tắt thị trường ngắn gọn, có cấu trúc, hữu ích cho nhà đầu tư.

Quy tắc:
- Luôn trả về JSON hợp lệ, không có text thừa bên ngoài JSON.
- Ngôn ngữ: tiếng Việt, giọng chuyên nghiệp nhưng dễ hiểu.
- Tập trung vào thông tin actionable, không lan man.
- Với watchlist: chỉ đề cập ticker nếu có điều đáng chú ý thực sự.

JSON schema:
{
  "headline": "string — một câu tóm tắt thị trường hôm nay",
  "sentiment": "RISK_ON | RISK_OFF | MIXED | UNCERTAIN",
  "summary": "string — 2-3 câu narrative",
  "key_movers": ["ticker hoặc ngành đáng chú ý"],
  "watchlist_alerts": ["quan sát cụ thể về watchlist"],
  "action_items": ["gợi ý hành động cụ thể cho nhà đầu tư"]
}
"""


def build_morning_prompt(
    market_context: str,
    watchlist_tickers: list[str],
    extra_context: str = "",
) -> str:
    """Build user message for morning brief."""
    ticker_str = ", ".join(watchlist_tickers) if watchlist_tickers else "(không có watchlist)"
    prompt = f"""[MORNING BRIEF — Phiên hôm nay]

Dữ liệu thị trường:
{market_context or 'Chưa có dữ liệu pre-market.'}

Watchlist cần theo dõi: {ticker_str}
"""
    if extra_context:
        prompt += f"\nThông tin bổ sung:\n{extra_context}\n"

    prompt += "\nTạo morning brief theo JSON schema đã được định nghĩa."
    return prompt


def build_eod_prompt(
    market_context: str,
    watchlist_tickers: list[str],
    extra_context: str = "",
) -> str:
    """Build user message for end-of-day brief."""
    ticker_str = ", ".join(watchlist_tickers) if watchlist_tickers else "(không có watchlist)"
    prompt = f"""[EOD BRIEF — Tổng kết phiên]

Diễn biến phiên hôm nay:
{market_context or 'Chưa có dữ liệu EOD.'}

Watchlist cần review: {ticker_str}
"""
    if extra_context:
        prompt += f"\nThông tin bổ sung:\n{extra_context}\n"

    prompt += "\nTạo EOD brief theo JSON schema đã được định nghĩa."
    return prompt
