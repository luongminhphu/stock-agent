"""Prompt pack for BriefingAgent.

Owner: ai segment. Briefing business rules (which tickers to include,
format preferences) live in briefing segment — these prompts only
encode the reasoning style.
"""

MORNING_SYSTEM_PROMPT = """Bạn là trợ lý phân tích chứng khoán Việt Nam.
Nhiệm vụ: viết morning brief ngắn gọn, thực tế cho nhà đầu tư cá nhân.
Tập trung vào tín hiệu hành động, không lan man.
Trả về JSON hợp lệ theo schema.
"""

EOD_SYSTEM_PROMPT = """Bạn là trợ lý phân tích chứng khoán Việt Nam.
Nhiệm vụ: viết end-of-day brief tóm tắt phiên giao dịch và gợi ý cần theo dõi cho ngày mai.
Trả về JSON hợp lệ theo schema.
"""


def build_morning_prompt(market_context: str, watchlist_tickers: list[str]) -> str:
    tickers_str = ", ".join(watchlist_tickers) if watchlist_tickers else "(watchlist trống)"
    return f"""Thông tin thị trường buổi sáng:
{market_context}

Watchlist của nhà đầu tư: {tickers_str}

Viết morning brief và trả về JSON:
{{
  "headline": "...",
  "sentiment": "RISK_ON|RISK_OFF|MIXED|UNCERTAIN",
  "summary": "...",
  "key_movers": ["..."],
  "watchlist_alerts": ["..."],
  "action_items": ["..."]
}}
"""


def build_eod_prompt(market_context: str, watchlist_tickers: list[str]) -> str:
    tickers_str = ", ".join(watchlist_tickers) if watchlist_tickers else "(watchlist trống)"
    return f"""Tóm tắt phiên giao dịch hôm nay:
{market_context}

Watchlist của nhà đầu tư: {tickers_str}

Viết EOD brief và trả về JSON:
{{
  "headline": "...",
  "sentiment": "RISK_ON|RISK_OFF|MIXED|UNCERTAIN",
  "summary": "...",
  "key_movers": ["..."],
  "watchlist_alerts": ["..."],
  "action_items": ["..."]
}}
"""
