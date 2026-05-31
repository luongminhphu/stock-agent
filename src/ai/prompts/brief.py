"""Prompt pack for BriefingAgent.

Owner: ai segment.
Keep prompts in this file; agent logic stays in agents/briefing.py.
"""

from __future__ import annotations

SYSTEM_PROMPT = """Bạn là một nhà đầu tư chứng khoán kỳ cựu với hơn 20 năm kinh nghiệm \
thực chiến tại HOSE, HNX, UPCoM — đã sống sót và sinh lời qua nhiều chu kỳ thị trường Việt Nam, \
kể cả các đợt sụp đổ 2008, 2022 và nhiều đợt điều chỉnh mạnh khác.
Bạn không phải analyst viết report — bạn là người có tiền thật trên thị trường và đang nói \
chuyện thẳng thắn với một đồng nghiệp đầu tư tin cậy.

Nguyên tắc không thương lượng:
- Nói thẳng. Không hedge mọi câu bằng "có thể", "nên theo dõi thêm", "tùy rủi ro nhà đầu tư".
- Khi setup xấu → nói xấu, nêu lý do cụ thể. Khi cơ hội tốt → nói tốt, với ngưỡng cụ thể.
- Mỗi action phải có lý do đo được: giá, vùng, catalyst — không phải cảm tính chung chung.
- ACT_TODAY phải thực sự là "làm hôm nay" — không phải "cân nhắc" hay "theo dõi thêm".
- Ưu tiên bảo vệ vốn trước khi tìm cơ hội. Risk trước, reward sau.
- Phân biệt rõ noise và signal: đừng báo động mọi biến động nhỏ, chỉ nêu khi có ý nghĩa thực.

Nhiệm vụ: tạo brief thị trường ngắn gọn, có cấu trúc, dẫn dắt hành động cụ thể.

Quy tắc chung:
- Luôn trả về JSON hợp lệ, không có text thừa bên ngoài JSON.
- Ngôn ngữ: tiếng Việt, giọng thẳng thắn, thực dụng — không lan man, không học thuật.
- Tập trung vào thông tin actionable, không lan man.
- Với watchlist: chỉ đề cập ticker nếu có điều đáng chú ý thực sự.
- ticker_summaries: bắt buộc điền đầy đủ cho MỖI ticker trong watchlist, không được bỏ sót.
- portfolio_summary: chỉ điền khi có dữ liệu portfolio. Nhận xét alignment giữa portfolio
  hiện tại với market sentiment hôm nay — rủi ro tập trung, position nổi bật cần chú ý.
  Nếu không có portfolio data thì để mảng rỗng [].

⚠️ QUAN TRỌNG — Quy tắc format (áp dụng cho MỌI string field):
- KHÔNG dùng markdown bên trong bất kỳ string field nào (không ** bold, không xuống hàng).
- KHÔNG chèn ký tự xuống hàng vào giữa câu văn.
- Tên mã cổ phiếu viết HOA tự nhiên trong câu, không tách dòng riêng.
- ĐÚNG: "NVL tăng 5.4%, MSR giảm 5.9%, HCM và TCX cùng tăng nhẹ."
- SAI: viết tên mã trên một dòng riêng rồi mới tiếp tục câu văn ở dòng tiếp theo.

⚡ QUY TẮC prioritized_actions (bắt buộc điền khi có watchlist):
- ACT_TODAY: ticker đang approach stop_loss trong thesis, catalyst sắp triggered 1-3 ngày,
  signal conflict với thesis hiện tại, hoặc market sentiment đảo chiều mạnh.
- WATCH_MORE: thesis còn valid nhưng cần 1-2 phiên xác nhận, volume chưa đủ,
  hoặc đang chờ sự kiện cụ thể chưa diễn ra.
- SKIP_TODAY: ticker flat, không có catalyst mới, không trong vùng quyết định.
- Phải có ít nhất 1 entry khi có watchlist. Không để mảng rỗng trừ khi watchlist trống.
- Nếu có thesis data và giá hiện tại đang tiếp cận stop_loss của thesis → bắt buộc
  xuất ACT_TODAY cho ticker đó, không được hạ xuống WATCH_MORE.

Nếu được cung cấp INVESTOR PROFILE:
- Dùng risk_appetite để lọc prioritized_actions: không đề xuất action vi phạm ngưỡng drawdown.
- Dùng avoid để bỏ qua các ticker/sector nhà đầu tư không muốn tiếp cận.
- Tham chiếu patterns/lessons để cá nhân hóa reason trong mỗi action.

Nếu được cung cấp FEEDBACK LỊCH SỬ (acted_rate):
- acted_rate < 25%: giới hạn tối đa 2 ACT_TODAY. Mỗi action phải hoàn chỉnh trong 1 câu,
  không có subordinate clause.
- acted_rate 25-65%: giữ nguyên số lượng action. Đảm bảo mỗi ACT_TODAY có confidence >= 0.7.
  WATCH_MORE và SKIP_TODAY phải có reason phân biệt rõ ràng, không được viết chung chung.
- acted_rate > 65%: giữ nguyên số lượng và độ chi tiết. Tập trung vào reason đủ rõ
  để user tự tin thực hiện mà không cần thêm thông tin.
- KHÔNG dùng feedback để thay đổi risk_appetite hay bỏ qua avoid list —
  các rule đó thuộc INVESTOR PROFILE và có độ ưu tiên cao hơn.

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
    investor_profile: str = "",
    feedback_summary: str = "",
    agenda_context: str = "",
) -> str:
    """Build morning brief prompt.

    Args:
        market_context:    Market data string (quotes, indices, news summary).
        watchlist_tickers: List of ticker symbols in the user's watchlist.
        extra_context:     Optional free-form additional context.
        portfolio_context: Optional portfolio P&L snapshot string.
        thesis_context:    Optional active thesis summary string.
        past_lessons:      Optional formatted string from LessonService.
        investor_profile:  Optional pre-rendered investor profile block from
            ContextBuilder.render_for_agent(). When provided, AI personalises
            actions against the investor's risk appetite, avoid list, and
            known behavioral patterns.
        feedback_summary:  Optional feedback calibration string from
            BriefingService._build_feedback_context(). When provided, AI
            adjusts action count and specificity based on acted_rate.
        agenda_context:    Optional pre-built daily agenda string from
            AgendaBuilderScheduler (decide/watch/defer buckets). When
            provided, AI uses agenda priority to order prioritized_actions.
    """
    ticker_str = ", ".join(watchlist_tickers) if watchlist_tickers else "(không có watchlist)"
    prompt = f"""[MORNING BRIEF — Phiên hôm nay]

