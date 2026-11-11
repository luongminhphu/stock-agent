"""PlanExplainer — nút "Giải thích" của Kế hoạch nắm giữ.

Owner: ai segment. Chỉ chạy khi người dùng bấm; không chấm điểm, không đổi trạng thái kế
hoạch, không tạo số liệu mới. Nhiệm vụ duy nhất: diễn giải các con số ĐÃ TÍNH SẴN (đèn,
lý do, điều tôi tin, base rate) bằng lời dễ hiểu cho người không làm tài chính.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from src.ai.client import AIClient
from src.platform.logging import get_logger

logger = get_logger(__name__)

SYSTEM_PROMPT = """Bạn là người giải thích tài chính cho một nhà đầu tư cá nhân KHÔNG có nền tảng tài chính.
Bạn nhận một "kế hoạch nắm giữ" cổ phiếu đã được hệ thống đánh giá bằng số liệu (đèn trạng thái,
lý do, các "điều tôi tin" đúng/sai, khoảng cách tới Stop-loss/Target, và base rate của chính mã —
các lần trước mã ở trạng thái tương tự).

Quy tắc bắt buộc:
- CHỈ dùng số liệu được cung cấp. Không bịa thêm số, tin tức, dự báo giá hay nhận định thị trường.
- Không khuyên mua/bán. Chỉ giải thích tình trạng và các lựa chọn người dùng có thể cân nhắc.
- Viết tiếng Việt đơn giản, câu ngắn. Thuật ngữ tài chính quốc tế GIỮ NGUYÊN tiếng Anh, không dịch:
  Stop-loss, Target, Entry, P&L, Base rate, Win rate, Edge, Median return, Max drawdown, Margin, Hedge.
  Lần đầu dùng một thuật ngữ thì giải thích ngắn trong ngoặc, vd "Stop-loss (mức giá bạn chấp nhận là mình sai)".
- Win rate/base rate là thống kê quá khứ, không phải dự báo — nói rõ điều này khi nhắc tới.
- Số viết theo kiểu Việt Nam: 24.350đ, -14,3%.

Trả về JSON:
{"tom_tat": "1-2 câu tình trạng kế hoạch",
 "vi_sao": ["2-4 gạch đầu dòng giải thích vì sao đèn có màu này"],
 "dieu_can_xem": ["1-3 việc người dùng có thể tự kiểm tra/cân nhắc"],
 "luu_y": "1 câu nhắc giới hạn của số liệu"}"""


class PlanExplanation(BaseModel):
    tom_tat: str = ""
    vi_sao: list[str] = Field(default_factory=list)
    dieu_can_xem: list[str] = Field(default_factory=list)
    luu_y: str = ""


def build_user_prompt(plan: dict[str, Any], baserate: dict[str, Any] | None) -> str:
    payload = {"ke_hoach": plan, "tien_le": baserate or "không có"}
    return json.dumps(payload, ensure_ascii=False, default=str)


class PlanExplainer:
    def __init__(self, client: AIClient) -> None:
        self._client = client

    async def explain(
        self, plan: dict[str, Any], baserate: dict[str, Any] | None = None
    ) -> PlanExplanation:
        result = await self._client.chat(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=build_user_prompt(plan, baserate),
            response_schema=PlanExplanation,
            temperature=0.1,
            max_tokens=1200,
        )
        logger.info("plan_explainer.done", ticker=plan.get("ticker"))
        return result
