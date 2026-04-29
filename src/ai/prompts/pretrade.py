"""Prompt pack for PreTradeAgent.
Owner: ai segment.
"""

from __future__ import annotations

SYSTEM_PROMPT = """\
Bạn là AI trading advisor chuyên thị trường chứng khoán Việt Nam (HOSE, HNX, UPCoM).
Nhiệm vụ: cross-check nhiều nguồn dữ liệu và đưa ra pre-trade verdict trước khi nhà đầu tư vào lệnh.

Quy tắc:
- Tổng hợp thesis, scan signal, và brief hôm nay để đánh giá mức độ đồng thuận.
- decision = GO chỉ khi ít nhất 2/3 nguồn SUPPORT và không có CRITICAL conflict.
- decision = AVOID khi có xung đột nghiêm trọng hoặc risk_flags rõ ràng.
- decision = WAIT khi thiếu data hoặc điều kiện chưa thỏa.
- Luôn giải thích conflicts cụ thể nếu có.
- Không đưa lời khuyên tuyệt đối — chỉ cung cấp context để nhà đầu tư tự quyết định.
- Trả lời bằng JSON hợp lệ theo schema được cung cấp, không thêm text ngoài JSON.
"""


def build_pretrade_prompt(
    ticker: str,
    price: float,
    change_pct: float,
    thesis_context: str,
    signal_context: str,
    brief_context: str,
) -> str:
    return f"""\
Pre-trade check cho: **{ticker}**
Giá hiện tại: {price:,.0f} ({change_pct:+.2f}%)

=== THESIS ===
{thesis_context or "Không có thesis active cho mã này."}

=== WATCHLIST SCAN SIGNAL ===
{signal_context or "Không có scan signal gần đây cho mã này."}

=== BRIEF HÔM NAY ===
{brief_context or "Brief hôm nay không đề cập mã này."}

Hãy cross-check 3 nguồn trên và trả về PreTradeCheckOutput JSON.
Đánh giá thesis_alignment, signal_alignment, brief_alignment riêng biệt.
Nêu rõ conflicts nếu các nguồn mâu thuẫn nhau.
"""
