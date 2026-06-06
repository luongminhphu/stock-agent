"""ProactiveDiscoveryAgent — portfolio-aware stock discovery.

Owner: ai segment.
Caller: market.ProactiveDiscoveryService

Responsibility:
  - Nhận portfolio context (holdings, sector exposure, P&L) + market screen candidates.
  - Dùng AI để xác định mã nào phù hợp thêm vào / cần tránh, dựa trên danh mục thực.
  - Trả về ProactiveDiscoveryOutput: ranked picks với reasoning gắn portfolio cụ thể.

Boundary:
  - KHÔNG đọc DB trực tiếp — tất cả context được inject bởi ProactiveDiscoveryService.
  - KHÔNG emit event — caller owns event publish.
  - KHÔNG import bot / scheduler / discord.
  - Trả về None on any failure — caller degrades gracefully.

Output contract (AI JSON):
  {
    "picks": [
      {
        "ticker": "VCB",
        "action": "BUY_WATCH",
        "verdict": "...",
        "entry_logic": "...",
        "portfolio_fit": "...",
        "upside_catalyst": "...",
        "invalidation_condition": "...",
        "confidence": 0.75,
        "signal_basis": "BREAKOUT+MOMENTUM"
      }
    ],
    "portfolio_gaps": ["TECHNOLOGY", "ENERGY"],
    "market_regime_note": "...",
    "avoid_tickers": ["HPG", "MSN"]
  }
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from src.platform.logging import get_logger

logger = get_logger(__name__)

# ── Output schema (dataclass — no Pydantic dep needed here) ─────────────────

@dataclass
class DiscoveryPick:
    ticker: str
    action: str                         # BUY_WATCH | ACCUMULATE | AVOID
    verdict: str                        # 1 câu — tại sao relevant với portfolio
    entry_logic: str                    # điều kiện vào lệnh cụ thể
    portfolio_fit: str                  # bổ sung hay duplicate exposure?
    upside_catalyst: str
    invalidation_condition: str
    confidence: float                   # 0.0–1.0
    signal_basis: str                   # "BREAKOUT" | "MOMENTUM" | "REVERSAL_WATCH" | ...


@dataclass
class ProactiveDiscoveryOutput:
    picks: list[DiscoveryPick] = field(default_factory=list)
    portfolio_gaps: list[str] = field(default_factory=list)
    market_regime_note: str = ""
    avoid_tickers: list[str] = field(default_factory=list)


# ── System prompt ────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
Bạn là một chuyên gia phân tích đầu tư chứng khoán Việt Nam (HOSE/HNX/UPCoM) với tư duy
portfolio manager — không phải analyst viết báo cáo chung chung.

Nhiệm vụ: dựa trên danh mục hiện có của nhà đầu tư và danh sách mã vừa vượt qua market screen
hôm nay, hãy xác định MÃ NÀO phù hợp nhất để thêm vào hoặc cần tránh — với lý do GẮN LIỀN
với portfolio cụ thể này, không phải nhận xét chung về mã đó.

Nguyên tắc bắt buộc:
1. action phải là một trong: "BUY_WATCH" | "ACCUMULATE" | "AVOID".
   - BUY_WATCH: mã mới, không có trong danh mục — nên theo dõi để vào lệnh.
   - ACCUMULATE: mã đã có trong danh mục — tín hiệu tốt để tăng vị thế.
   - AVOID: có signal nhưng không phù hợp với profile hoặc tăng risk concentration.
2. portfolio_fit phải nêu rõ: mã này bổ sung sector/exposure chưa có, hay duplicate
   exposure đã quá nặng?
3. verdict là 1 câu súc tích, cụ thể — có ticker, có lý do gắn portfolio.
4. entry_logic phải actionable: giá vào, điều kiện kỹ thuật, hoặc catalyst chờ.
5. invalidation_condition: khi nào thesis ngắn hạn này sai — cụ thể, không chung chung.
6. picks tối đa 5, sắp xếp theo confidence DESC.
7. avoid_tickers: chỉ list mã trong candidates mà nên tránh hôm nay — không thêm mã khác.
8. portfolio_gaps: list sector (BANKING/REAL_ESTATE/TECHNOLOGY/ENERGY/MATERIALS/CONSUMER_GOODS/...)
   đang thiếu hoặc under-represented so với market breadth hiện tại.
9. market_regime_note: 1 câu mô tả tone thị trường hôm nay (bullish/bearish/mixed/range-bound).
10. Trả về JSON hợp lệ, không có markdown.

Schema output bắt buộc:
{
  "picks": [
    {
      "ticker": "string",
      "action": "BUY_WATCH|ACCUMULATE|AVOID",
      "verdict": "string — 1 câu cụ thể",
      "entry_logic": "string",
      "portfolio_fit": "string",
      "upside_catalyst": "string",
      "invalidation_condition": "string",
      "confidence": float 0.0-1.0,
      "signal_basis": "string — criteria từ screen"
    }
  ],
  "portfolio_gaps": ["SECTOR_NAME", ...],
  "market_regime_note": "string",
  "avoid_tickers": ["TICKER", ...]
}
"""


