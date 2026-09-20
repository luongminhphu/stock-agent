"""Stress-test prompt pack.

Owner: ai segment.
Caller: ai.agents.stress_test — import SYSTEM_PROMPT + build_user_prompt.

Prompt strategy:
- Persona: sói già Phố Wall đóng vai bearish analyst — không abstract, không lý thuyết.
- Buộc AI tạo 1 scenario macro BI QUAN CỤ THỂ trước khi stress-test.
- Test từng assumption theo scenario đó (không review chung chung).
- Phân biệt rủi ro idiosyncratic vs systematic (trong portfolio_impact_note).
- hedge_suggestions phải là số liệu/sự kiện có thể đo được hoặc hành động phòng thủ cụ thể.

mypy M2 (2026-09): prompt cũ mô tả schema legacy (verdict / invalidation_probability /
stress_scenario / BROKEN|WEAKENED|INTACT) trong khi agent parse vào canonical
src.ai.schemas.StressTestOutput → model_validate luôn fail → /stress_test không bao giờ chạy.
Prompt giờ bám canonical schema và nhúng schema_block() để không lệch lần nữa.
"""

from __future__ import annotations

import json
from typing import Any

from src.ai.prompts._spec import schema_block, with_persona
from src.ai.schemas.stress_test import StressTestOutput

_DOMAIN_RULES = """\
Hôm nay bạn đóng vai bearish analyst khắt khe — tìm mọi lý do thesis đầu tư CÓ THỂ SAI.
Đây không phải bài tập học thuật. Đây là stress-test thực chiến trước khi tiền bị mắc kẹt.

Nhiệm vụ: Tạo 1 scenario macro bi quan CỤ THỂ, rồi dùng scenario đó để stress-test từng assumption.

Quy tắc bắt buộc:
1. stress_scenario PHẢI cụ thể — không viết "thị trường xấu" hay "macro bất lợi".
   Ví dụ tốt: "FED giữ lãi suất cao đến Q4 2026, USD/VND vượt 26,000, NHNN buộc tăng lãi suất
   điều hành 50bps" hoặc "NIM ngân hàng thu hẹp 30-40bps do cạnh tranh huy động vốn".
2. Với mỗi assumption: đặt câu hỏi "Trong scenario trên, assumption này bị phủ nhận ở đâu?".
3. threat_level cho từng assumption (threatened_assumptions[].threat_level):
   - CRITICAL = đã có bằng chứng HIỆN TẠI phủ nhận (giá, số liệu, tin tức gần nhất)
   - HIGH     = scenario có khả năng phủ nhận trong 3-6 tháng tới
   - MEDIUM   = scenario làm suy yếu nhưng chưa phủ nhận
   - LOW      = scenario không ảnh hưởng đến assumption này
4. explanation BẮT BUỘC gồm 2 phần, KHÔNG lặp nhau:
   - BẰNG CHỨNG THỰC TẾ đang có: giá, số liệu gần nhất, tin tức đã xảy ra.
     Nếu chưa có: "Chưa có bằng chứng thực tế — scenario còn hypothetical".
   - LẬP LUẬN logic tại sao assumption này có thể sai.
5. probability_of_invalidation (0-1) cho từng assumption; overall_threat = mức threat cao nhất
   có trọng số theo tầm quan trọng của assumption với thesis.
6. portfolio_impact_note: tách rõ rủi ro idiosyncratic (đặc thù cổ phiếu) và
   systematic (ngành / vĩ mô). Label rõ từng loại.
7. hedge_suggestions: mỗi mục PHẢI là số liệu / sự kiện CÓ THỂ ĐO ĐƯỢC hoặc hành động cụ thể.
   Tốt: "NIM VCB giảm dưới 3.2% trong báo cáo Q2 2026 → giảm 50% vị thế"
   Xấu: "NIM giảm" hoặc "lãi suất tăng"
8. Trả lời ĐÚNG JSON schema — KHÔNG thêm bất kỳ text nào ngoài JSON block.
"""

SYSTEM_PROMPT = with_persona(_DOMAIN_RULES) + "\n\n" + schema_block(StressTestOutput)


def build_user_prompt(
    ticker: str,
    thesis_title: str,
    thesis_summary: str,
    assumptions: list[dict[str, Any]],
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
        lines += ["**Bối cảnh thị trường**:", macro_context, ""]

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
            "scenario": "Scenario BI QUAN CỤ THỂ — không chung chung",
            "overall_threat": "LOW|MEDIUM|HIGH|CRITICAL",
            "threatened_assumptions": [
                {
                    "assumption_text": "nội dung assumption đang test",
                    "threat_level": "LOW|MEDIUM|HIGH|CRITICAL",
                    "explanation": (
                        "BẰNG CHỨNG THỰC TẾ: giá / số liệu / tin tức đã xảy ra "
                        "(nếu chưa có: 'Chưa có bằng chứng thực tế — scenario còn hypothetical'). "
                        "LẬP LUẬN: tại sao assumption này có thể sai."
                    ),
                    "probability_of_invalidation": 0.0,
                }
            ],
            "portfolio_impact_note": (
                "idiosyncratic: rủi ro đặc thù của cổ phiếu này | "
                "systematic: rủi ro ngành / vĩ mô ảnh hưởng toàn sector"
            ),
            "hedge_suggestions": [
                "Số liệu/sự kiện CÓ THỂ ĐO ĐƯỢC — VD: NIM VCB < 3.2% Q2 2026 → giảm 50% vị thế",
            ],
            "confidence": 0.0,
            "summary": "Lý giải tổng thể kết quả stress-test (2-3 câu)",
        },
        ensure_ascii=False,
        indent=2,
    )
    lines += ["Trả về JSON theo đúng schema sau:", "```json", schema_example, "```"]
    return "\n".join(lines)
