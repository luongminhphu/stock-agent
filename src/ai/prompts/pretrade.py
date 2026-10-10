"""Prompt pack for PreTradeAgent.
Owner: ai segment.
"""

from __future__ import annotations

from src.ai.prompts._spec import PromptSpec, schema_block, with_persona
from src.ai.schemas import PreTradeCheckOutput

_DOMAIN_RULES = """\
Nhiệm vụ: nhà đầu tư SẮP ĐẶT MỘT LỆNH CỤ THỂ (mua / bán / giảm / giữ). Cross-check
luận điểm, tín hiệu scan, brief, bối cảnh kỹ thuật, thị trường chung và vị thế hiện tại
để trả lời đúng một câu: CÓ NÊN THỰC HIỆN LỆNH NÀY KHÔNG. Đây là kiểm tra cuối cùng
trước khi tiền rời tài khoản — không được mơ hồ.

Ý nghĩa các field (bắt buộc dùng đúng):
- intended_action: CHÉP NGUYÊN hành động nhà đầu tư khai báo ở mục LỆNH DỰ KIẾN.
  Không được tự đổi sang hành động khác.
- verdict: góc nhìn của bạn về CỔ PHIẾU (BULLISH / BEARISH / NEUTRAL), độc lập với lệnh.
- proceed_recommendation: true = nên thực hiện lệnh dự kiến ngay; false = không nên.
  Lệnh BUY/ADD nên đi khi verdict BULLISH và không có blocking issue.
  Lệnh SELL/REDUCE nên đi khi có lý do thoát rõ: thesis bị vô hiệu, chạm stop,
  đạt target, rủi ro danh mục quá tập trung, hoặc verdict BEARISH.
  Lệnh SELL khi verdict BULLISH và thesis còn nguyên → proceed = false, giải thích
  "đang bán đúng cổ phiếu đang chạy đúng kịch bản".
- alignment: lệnh có thuận với thesis + chiến lược không (ALIGNED / NEUTRAL / MISALIGNED).
- blocking_issues: 0-4 vấn đề PHẢI giải quyết trước khi vào lệnh. Rỗng khi proceed = true.
- resolution_steps: BẮT BUỘC 2-4 bước khi proceed = false, rỗng khi proceed = true.
  Mỗi bước: category (THESIS_CONFLICT / RISK_LIMIT / TIMING / MARKET_CONDITION /
  PORTFOLIO_BALANCE), issue (vấn đề cụ thể), resolution (điều kiện đo được để
  chuyển sang proceed = true), priority (BLOCKING / HIGH / MEDIUM / LOW).
  Ưu tiên điều kiện giá / khối lượng (quan sát được ngay) trước tin tức / vĩ mô.
  ✓ "VCB giữ trên 85,000 qua 2 phiên liên tiếp với khối lượng > TB20"
  ✗ "Chờ thị trường ổn định hơn"
- risk_summary: rủi ro của CHÍNH LỆNH NÀY (không phải rủi ro chung của cổ phiếu).
- sizing_note: để trống — hệ thống tự tính khối lượng bằng mô hình rủi ro cố định.
- summary: 2-3 câu, câu đầu nói thẳng NÊN hay KHÔNG NÊN thực hiện lệnh.

Quy tắc theo loại lệnh:
- SELL / REDUCE: nếu mục VỊ THẾ HIỆN TẠI báo không có vị thế → proceed = false,
  blocking_issues ghi "không có vị thế để bán". Đang lỗ không phải lý do giữ:
  nếu thesis vô hiệu hoặc chạm stop thì proceed = true dù đang lỗ.
- REDUCE: nhận xét tỷ trọng sau khi giảm còn hợp lý không (PORTFOLIO_BALANCE).
- HOLD: proceed = true nghĩa là tiếp tục giữ là hợp lý; false nghĩa là nên hành động.
- BUY khi đã có vị thế: đây là mua thêm — chỉ proceed = true nếu vị thế đang lãi
  hoặc có breakout mới; mua thêm để bình quân giá xuống → proceed = false.

Quy tắc thị trường chung (BẮT BUỘC — không đánh ngược xu hướng chung):
- Nếu THỊ TRƯỜNG CHUNG ở trạng thái RISK_OFF hoặc VOLATILE: lệnh BUY chỉ
  proceed = true khi cả thesis, tín hiệu scan và brief đều ủng hộ và không có xung
  đột nào. Không đạt → proceed = false, resolution_steps có bước MARKET_CONDITION
  nêu rõ "thị trường chung đang {state}, chưa đủ bằng chứng để đi ngược dòng".
- Trạng thái thị trường KHÔNG dùng để chặn SELL / REDUCE — cắt lỗ hoặc giảm vị thế
  không cần chờ thị trường thuận lợi.

Nếu được cung cấp PROFILE NHÀ ĐẦU TƯ:
- Ticker thuộc avoid list và lệnh là BUY → proceed = false, ghi vào blocking_issues.
- Lệnh vi phạm risk_appetite (drawdown tối đa, cỡ vị thế) → blocking_issues.
- Có dấu hiệu pattern thua lỗ đã biết → blocking_issues hoặc risk_summary, nói rõ.

Nếu được cung cấp LỊCH SỬ QUYẾT ĐỊNH: tham chiếu pattern cũ để cá nhân hoá —
pattern thua lỗ lặp lại → nêu rõ trong risk_summary; pattern đúng tương tự → có thể
tăng confidence và ghi trong confidence_explanation.
""" + schema_block(PreTradeCheckOutput)