def _build_user_prompt(
    candidates_block: str,
    portfolio_block: str,
    trading_date: str,
) -> str:
    return f"""\
=== NGÀY GIAO DỊCH ===
{trading_date}

=== DANH MỤC HIỆN TẠI CỦA NHÀ ĐẦU TƯ ===
{portfolio_block}

=== MÃ VƯỢT MARKET SCREEN HÔM NAY (ranked by composite score) ===
{candidates_block}

Hãy phân tích và trả về JSON theo schema đã định nghĩa.
Ưu tiên mã bổ sung được diversification và phù hợp với momentum hiện tại của danh mục.
"""


def _parse_output(raw: str) -> dict[str, Any]:
    """Parse AI JSON. Returns empty dict on failure — caller uses fallback."""
    text = raw.strip()
    # Strip markdown fences
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"```\s*$", "", text, flags=re.MULTILINE)
    try:
        return json.loads(text.strip())
    except Exception:
        return {}


def _build_output(data: dict[str, Any]) -> ProactiveDiscoveryOutput:
    """Map raw dict → typed output. Graceful on missing/malformed keys."""
    picks: list[DiscoveryPick] = []
    for item in data.get("picks", []):
        if not isinstance(item, dict) or not item.get("ticker"):
            continue
        try:
            picks.append(DiscoveryPick(
                ticker=str(item.get("ticker", "")).upper(),
                action=str(item.get("action", "BUY_WATCH")),
                verdict=str(item.get("verdict", "")),
                entry_logic=str(item.get("entry_logic", "")),
                portfolio_fit=str(item.get("portfolio_fit", "")),
                upside_catalyst=str(item.get("upside_catalyst", "")),
                invalidation_condition=str(item.get("invalidation_condition", "")),
                confidence=float(item.get("confidence", 0.5)),
                signal_basis=str(item.get("signal_basis", "")),
            ))
        except Exception:
            continue

    return ProactiveDiscoveryOutput(
        picks=picks[:5],
        portfolio_gaps=[str(s) for s in data.get("portfolio_gaps", [])],
        market_regime_note=str(data.get("market_regime_note", "")),
        avoid_tickers=[str(t).upper() for t in data.get("avoid_tickers", [])],
    )


# ── Agent ────────────────────────────────────────────────────────────────────

class ProactiveDiscoveryAgent:
    """Stateless agent — all context injected per call."""

    def __init__(self, ai_client: Any) -> None:
        self._client = ai_client

    async def analyze(
        self,
        candidates_block: str,
        portfolio_block: str,
        trading_date: str,
    ) -> ProactiveDiscoveryOutput | None:
        """Run AI analysis. Returns None on any failure — caller handles gracefully.

        Args:
            candidates_block: Pre-formatted string of screen candidates (one per line).
            portfolio_block:  Pre-formatted string of portfolio holdings + sector exposure.
            trading_date:     YYYY-MM-DD string.
        """
        if not candidates_block or candidates_block.strip() == "(none)":
            logger.debug("proactive_discovery_agent.no_candidates_skip")
            return None

        try:
            json_instruction = (
                "You MUST respond with valid JSON only. "
                "No markdown, no code fences, no explanation outside JSON."
            )
            messages = [
                {"role": "system", "content": f"{json_instruction}\n\n{_SYSTEM_PROMPT}"},
                {"role": "user", "content": _build_user_prompt(
                    candidates_block=candidates_block,
                    portfolio_block=portfolio_block,
                    trading_date=trading_date,
                )},
            ]
            response = await self._client.chat_completion(
                messages=messages,
                temperature=0.2,
            )
            raw = self._client.extract_text(response)
            data = _parse_output(raw)

            if not data:
                logger.warning("proactive_discovery_agent.parse_failed", raw_preview=raw[:200])
                return None

            output = _build_output(data)
            logger.info(
                "proactive_discovery_agent.ok",
                picks_count=len(output.picks),
                portfolio_gaps=output.portfolio_gaps,
                avoid_count=len(output.avoid_tickers),
                trading_date=trading_date,
            )
            return output

        except Exception as exc:
            logger.warning(
                "proactive_discovery_agent.failed",
                error=str(exc),
                trading_date=trading_date,
            )
            return None
