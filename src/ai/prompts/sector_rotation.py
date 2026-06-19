"""Prompt pack cho SectorRotationAgent.

Tách prompt ra khỏi agent để dễ iterate và test độc lập.
Owner: ai segment.
"""

from __future__ import annotations

from src.ai.prompts._spec import PromptSpec
from src.ai.schemas import SectorFlow, SectorRotationOutput

# NOTE: schema_block không dùng ở đây vì prompt đã embed inline JSON example
# (schema đủ chi tiết để model follow, thêm schema_block sẽ gây trùng lặp).
# Nếu SectorRotationOutput thay đổi cấu trúc đáng kể, hãy cập nhật inline JSON dưới đây.
SYSTEM_PROMPT = (
    "Bạn là chuyên gia phân tích dòng tiền thị trường chứng khoán Việt Nam.\n"
    "Nhiệm vụ: phân tích dữ liệu sector được cung cấp và xác định xu hướng rotation hôm nay."
)

SPEC = PromptSpec(
    agent_name="SectorRotationAgent",
    system_prompt=SYSTEM_PROMPT,
    output_schema=SectorRotationOutput,
    max_tokens=900,
)


def build_sector_rotation_prompt(
    sector_flows: list[SectorFlow],
    snapshot_date: str,
    watchlist_tickers: list[str] | None = None,
) -> str:
    """Xây dựng prompt phân tích sector rotation.

    Args:
        sector_flows: Dữ liệu giá đã aggregate theo sector.
        snapshot_date: Ngày dữ liệu (YYYY-MM-DD).
        watchlist_tickers: Danh sách tickers watchlist của user.

    Returns:
        Prompt string ready to send to AI client.
    """
    sector_lines = []
    for sf in sector_flows:
        direction_emoji = {"INFLOW": "⬆️", "OUTFLOW": "⬇️", "NEUTRAL": "➡️"}.get(
            sf.flow_direction, ""
        )
        movers = ", ".join(sf.top_movers) if sf.top_movers else "N/A"
        sector_lines.append(
            f"- {sf.sector}: avg {sf.avg_change_pct_1d:+.2f}% {direction_emoji} "
            f"| top movers: {movers} | n={sf.ticker_count}"
        )

    sector_block = "\n".join(sector_lines) if sector_lines else "(không có dữ liệu sector)"

    watchlist_block = ""
    if watchlist_tickers:
        watchlist_block = (
            f"\n\n## Watchlist của nhà đầu tư\n"
            f"{', '.join(watchlist_tickers)}\n"
            f"Hãy kiểm tra xem có ticker nào đang diverge khỏi sector của nó không (contrarian signal)."
        )

    return f"""Bạn là chuyên gia phân tích dòng tiền thị trường chứng khoán Việt Nam.
Nhiệm vụ: phân tích dữ liệu sector được cung cấp và xác định xu hướng rotation hôm nay.

## Dữ liệu sector ngày {snapshot_date}
{sector_block}{watchlist_block}

## Yêu cầu output
Trả lời STRICT JSON theo schema SectorRotationOutput:
{{
  "snapshot_date": "{snapshot_date}",
  "rotation_narrative": "2-3 câu mô tả dòng tiền đang chảy đi đâu và tại sao",
  "risk_regime": "RISK_ON | RISK_OFF | MIXED",
  "leading_sectors": [
    {{
      "sector": "Tên sector",
      "avg_change_pct_1d": 0.0,
      "flow_direction": "INFLOW",
      "top_movers": ["VCB", "BID"],
      "ticker_count": 5
    }}
  ],
  "lagging_sectors": [ /* tương tự, top 3 OUTFLOW */ ],
  "watchlist_crosscheck": [
    {{
      "ticker": "VCB",
      "sector": "Banking",
      "ticker_change_pct": -1.2,
      "sector_avg_change_pct": 0.5,
      "is_contrarian": true,
      "note": "VCB -1.2% trong khi Banking +0.5%"
    }}
  ],
  "actionable_insight": "1 insight cụ thể nhất cho user dựa trên watchlist",
  "confidence": 0.75
}}

Chỉ trả JSON thuần, không giải thích thêm."""