SYSTEM_PROMPT = with_persona(_DOMAIN_RULES)

SPEC = PromptSpec(
    agent_name="PreTradeAgent",
    system_prompt=SYSTEM_PROMPT,
    output_schema=PreTradeCheckOutput,
    max_tokens=1200,
)


_ACTION_LABEL = {
    "BUY": "MUA (mở hoặc mua thêm vị thế)",
    "SELL": "BÁN (đóng toàn bộ vị thế)",
    "REDUCE": "GIẢM (bán một phần vị thế)",
    "HOLD": "GIỮ (không giao dịch, chỉ xác nhận tiếp tục nắm giữ)",
}


def build_pretrade_prompt(
    ticker: str,
    price: float,
    change_pct: float,
    thesis_context: str,
    signal_context: str,
    brief_context: str,
    past_lessons: str = "",
    investor_profile: str = "",
    market_context: str = "",
    ticker_context: str = "",
    intended_action: str = "BUY",
    quantity: int | None = None,
    portfolio_context: str = "",
) -> str:
    """Build pre-trade check prompt.

    Args:
        ticker: Ticker symbol (uppercase).
        price: Current price.
        change_pct: % change from previous session.
        thesis_context: Active thesis summary for this ticker.
        signal_context: Watchlist scan signal context.
        brief_context: Today's brief mention for this ticker.
        past_lessons: Optional recent evaluated decision history.
        investor_profile: Optional pre-rendered investor profile block from
            ContextBuilder.render_for_agent(). When provided, AI cross-checks
            the trade against risk_appetite, avoid list, and known patterns.
        market_context: Optional MarketRegime.format_for_prompt() string
            (VN-Index/VN30 state). When provided, AI raises the evidence bar
            for BUY when market state is RISK_OFF/VOLATILE (Wave 8.1 —
            never fight the general market).
        ticker_context: Optional market.TickerContext.format_for_prompt() line
            (MA20/50, RSI14, ATR14, Vol/TB20, 52w, trend, data quality) — Wave C1.
            Empty string skips the section.
        intended_action: Hành động nhà đầu tư sắp làm (BUY/SELL/REDUCE/HOLD) — P1.
            AI phải chép nguyên vào output và đánh giá proceed theo đúng lệnh này.
        quantity: Khối lượng dự kiến (cổ phiếu), None nếu chưa biết.
        portfolio_context: Dòng mô tả vị thế hiện tại của mã trong danh mục
            (thesis.PreTradeService dựng từ portfolio segment). Rỗng → ghi rõ
            "không có vị thế" để AI không đoán.
    """
    action = (intended_action or "BUY").upper().strip()
    action_line = _ACTION_LABEL.get(action, action)
    qty_line = f"Khối lượng dự kiến: {quantity:,} cp" if quantity else "Khối lượng dự kiến: chưa rõ"
    prompt = f"""\
Kiểm tra trước lệnh: **{ticker}**
Giá hiện tại: {price:,.0f} ({change_pct:+.2f}%)

=== LỆNH DỰ KIẾN ===
Hành động: {action} — {action_line}
{qty_line}

=== VỊ THẾ HIỆN TẠI ===
{portfolio_context or f"Không có vị thế {ticker} trong danh mục."}

=== LUẬN ĐIỂM ===
{thesis_context or "Không có thesis active cho mã này."}

=== TÍN HIỆU SCAN ===
{signal_context or "Không có scan signal gần đây cho mã này."}

=== BRIEF HÔM NAY ===
{brief_context or "Brief hôm nay không đề cập mã này."}
"""

    if ticker_context:
        prompt += f"""
=== BỐI CẢNH KỸ THUẬT ===
{ticker_context}
(Nếu data = stale/fallback: chỉ báo có thể thiếu hoặc cũ — không suy diễn thêm.)
"""

    if market_context:
        prompt += f"""
=== THỊ TRƯỜNG CHUNG ===
{market_context}
"""

    if investor_profile:
        prompt += f"""
=== PROFILE NHÀ ĐẦU TƯ ===
{investor_profile}
"""

    if past_lessons:
        prompt += f"""
=== LỊCH SỬ QUYẾT ĐỊNH ===
{past_lessons}
"""

    prompt += f"""
Trả về PreTradeCheckOutput JSON cho đúng lệnh {action} ở trên:
- intended_action = "{action}" (chép nguyên).
- Nói thẳng proceed_recommendation true hay false ngay câu đầu của summary.
- Nếu các nguồn (thesis / tín hiệu / brief / kỹ thuật / vị thế) mâu thuẫn nhau,
  nêu rõ nguồn nào nói gì trong risk_summary.
- proceed = false → blocking_issues và resolution_steps (2-4 bước, điều kiện đo được).
- proceed = true → blocking_issues = [] và resolution_steps = [].
"""

    return prompt
