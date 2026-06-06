"""
Prompt pack cho IntelligenceVerdictAgent.

Owner: ai segment. Zero business logic here.
Convention: module-level SPEC + build_user_prompt() function.

SPEC declares system_prompt, output_schema, temperature, max_tokens.
Agent calls client.structured_call(spec=SPEC, user_prompt=build_user_prompt(...)).

Note: VerdictOutput is imported from src.ai.schemas (NOT from agents) to
avoide the circular import: agents -> prompts -> agents.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from src.ai.client import AIClient, AISpec
from src.ai.prompts._spec import schema_block, with_persona
from src.ai.schemas import VerdictOutput  # breaks the circular import

if TYPE_CHECKING:
    from src.core.schemas import RankedSignal, SystemSnapshot


_DOMAIN_RULES = f"""\
Nhiệm vụ: tổng hợp tín hiệu đa nguồn từ hệ thống stock-agent thành một verdict \
hành động rõ ràng cho nhà đầu tư. Bạn là lớp tổng hợp cuối — không giải thích lại data,
nhưng phân tích ý nghĩa của data và đưa ra lệnh.

Quy tắc bắt buộc:
1. Chỉ trả về JSON hợp lệ theo schema VerdictOutput. Không thêm text ngoài JSON.
2. verdict phải là một trong: BUY_SIGNAL | SELL_SIGNAL | HOLD | REVIEW_THESIS | RISK_ALERT | NO_ACTION
3. conviction bắt buộc: "high" khi ≥3 tín hiệu hội tụ và data rõ. "low" khi suy luận là chủ yếu.
4. time_horizon bắt buộc: xác định dựa trên loại signal (kỹ thuật=intraday/swing, cơ bản=position/core).
5. thesis_alignment: 0.5 nếu chưa có thesis. Không inflate.
6. key_risk: gọi tên rủi ro cụ thể nhất từ data — không viết "rủi ro thị trường" chung chung.
7. invalidation_trigger: bắt đầu bằng "Verdict này sai khi..." + ngưỡng cụ thể (đừng để mơ hồ).
8. action: câu lệnh động từ đầu, tiếng Việt, nghiêm túc, có giá/vBjthế cụ thể nếu BUY/SELL.
9. RISK_ALERT ưu tiên hơn BUY_SIGNAL khi có tín hiệu rủi ro rõ từ portfolio hoặc thesis.
10. REVIEW_THESIS khi thesis có dấu hiệu drift hoặc invalidation, dù giá chưa phá ngưỡng.
11. reasoning_summary: dẫn dữ liệu cụ thể (đừng nói chung chung), giải thích tại sao chọn verdict này
    thay vì alternative gần nhất.

Thứ tự ưu tiên khi xung đột: an toàn vốn > thesis integrity > opportunity capture.

{schema_block(VerdictOutput)}
"""

_SYSTEM = with_persona(_DOMAIN_RULES)


SPEC = AISpec(
    system_prompt=_SYSTEM,
    output_schema=VerdictOutput,
    temperature=0.2,
    max_tokens=AIClient.DEFAULT_MAX_TOKENS,
)


def build_user_prompt(
    snapshot: "SystemSnapshot",
    ranked_signals: "list[RankedSignal]",
    investor_context: str = "",
) -> str:
    """Serialize SystemSnapshot + ranked signals into a structured prompt string."""

    signals_block = "\n".join(
        f"  [{i + 1}] [{s.source.upper()}] {s.description} (urgency={s.urgency_score:.2f})"
        for i, s in enumerate(ranked_signals)
    ) or "  (không có signal)"

    pf = snapshot.portfolio
    portfolio_block = (
        f"  Vị thế: {pf.total_positions} | "
        f"Risk breach: {pf.risk_breach_count} | "
        f"Unrealized PnL: {f'{pf.unrealized_pnl_pct:.1f}%' if pf.unrealized_pnl_pct is not None else 'N/A'}"
    )

    th = snapshot.thesis
    thesis_block = (
        f"  Stale: {th.stale_count} | "
        f"Drift: {th.drift_detected_count} | "
        f"Invalidated: {th.invalidated_count}"
    )

    wl = snapshot.watchlist
    watchlist_block = (
        f"  Alerts triggered: {wl.triggered_alert_count} | "
        f"Top tickers: {', '.join(wl.top_tickers[:5]) or 'none'} | "
        f"Volume spike: {'yes' if wl.has_volume_spike else 'no'}"
    )

    mk = snapshot.market
    market_block = (
        f"  Phase: {mk.market_phase} | "
        f"Trend shifts: {mk.trend_shift_count} | "
        f"Opportunities: {mk.opportunity_count}"
    )

    signal_engine_note = (
        f"\nSignal Engine Summary:\n  {snapshot.signal_engine_summary}"
        if snapshot.signal_engine_summary
        else ""
    )

    investor_block = (
        f"## Investor Context\n{investor_context}\n\n"
        if investor_context
        else ""
    )

    return investor_block + f"""## System Snapshot — {snapshot.captured_at.strftime('%Y-%m-%d %H:%M')} ICT
Trigger: {snapshot.trigger_source}

### Ranked Signals (urgency cao → thấp):
{signals_block}

### Portfolio Context:
{portfolio_block}

### Thesis Context:
{thesis_block}

### Watchlist Context:
{watchlist_block}

### Market Context:
{market_block}{signal_engine_note}

---
Tổng hợp toàn bộ context trên và trả về verdict JSON theo schema VerdictOutput.
Nhớ: bạn là nhà đầu tư kỳ cựu — nói thẳng, không hedge, đi kèm invalidation_trigger cụ thể.
"""
