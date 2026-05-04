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
- ticker_summaries: bắt buộc điền đầy đủ cho MỔI ticker trong watchlist, không được bỏ sót.
- portfolio_summary: chỉ điền khi có dữ liệu portfolio. Nhận xét alignment giữa portfolio
  hiện tại với market sentiment hôm nay — rủi ro tập trung, position nổi bật cần chú ý.
  Nếu không có portfolio data thì để mảng rỗng [].

⚠️ QUAN TRỌNG — Quy tắc format (áp dụng cho MỌI string field):
- KHÔNG dùng markdown bên trong bất kỳ string field nào (không ** bold, không xuống hàng).
- KHÔNG chèn ký tự xuống hàng vào giữa câu văn.
- Tên mã cổ phiếu viết HOA tự nhiên trong câu, không tách dòng riêng.
- ĐÚNG: "NVL tăng 5.4%, MSR giảm 5.9%, HCM và TCX cùng tăng nhẹ."
- SAI: viết tên mã trên một dòng riêng rồi mới tiếp tục câu văn ở dòng tiếp theo.

⚡ QUY TẬC prioritized_actions (bắt buộc điền khi có watchlist):
- ACT_TODAY: ticker đang approach stop_loss trong thesis, catalyst sắp triggered 1-3 ngày,
  signal conflict với thesis hiện tại, hoặc market sentiment đảo chiều mạnh.
- WATCH_MORE: thesis còn valid nhưng cần 1-2 phiên xác nhận, volume chưa đủ,
  hoặc đang chờ sự kiện cụ thể chưa diễn ra.
- SKIP_TODAY: ticker flat, không có catalyst mới, không trong vùng quyết định.
- Phải có ít nhất 1 entry khi có watchlist. Không để mảng rỗng trừ khi watchlist trống.
- Nếu có thesis data và giá hiện tại đang tiếp cận stop_loss của thesis → bắt buộc
  xuất ACT_TODAY cho ticker đó, không được hạ xuống WATCH_MORE.

JSON schema:
{
  "headline": "string — TỐI ĐA 15 từ, mô tả tâm lý/xu hướng chính, không liệt kê ticker",
  "sentiment": "RISK_ON | RISK_OFF | MIXED | UNCERTAIN",
  "summary": "string — 2-3 câu narrative LIÊN TỤC, không xuống hàng, không markdown",
  "key_movers": ["chỉ ticker hoặc tên ngành ngắn, VD: 'NVL', 'MSR', 'Bất động sản'"],
  "watchlist_alerts": ["mỗi item là 1 câu liên tục, không markdown, không xuống hàng"],
  "action_items": [],
  "prioritized_actions": [
    {
      "ticker": "string (mã CK viết hoa) hoặc null nếu là action market-level",
      "priority": "ACT_TODAY | WATCH_MORE | SKIP_TODAY",
      "action": "string — hành động cụ thể, có thể đo được, không markdown",
      "reason": "string — lý do ngắn gọn (1 câu), không markdown",
      "confidence": "number 0.0-1.0"
    }
  ],
  "ticker_summaries": [
    {
      "ticker": "string — mã CK viết hoa, VD: VNM",
      "price": "number — giá đóng cửa / hiện tại",
      "change_pct": "number — % thay đổi so với phiên trước, VD: -1.25",
      "signal": "bullish | bearish | neutral",
      "one_line": "string — 1 câu liên tục, không markdown",
      "watch_reason": "string — 1 câu liên tục, không markdown"
    }
  ],
  "portfolio_summary": [
    "string — mỗi item là 1 nhận xét portfolio liên tục, không markdown, không xuống hàng."
    " VD: 'VCB đang lãi +8.2%, phiên hôm nay sentiment RISK_OFF — cân nhắc chốt một phần.'"
    " Rỗng nếu không có portfolio data."
  ]
}
"""


def build_morning_prompt(
    market_context: str,
    watchlist_tickers: list[str],
    extra_context: str = "",
    portfolio_context: str = "",
    thesis_context: str = "",
    past_lessons: str = "",
) -> str:
    """Build morning brief prompt.

    Args:
        market_context: Market data string (quotes, indices, news summary).
        watchlist_tickers: List of ticker symbols in the user's watchlist.
        extra_context: Optional free-form additional context.
        portfolio_context: Optional portfolio P&L snapshot string.
        thesis_context: Optional active thesis summary string. When provided,
            AI will cross-reference thesis stop_loss levels against current price
            and force ACT_TODAY for any ticker approaching invalidation.
        past_lessons: Optional formatted string from LessonService — recent
            evaluated decisions with outcome verdicts, key lessons, and detected
            patterns. When provided, AI will personalise analysis by referencing
            the investor's own historical decision quality.
    """
    ticker_str = ", ".join(watchlist_tickers) if watchlist_tickers else "(không có watchlist)"
    prompt = f"""[MORNING BRIEF — Phiên hôm nay]

Dữ liệu thị trường:
{market_context or "Chưa có dữ liệu pre-market."}

Watchlist cần theo dõi: {ticker_str}
"""
    if portfolio_context:
        prompt += f"\nPortfolio hiện tại:\n{portfolio_context}\n"

    if thesis_context:
        prompt += f"\nThesis đang active (dùng để xác định ACT_TODAY):\n{thesis_context}\n"

    if past_lessons:
        prompt += f"\nLịch sử quyết định của nhà đầu tư này (dùng để cá nhân hóa phân tích):\n{past_lessons}\n"

    if extra_context:
        prompt += f"\nThông tin bổ sung:\n{extra_context}\n"

    prompt += (
        "\nTạo morning brief theo JSON schema đã được định nghĩa."
        "\nLưu ý: ticker_summaries phải có entry cho TẤT CẢ các ticker trong watchlist."
        " Với mỗi ticker, dùng dữ liệu giá từ phần 'Dữ liệu thị trường' ở trên."
        " Nếu không có giá, đặt price=0, change_pct=0 và ghi rõ trong one_line là thiếu dữ liệu."
    )
    if portfolio_context:
        prompt += (
            " Điền portfolio_summary dựa trên dữ liệu portfolio ở trên:"
            " nhận xét alignment với market sentiment hôm nay, position nào cần chú ý."
        )
    if thesis_context:
        prompt += (
            " Điền prioritized_actions dựa trên thesis data:"
            " nếu giá hiện tại đang tiếp cận stop_loss của bất kỳ thesis nào"
            " → bắt buộc xuất ACT_TODAY cho ticker đó với lý do rõ ràng."
        )
    if past_lessons:
        prompt += (
            " Tham chiếu lịch sử quyết định để cá nhân hóa prioritized_actions:"
            " nếu có pattern thua lỗ từng xảy ra → nâng thêm cảnh báo trong reason."
            " Nếu có tín hiệu tương tự từng CORRECT → tăng confidence cho action tươngứng."
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
