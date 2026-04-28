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
- ticker_summaries: bắt buộc điền đầy đủ cho MỖI ticker trong watchlist, không được bỏ sót.

⚠️ QUAN TRỌNG — Quy tắc format cho field "summary":
- Phải là một đoạn văn LIÊN TỤC trên một dòng, KHÔNG được xuống hàng giữa chừng.
- KHÔNG dùng markdown (không bold **, không xuống dòng \\n) bên trong summary.
- Tên mã cổ phiếu viết HOA trong câu văn bình thường, VD: "NVL tăng 5.4% dẫn dắt nhóm BDS, trong khi MSR lao dốc 5.9%."
- SAI: "NVL\\ntăng 5.4%..." hoặc "**NVL**\\ntăng..."
- ĐÚNG: "NVL tăng 5.4%, MSR giảm 5.9%, HCM và TCX cùng tăng nhẹ."

JSON schema:
{
  "headline": "string — một câu tóm tắt thị trường hôm nay",
  "sentiment": "RISK_ON | RISK_OFF | MIXED | UNCERTAIN",
  "summary": "string — 2-3 câu narrative LIÊN TỤC, không xuống hàng, không markdown",
  "key_movers": ["chỉ ticker hoặc tên ngành ngắn, VD: 'NVL', 'MSR', 'Bất động sản'"],
  "watchlist_alerts": ["quan sát cụ thể về watchlist"],
  "action_items": ["gợi ý hành động cụ thể cho nhà đầu tư"],
  "ticker_summaries": [
    {
      "ticker": "string — mã CK viết hoa, VD: VNM",
      "price": "number — giá đóng cửa / hiện tại",
      "change_pct": "number — % thay đổi so với phiên trước, VD: -1.25",
      "signal": "bullish | bearish | neutral",
      "one_line": "string — 1 câu nhận định ngắn gọn về mã này hôm nay",
      "watch_reason": "string — lý do cần theo dõi mã này trong phiên tới"
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
