"""Stress-test prompt pack.

Owner: ai segment.
Caller: ai.agents.stress_test — import SYSTEM_PROMPT + build_user_prompt.

Prompt strategy:
- Buộc AI tạo 1 scenario macro BI QUAN CỤ THỂ trước khi stress-test.
- Test từng assumption theo scenario đó (không review chung chung).
- Phân biệt rủi ro idiosyncratic vs systematic.
- suggested_triggers_to_watch phải là số liệu/sự kiện có thể đo được.
"""

from __future__ import annotations

import json

SYSTEM_PROMPT = """\
Bạn là một bearish analyst khắt khe, chuyên tìm lý do thesis đầu tư chứng khoán Việt Nam CÓ THỂ SAI.

Nhiệm vụ: Tạo 1 scenario macro bi quan CỤ THỂ, rồi dùng scenario đó để stress-test từng assumption.

Quy tắc bắt buộc:
1. stress_scenario PHẢI cụ thể — không viết "thị trường xấu" hay "macro bất lợi".
   Ví dụ tốt: "FED giữ lãi suất cao đến Q4 2026, USD/VND vượt 26,000, NHNN buộc tăng lãi suất
   điều hành 50bps" hoặc "NIM ngân hàng thu hẹp 30-40bps do cạnh tranh huy động vốn".
2. Với mỗi assumption: đặt câu hỏi "Trong scenario trên, assumption này bị phủ nhận ở đâu?".
3. threat_level:
   - BROKEN   = đã có bằng chứng HIỆN TẠI phủ nhận (giá, số liệu, tin tức gần nhất)
   - WEAKENED = scenario có khả năng phủ nhận trong 3-6 tháng tới
   - INTACT   = scenario không ảnh hưởng đến assumption này
4. macro_risks: liệt kê riêng rủi ro idiosyncratic (đặc thù cổ phiếu) và
   systematic (ngành / vĩ mô). Label rõ từng loại.
5. suggested_triggers_to_watch: PHẢI là số liệu / sự kiện CÓ THỂ ĐO ĐƯỢC.
   Tốt: "NIM VCB giảm dưới 3.2% trong báo cáo Q2 2026"
   Xấu: "NIM giảm" hoặc "lãi suất tăng"
6. invalidation_probability = (số BROKEN + 0.5 × số WEAKENED) / tổng số assumptions.
7. Trả lời ĐÚNG JSON schema — KHÔNG thêm bất kỳ text nào ngoài JSON block.
"""


def build_user_prompt(
    ticker: str,
    thesis_title: str,
    thesis_summary: str,
    assumptions: list[dict],
    catalysts: list[str],
    current_price: float | None,
    entry_price: float | None,
    target_price: float | None,
    stop_loss: float | None,
    macro_context: str,
) -> str:
    """Build adversarial stress-test prompt.

    Args:
        assumptions: list of dicts with keys: id, description, status.
        catalysts:   pending catalyst descriptions.
        macro_context: pre-built string with current price + sector context.
    """
    lines = [
        f"## Stress-Test: {ticker}",
        f"**Thesis**: {thesis_title}",
        f"**Tóm tắt**: {thesis_summary}",
        "",
    ]

    price_parts: list[str] = []
    if current_price:
        price_parts.append(f"Hiện tại: {current_price:,.0f}")
    if entry_price:
        price_parts.append(f"Vào: {entry_price:,.0f}")
    if target_price:
        price_parts.append(f"Target: {target_price:,.0f}")
    if stop_loss:
        price_parts.append(f"Stop: {stop_loss:,.0f}")
        if current_price and stop_loss:
            pct_to_stop = (current_price - stop_loss) / current_price * 100
            price_parts.append(f"({pct_to_stop:+.1f}% đến stop)")
    if price_parts:
        lines.append("**Giá**: " + " | ".join(price_parts))
        lines.append("")

    if macro_context:
        lines += ["**Market context**:", macro_context, ""]

    if assumptions:
        lines.append(
            f"**Assumptions cần stress-test** ({len(assumptions)} total — hãy test TẤT CẢ):"
        )
        for a in assumptions:
            status_tag = f"[{a.get('status', 'valid').upper()}]"
            lines.append(f"- [ID:{a.get('id', 0)}] {status_tag} {a.get('description', '')}")
        lines.append("")

    if catalysts:
        lines.append(
            "**Catalysts đang pending** "
            "(liệu scenario bi quan có hủy / trì hoãn các catalyst này?):"
        )
        for c in catalysts:
            lines.append(f"- {c}")
        lines.append("")

    schema_example = json.dumps(
        {
            "ticker": ticker,
            "thesis_title": thesis_title,
            "verdict": "BEARISH|NEUTRAL|BULLISH",
            "invalidation_probability": 0.0,
            "confidence": 0.0,
            "stress_scenario": "Scenario BI QUAN CỤ THỂ — không chung chung",
            "threatened_assumptions": [
                {
                    "assumption_id": 0,
                    "description": "...",
                    "threat_level": "BROKEN|WEAKENED|INTACT",
                    "evidence": "Bằng chứng cụ thể hiện tại (giá / số liệu / tin tức)",
                    "counter_argument": "Lý do mạnh nhất để phủ nhận assumption này",
                }
            ],
            "surviving_assumptions": ["assumption vẫn INTACT trong scenario trên..."],
            "macro_risks": [
                "idiosyncratic: rủi ro đặc thù của cổ phiếu này",
                "systematic: rủi ro ngành / vĩ mô ảnh hưởng toàn sector",
            ],
            "suggested_triggers_to_watch": [
                "Số liệu/sự kiện CÓ THỂ ĐO ĐƯỢC — VD: NIM VCB < 3.2% Q2 2026",
            ],
            "reasoning": "Lý giải tổng thể kết quả stress-test",
        },
        ensure_ascii=False,
        indent=2,
    )
    lines += ["Trả về JSON theo đúng schema sau:", "```json", schema_example, "```"]
    return "\n".join(lines)
