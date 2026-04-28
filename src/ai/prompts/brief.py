"""Prompt pack for BriefingAgent.

Owner: ai segment.
Keep prompts in this file; agent logic stays in agents/briefing.py.
"""

from __future__ import annotations

SYSTEM_PROMPT = """Bạn là chuyên gia phân tích thị trường chứng khoán Việt Nam (HOSE, HNX, UPCoM).
Nhiệm vụ của bạn là tạo ra bản tóm tắt thị trường ngắn gọn, có cấu trúc, hữu ích cho nhà đầu tư.

Quy tắc chung:
- Luôn trả về JSON hợp lệ, không có text thừa bên ngoài JSON.
- Ngôn ngữ: tiếng Việt, giọng chuyên nghiệp nhưng dễ hiểu.
- Tập trung vào thông tin actionable, không lan man.
- Với watchlist: chỉ đề cập ticker nếu có điều đáng chú ý thực sự.
- ticker_summaries: bắt buộc điền đầy đủ cho MỖI ticker trong watchlist, không được bỏ sót.

⚠️ QUAN TRỌNG — Quy tắc format (áp dụng cho MỌI string field):
- KHÔNG dùng markdown bên trong bất kỳ string field nào (không ** bold, không xuống hàng).
- KHÔNG chèn ký tự xuống hàng vào giữa câu văn.
- Tên mã cổ phiếu viết HOA tự nhiên trong câu, không tách dòng riêng.
- ĐÚNG: "NVL tăng 5.4%, MSR giảm 5.9%, HCM và TCX cùng tăng nhẹ."
- SAI: viết tên mã trên một dòng riêng rồi mới tiếp tục câu văn ở dòng tiếp theo.

JSON schema:
{
  "headline": "string — TỐI ĐA 15 từ, mô tả tâm lý/xu hướng chính, không liệt kê ticker",
  "sentiment": "RISK_ON | RISK_OFF | MIXED | UNCERTAIN",
  "summary": "string — 2-3 câu narrative LIÊN TỤC, không xuống hàng, không markdown",
  "key_movers": ["chỉ ticker hoặc tên ngành ngắn, VD: 'NVL', 'MSR', 'Bất động sản'"],
  "watchlist_alerts": ["mỗi item là 1 câu liên tục, không markdown, không xuống hàng"],
  "action_items": ["mỗi item là 1 câu liên tục, không markdown, không xuống hàng"],
  "ticker_summaries": [
    {
      "ticker": "string — mã CK viết hoa, VD: VNM",
      "price": "number — giá đóng cửa / hiện tại",
      "change_pct": "number — % thay đổi so với phiên trước, VD: -1.25",
      "signal": "bullish | bearish | neutral",
      "one_line": "string — 1 câu liên tục, không markdown",
      "watch_reason": "string — 1 câu liên tục, không markdown"
    }
  ]
}
"""


def build_morning_prompt(
    market_context: str,
    watchlist_tickers: list[str],
    extra_context: str = "",
) -> str:
    ticker_str = ", ".join(watchlist_tickers) if watchlist_tickers else "(không có watchlist)"
    prompt = f"""[MORNING BRIEF — Phiên hôm nay]

Dữ liệu thị trường:
{market_context or "Chưa có dữ liệu pre-market."}

Watchlist cần theo dõi: {ticker_str}
"""
    if extra_context:
        prompt += f"\nThông tin bổ sung:\n{extra_context}\n"

    prompt += (
        "\nTạo morning brief theo JSON schema đã được định nghĩa."
        "\nLưu ý: ticker_summaries phải có entry cho TẤT CẢ các ticker trong watchlist."
        " Với mỗi ticker, dùng dữ liệu giá từ phần 'Dữ liệu thị trường' ở trên."
        " Nếu không có giá, đặt price=0, change_pct=0 và ghi rõ trong one_line là thiếu dữ liệu."
    )
    return prompt


def build_eod_prompt(
    market_context: str,
    watchlist_tickers: list[str],
    extra_context: str = "",
) -> str:
    ticker_str = ", ".join(watchlist_tickers) if watchlist_tickers else "(không có watchlist)"
    prompt = f"""[EOD BRIEF — Tổng kết phiên]

Diễn biến phiên hôm nay:
{market_context or "Chưa có dữ liệu EOD."}

Watchlist cần review: {ticker_str}
"""
    if extra_context:
        prompt += f"\nThông tin bổ sung:\n{extra_context}\n"

    prompt += (
        "\nTạo EOD brief theo JSON schema đã được định nghĩa."
        "\nLưu ý: ticker_summaries phải có entry cho TẤT CẢ các ticker trong watchlist."
        " Tổng kết hiệu suất từng mã trong phiên: giá đóng cửa, % thay đổi, tín hiệu kỹ thuật."
        " watch_reason là điểm cần chú ý cho phiên TIẾP THEO."
    )
    return prompt