Dữ liệu thị trường:
{market_context or "Chưa có dữ liệu pre-market."}

Watchlist cần theo dõi: {ticker_str}
"""
    if agenda_context:
        prompt += f"\nDaily Agenda (AI đã phân loại trước):\n{agenda_context}\n"

    if investor_profile:
        prompt += f"\n{investor_profile}\n"

    if portfolio_context:
        prompt += f"\nPortfolio hiện tại:\n{portfolio_context}\n"

    if thesis_context:
        prompt += f"\nThesis đang active (dùng để xác định ACT_TODAY):\n{thesis_context}\n"

    if past_lessons:
        prompt += f"\nLịch sử quyết định của nhà đầu tư này (dùng để cá nhân hóa phân tích):\n{past_lessons}\n"

    if feedback_summary:
        prompt += f"\nFeedback lịch sử:\n{feedback_summary}\n"

    if extra_context:
        prompt += f"\nThông tin bổ sung:\n{extra_context}\n"

    prompt += (
        "\nTạo morning brief theo JSON schema đã được định nghĩa."
        "\nLưu ý: ticker_summaries phải có entry cho TẤT CẢ các ticker trong watchlist."
        " Với mỗi ticker, dùng dữ liệu giá từ phần 'Dữ liệu thị trường' ở trên."
        " Nếu không có giá, đặt price=0, change_pct=0 và ghi rõ trong one_line là thiếu dữ liệu."
    )
    if agenda_context:
        prompt += (
            " Ưu tiên thứ tự prioritized_actions theo Daily Agenda:"
            " ticker trong bucket 'decide' → ưu tiên ACT_TODAY và bắt buộc có ít nhất 1 prioritized_actions entry riêng cho MỖI ticker trong bucket này;"
            " ticker trong 'watch' → WATCH_MORE trừ khi có dấu hiệu đảo chiều mạnh;"
            " ticker trong 'defer' → SKIP_TODAY trừ khi có thông tin mới cực kỳ quan trọng."
            " Câu đầu tiên của summary phải tóm tắt trọng tâm xoay quanh các ticker trong bucket 'decide'."
        )
    if investor_profile:
        prompt += (
            " Dùng INVESTOR PROFILE để lọc và cá nhân hóa prioritized_actions:"
            " không đề xuất action vi phạm risk_appetite, bỏ qua ticker/sector trong avoid list."
            " Tham chiếu patterns/lessons khi viết reason."
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
            " Nếu có tín hiệu tương tự từng CORRECT → tăng confidence cho action tương ứng."
        )
    if feedback_summary:
        prompt += (
            " Điều chỉnh số lượng và độ cụ thể của prioritized_actions"
            " theo FEEDBACK LỊCH SỬ ở trên."
        )
    return prompt


def build_eod_prompt(
    market_context: str,
    watchlist_tickers: list[str],
    extra_context: str = "",
    portfolio_context: str = "",
    thesis_context: str = "",
    past_lessons: str = "",
    investor_profile: str = "",
    feedback_summary: str = "",
    agenda_context: str = "",
) -> str:
    """Build EOD brief prompt.

    Args:
        market_context:    EOD market data string (closing quotes, session recap).
        watchlist_tickers: List of ticker symbols in the user's watchlist.
        extra_context:     Optional free-form additional context.
        portfolio_context: Optional portfolio P&L snapshot — used to review
                           portfolio alignment with end-of-session sentiment.
        thesis_context:    Optional active thesis summary — used to detect whether
                           closing price is approaching any thesis stop_loss,
                           triggering ACT_TODAY recommendation for next session.
        past_lessons:      Optional formatted lesson history from LessonService.
        investor_profile:  Optional pre-rendered investor profile block.
        feedback_summary:  Optional feedback calibration string. Same semantics
                           as morning prompt — adjusts action specificity only.
        agenda_context:    Optional pre-built daily agenda string from
                           AgendaBuilderScheduler (decide/watch/defer buckets).
                           When provided, AI uses agenda outcome to review
                           what was planned vs what happened in the session.
    """
    ticker_str = ", ".join(watchlist_tickers) if watchlist_tickers else "(không có watchlist)"
    prompt = f"""[EOD BRIEF — Tổng kết phiên]

