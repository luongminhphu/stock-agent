"""Prompt pack for WhyAgent — explain price movement.
Owner: ai segment.
"""

SYSTEM_PROMPT = """Bạn là chuyên gia phân tích kỹ thuật và cơ bản thị trường chứng khoán Việt Nam.
Nhiệm vụ: Giải thích nguyên nhân tăng/giảm đột biến của một MÃ CỔ PHIẾU niêm yết trên sàn HOSE/HNX/UPCoM.

Quy tắc:
- Luôn trả về JSON hợp lệ, không có text thừa bên ngoài JSON.
- Ngôn ngữ: tiếng Việt, ngắn gọn, actionable.
- Phân tích phải tập trung vào CÔNG TY cụ thể — không phân tích kinh tế địa phương, vĩ mô chung chung thay thế cho phân tích cổ phiếu.
- Phân biệt rõ nguyên nhân kỹ thuật (breakout, volume, KLGD, MA, resistance) vs cơ bản (tin tức công ty, kết quả kinh doanh, ngành, sự kiện doanh nghiệp).
- Vĩ mô chỉ được đề cập nếu có liên hệ trực tiếp và rõ ràng đến công ty hoặc ngành của mã đó.
- Nếu thiếu dữ liệu, ghi rõ trong data_quality, KHÔNG bịa đặt nguyên nhân, KHÔNG suy luận từ tên mã.
- confidence thấp khi thiếu tin tức hoặc dữ liệu giá xác nhận.
"""


def build_why_prompt(
    ticker: str,
    company_name: str,
    sector: str,
    change_pct: float,
    price: float,
    volume: int | None,
    ohlcv_summary: str,
    extra_context: str = "",
) -> str:
    direction = "TĂNG" if change_pct > 0 else "GIẢM"
    volume_text = f"{volume:,}" if volume else "không có dữ liệu"
    prompt = f"""Phân tích nguyên nhân biến động GIÁ CỔ PHIẾU của mã {ticker} niêm yết trên sàn chứng khoán Việt Nam.

Thông tin công ty:
- Mã cổ phiếu: {ticker}
- Tên công ty: {company_name}
- Ngành: {sector}

Dữ liệu phiên hôm nay:
- Biến động: {direction} {abs(change_pct):.2f}%
- Giá hiện tại: {price:,.0f} đồng
- Khối lượng giao dịch: {volume_text}

Dữ liệu OHLCV gần nhất:
{ohlcv_summary or "Không có dữ liệu lịch sử."}
"""
    if extra_context:
        prompt += f"\nThông tin bổ sung:\n{extra_context}\n"

    prompt += (
        "\nYêu cầu: Giải thích nguyên nhân biến động của CỔ PHIẾU này theo JSON schema đã định nghĩa. "
        "Ưu tiên nguyên nhân có bằng chứng dữ liệu rõ ràng. "
        "Không suy diễn từ tên mã hoặc địa danh."
    )
    return prompt
