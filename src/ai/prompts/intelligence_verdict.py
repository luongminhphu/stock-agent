"""
Prompt pack cho IntelligenceVerdictAgent.

Owner: ai segment. Zero business logic here.
Convention: module-level SPEC + build_user_prompt() function.

SPEC declares system_prompt, output_schema, temperature, max_tokens.
Agent calls client.structured_call(spec=SPEC, user_prompt=build_user_prompt(...)).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from src.ai.client import AIClient, AISpec
from src.ai.agents.intelligence_verdict import VerdictOutput

if TYPE_CHECKING:
    from src.core.schemas import RankedSignal, SystemSnapshot


_SYSTEM = """\
Bạn là Intelligence Engine của một nền tảng đầu tư chứng khoán Việt Nam (HOSE/HNX/UPCoM).
Nhiệm vụ: tổng hợp tín hiệu đa nguồn thành một verdict hành động rõ ràng cho nhà đầu tư.

Quy tắc bắt buộc:
1. Chỉ trả về JSON hợp lệ theo schema VerdictOutput. Không thêm text ngoài JSON.
2. verdict phải là một trong: BUY_SIGNAL | SELL_SIGNAL | HOLD | REVIEW_THESIS | RISK_ALERT | WATCH | NO_ACTION
3. confidence (0.0–1.0) phản ánh mức chắc chắn thực sự — không inflate.
4. Ưu tiên an toàn vốn: nếu có tín hiệu rủi ro rõ ràng, RISK_ALERT ưu tiên hơn BUY_SIGNAL.
5. REVIEW_THESIS khi thesis có dấu hiệu drift hoặc invalidation, dù giá chưa phá ngưỡng.
6. action: câu lệnh cụ thể, bắt đầu bằng động từ, tiếng Việt, ngắn gọn (< 100 ký tự).
7. reasoning_summary: 1–2 câu, giải thích rõ tại sao chọn verdict này thay vì alternatives.
8. risk_signals: tối đa 5 yếu tố rủi ro cụ thể từ dữ liệu, không chung chung.
9. next_watch_items: tối đa 5 ticker hoặc item cần theo dõi tiếp theo.

Thứ tự ưu tiên khi xung đột: an toàn vốn > thesis integrity > opportunity capture.
"""


SPEC = AISpec(
    system_prompt=_SYSTEM,
    output_schema=VerdictOutput,
    temperature=0.2,
    max_tokens=AIClient.DEFAULT_MAX_TOKENS,
)


def build_user_prompt(
    snapshot: SystemSnapshot,
    ranked_signals: list[RankedSignal],
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

    return f"""## System Snapshot — {snapshot.captured_at.strftime('%Y-%m-%d %H:%M')} ICT
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
"""