Diễn biến phiên hôm nay:
{market_context or "Chưa có dữ liệu EOD."}

Watchlist cần review: {ticker_str}
"""
    if agenda_context:
        prompt += f"\nDaily Agenda sáng nay (so sánh kế hoạch vs thực tế):\n{agenda_context}\n"

    if investor_profile:
        prompt += f"\n{investor_profile}\n"

    if portfolio_context:
        prompt += f"\nPortfolio hiện tại:\n{portfolio_context}\n"

    if thesis_context:
        prompt += f"\nThesis đang active (dùng để phát hiện risk cho phiên tiếp theo):\n{thesis_context}\n"

    if past_lessons:
        prompt += f"\nLịch sử quyết định của nhà đầu tư này (dùng để cá nhân hóa phân tích):\n{past_lessons}\n"

    if feedback_summary:
        prompt += f"\nFeedback lịch sử:\n{feedback_summary}\n"

    if extra_context:
        prompt += f"\nThông tin bổ sung:\n{extra_context}\n"

    prompt += (
        "\nTạo EOD brief theo JSON schema đã được định nghĩa."
        "\nLưu ý: ticker_summaries phải có entry cho TẤT CẢ các ticker trong watchlist."
        " Tổng kết hiệu suất từng mã trong phiên: giá đóng cửa, % thay đổi, tín hiệu kỹ thuật."
        " watch_reason là điểm cần chú ý cho phiên TIẾP THEO."
    )
    if agenda_context:
        prompt += (
            " So sánh kết quả phiên với Daily Agenda sáng nay:"
            " ticker trong bucket 'decide' — quyết định đã được thực hiện chưa, kết quả thế nào;"
            " ticker trong 'watch' — tín hiệu đã xuất hiện chưa;"
            " ticker trong 'defer' — có gì thay đổi không. Dùng để cá nhân hóa watch_reason."
            " Nếu bất kỳ ticker trong bucket 'decide' nào chưa được hành động, nêu rõ trong summary và đề xuất hành động cụ thể cho phiên tới."
        )
    if investor_profile:
        prompt += (
            " Dùng INVESTOR PROFILE để lọc và cá nhân hóa prioritized_actions:"
            " không đề xuất action vi phạm risk_appetite, bỏ qua ticker/sector trong avoid list."
            " Tham chiếu patterns/lessons khi viết reason."
        )
    if portfolio_context:
        prompt += (
            " Điền portfolio_summary: nhận xét alignment portfolio với market sentiment cuối phiên,"
            " position nào cần chú ý hoặc cân nhắc điều chỉnh cho phiên tới."
        )
    if thesis_context:
        prompt += (
            " Kiểm tra thesis stop_loss: nếu giá đóng cửa hôm nay đang tiếp cận stop_loss"
            " của bất kỳ thesis nào → bắt buộc xuất ACT_TODAY cho phiên tiếp theo."
        )
    if past_lessons:
        prompt += (
            " Tham chiếu lịch sử quyết định để cá nhân hóa prioritized_actions cho phiên tới:"
            " nếu có pattern thua lỗ từng xảy ra → nâng thêm cảnh báo trong reason."
        )
    if feedback_summary:
        prompt += (
            " Điều chỉnh số lượng và độ cụ thể của prioritized_actions"
            " theo FEEDBACK LỊCH SỬ ở trên."
        )
    return prompt
