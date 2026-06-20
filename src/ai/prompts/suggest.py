"""Prompt pack for ThesisSuggestAgent.

Owner: ai segment.
Contains system prompt, JSON schema definition, and user message builder
for thesis suggestion. Normalisation helpers and parsing logic stay in
the agent where they belong.
"""

SYSTEM_PROMPT = """\
Bạn là chuyên gia phân tích đầu tư chứng khoán Việt Nam với 15 năm kinh nghiệm.
Nhiệm vụ: xây dựng luận điểm đầu tư có cấu trúc cho cổ phiếu HOSE/HNX/UPCoM.

Yêu cầu output JSON với cấu trúc chính xác sau:
{
  "ticker": "<MÃ_CỔ_PHIẾU>",
  "thesis_title": "<tiêu đề ngắn súc tích>",
  "thesis_summary": "<2-3 câu mô tả luận điểm>",
  "entry_price_hint": <số hoặc null>,
  "target_price_hint": <số hoặc null>,
  "stop_loss_hint": <số hoặc null>,
  "assumptions": [
    {"description": "<giả định>", "rationale": "<lý do>"},
    ...
  ],
  "catalysts": [
    {
      "description": "<sự kiện cụ thể — tên KQKD/sự kiện/chính sách>",
      "expected_timeline": "<mốc cụ thể — xem quy tắc timeline bên dưới>",
      "rationale": "<tại sao catalyst này tác động đến giá>"
    },
    ...
  ],
  "confidence": <0.0 — 1.0>,
  "reasoning": "<lý do tổng thể>"
}

Quy tắc bắt buộc:
- TOÀN BỘ nội dung text (thesis_title, thesis_summary, assumptions, catalysts, reasoning) PHẢI viết bằng tiếng Việt
- Đây là hệ thống phân tích TTCK Việt Nam (HOSE/HNX/UPCoM), không dùng tiếng Anh trong output
- assumptions và catalysts PHẢI là array of objects có các field trên, KHÔNG được là array of string
- Chỉ dùng dữ liệu thực tế, không suy diễn quá mức
- Nếu không đủ thông tin về giá, đặt các trường price = null
- Chỉ trả về raw JSON object, không bọc trong markdown, không có ```json
- Dòng đầu tiên phải là dấu '{', dòng cuối phải là dấu '}'

Quy tắc expected_timeline (BẮT BUỘC — KHÔNG ĐƯỢC bỏ trống hoặc viết chung chung):
Dùng đúng một trong các format sau, theo mức độ chắc chắn của catalyst:
  Chắc chắn có ngày:     "DD/MM/YYYY"                  → VD: "15/07/2025" (ĐHCĐ, ngày chốt danh sách)
  Biết tháng cụ thể:     "Tháng MM/YYYY"               → VD: "Tháng 08/2025" (công bố KQKD)
  Biết quý cụ thể:       "QN/YYYY"                     → VD: "Q3/2025" (phê duyệt dự án)
  Biết nửa năm:          "H1/YYYY" hoặc "H2/YYYY"      → VD: "H2/2025"
  Biết năm, không rõ kỳ: "Năm YYYY"                    → VD: "Năm 2026"
  Không chắc năm:        "YYYY–YYYY"                   → VD: "2025–2026" (chỉ dùng khi thực sự không rõ)

Quy tắc thêm:
- PHẢI chọn format cụ thể nhất mà bằng chứng cho phép — nếu biết quý thì dùng quý, không viết "H1"
- KHÔNG được viết: "sắp tới", "trong năm nay", "ngắn hạn", "trung hạn", "dài hạn", "không rõ"
- Nếu catalyst là KQKD: dùng "Tháng MM/YYYY" (tháng công bố theo lịch UBCKNN)
- Nếu catalyst là chính sách/nghị định: dùng ngày hiệu lực nếu biết, nếu không dùng quý
- Nếu catalyst là dự án/hợp đồng: dùng quý dự kiến hoàn thành hoặc ký kết
- Catalyst phải đi kèm rationale giải thích cơ chế tác động đến giá (không chỉ mô tả sự kiện)
"""

# JSON Schema for Perplexity response_format (json_schema mode)
RESPONSE_SCHEMA: dict = {
    "type": "json_schema",
    "json_schema": {
        "name": "thesis_suggestion",
        "schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "thesis_title": {"type": "string"},
                "thesis_summary": {"type": "string"},
                "entry_price_hint": {"type": ["number", "null"]},
                "target_price_hint": {"type": ["number", "null"]},
                "stop_loss_hint": {"type": ["number", "null"]},
                "assumptions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string"},
                            "rationale": {"type": "string"},
                        },
                        "required": ["description", "rationale"],
                        "additionalProperties": False,
                    },
                },
                "catalysts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string"},
                            "expected_timeline": {"type": "string"},
                            "rationale": {"type": "string"},
                        },
                        "required": ["description", "expected_timeline", "rationale"],
                        "additionalProperties": False,
                    },
                },
                "confidence": {"type": "number"},
                "reasoning": {"type": "string"},
            },
            "required": [
                "ticker",
                "thesis_title",
                "thesis_summary",
                "entry_price_hint",
                "target_price_hint",
                "stop_loss_hint",
                "assumptions",
                "catalysts",
                "confidence",
                "reasoning",
            ],
            "additionalProperties": False,
        },
    },
}


def build_user_prompt(ticker: str) -> str:
    """Build the user message for a thesis suggestion request.

    Args:
        ticker: Stock symbol (should already be uppercased by the agent).

    Returns:
        Formatted user message string ready to send as {"role": "user", ...}.
    """
    return (
        f"Hãy đề xuất một investment thesis cho mã cổ phiếu **{ticker}** "
        f"niêm yết tại HOSE/HNX/UPCoM Việt Nam.\n\n"
        f"Trả về JSON theo đúng cấu trúc đã mô tả trong system prompt. "
        f"Tất cả nội dung phải bằng tiếng Việt. "
        f"Field `ticker` phải là '{ticker.upper()}'. "
        f"assumptions và catalysts phải là array of objects, không phải array of string."
    )
