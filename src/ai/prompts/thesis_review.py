"""
Thesis Review Prompt Pack — ai segment.

Owner: ai segment.
Boundary:
  - Defines SYSTEM_PROMPT and build_user_prompt() for ThesisReviewAgent.
  - Pure string/schema — no DB, no external I/O.
  - ThesisReviewOutput schema is the structured output contract used by
    AIClient.chat() and returned to ReviewService.

This module co-locates the prompt engineering with the schema so every change
to the output structure is reflected in the prompt in the same diff.
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Structured output schema (Pydantic-compatible TypedDicts kept as dataclasses
# for zero dependency — consumers cast to Pydantic if needed).
# The canonical Pydantic version lives in src/ai/schemas.py;
# this module re-exports the prompt constants only.
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
B\u1ea1n l\u00e0 chuy\u00ean gia ph\u00e2n t\u00edch \u0111\u1ea7u t\u01b0 ch\u1ee9ng kho\u00e1n Vi\u1ec7t Nam (HOSE / HNX / UPCoM).
Nhi\u1ec7m v\u1ee5 c\u1ee7a b\u1ea1n l\u00e0 review m\u1ed9t investment thesis v\u00e0 \u0111\u00e1nh gi\u00e1 m\u1ee9c \u0111\u1ed9 c\u00f2n hi\u1ec7u l\u1ef1c c\u1ee7a n\u00f3.

B\u1ed1i c\u1ea3nh th\u1ecb tr\u01b0\u1eddng:
- Bi\u00ean \u0111\u1ed9 dao \u0111\u1ed9ng: HOSE \u00b17%, HNX \u00b110%, UPCoM \u00b115% m\u1ed7i phi\u00ean.
- M\u00fai gi\u1edd: ICT (UTC+7). Phi\u00ean giao d\u1ecbch: 09:00\u201314:30 ICT.
- \u0110\u01a1n v\u1ecb gi\u00e1: VN\u0110. Kh\u1ed1i l\u01b0\u1ee3ng t\u00ednh b\u1eb1ng c\u1ed5 phi\u1ebfu.
- Th\u1ecb tr\u01b0\u1eddng m\u1edbi n\u1ed5i \u2014 thanh kho\u1ea3n, t\u00e2m l\u00fd \u0111\u00e1m \u0111\u00f4ng v\u00e0 ch\u00ednh s\u00e1ch v\u0129 m\u00f4 VN
  \u1ea3nh h\u01b0\u1edfng m\u1ea1nh h\u01a1n c\u00e1c th\u1ecb tr\u01b0\u1eddng ph\u00e1t tri\u1ec3n.

Nguy\u00ean t\u1eafc review:
1. \u01afu ti\u00ean B\u1ea2O TO\u00c0N V\u1ed0N tr\u01b0\u1edbc l\u1ee3i nhu\u1eadn.
2. Ch\u1ec9 INVALIDATE thesis khi c\u00f3 b\u1eb1ng ch\u1ee9ng r\u00f5 r\u00e0ng, kh\u00f4ng ph\u1ea3i ch\u1ec9 v\u00ec gi\u00e1 gi\u1ea3m ng\u1eafn h\u1ea1n.
3. Ph\u00e2n bi\u1ec7t "thesis sai" vs "thesis \u0111\u00fang nh\u01b0ng timing sai".
4. X\u00e9t c\u1ea3 y\u1ebfu t\u1ed1 \u0111\u1ecbnh t\u00ednh (qu\u1ea3n tr\u1ecb, ng\u00e0nh) l\u1eabn \u0111\u1ecbnh l\u01b0\u1ee3ng (gi\u00e1, volume, t\u00e0i ch\u00ednh).
5. M\u1ed7i risk_signal ph\u1ea3i c\u00f3 severity: LOW | MEDIUM | HIGH.
6. next_watch_items l\u00e0 c\u00e1c \u0111i\u1ec1u ki\u1ec7n c\u1ee5 th\u1ec3 c\u1ea7n theo d\u00f5i trong 2\u20134 tu\u1ea7n t\u1edbi.

Verdicts:
- BULLISH      : Thesis c\u00f2n nguy\u00ean v\u1eb9n, momentum t\u1ed1t.
- NEUTRAL      : Thesis ch\u01b0a invalidate nh\u01b0ng c\u1ea7n theo d\u00f5i.
- WEAKENING    : M\u1ed9t s\u1ed1 assumptions \u0111ang lung lay, c\u1ea7n re-evaluate s\u1edbm.
- BEARISH      : Thesis \u0111ang b\u1ecb \u0111e d\u1ecda nghi\u00eam tr\u1ecdng, c\u00e2n nh\u1eafc reduce.
- INVALIDATED  : Thesis \u0111\u00e3 b\u1ecb ph\u00e1 v\u1ee1, n\u00ean exit ho\u1eb7c stop-loss ngay.

Output ph\u1ea3i l\u00e0 JSON h\u1ee3p l\u1ec7 theo schema \u0111\u00e3 cung c\u1ea5p. Kh\u00f4ng th\u00eam n\u1ed9i dung ngo\u00e0i JSON.
"""


def build_user_prompt(
    ticker: str,
    thesis_title: str,
    thesis_summary: str,
    assumptions_with_ids: list[dict[str, Any]],
    catalysts_with_ids: list[dict[str, Any]],
    triggered_catalysts_with_ids: list[dict[str, Any]],
    current_price: float | None = None,
    entry_price: float | None = None,
    target_price: float | None = None,
) -> str:
    """
    Build the user-turn prompt for a thesis review.

    Args:
        ticker:                       M\u00e3 c\u1ed5 phi\u1ebfu (VD: "VCB", "VNM").
        thesis_title:                 Ti\u00eau \u0111\u1ec1 thesis.
        thesis_summary:               T\u00f3m t\u1eaft lu\u1eadn \u0111i\u1ec3m \u0111\u1ea7u t\u01b0.
        assumptions_with_ids:         List [{"id": int, "description": str}] \u2014 assumptions ch\u01b0a INVALID.
        catalysts_with_ids:           List [{"id": int, "description": str}] \u2014 catalysts PENDING.
        triggered_catalysts_with_ids: List [{"id": int, "description": str}] \u2014 catalysts \u0111\u00e3 TRIGGERED.
        current_price:                Gi\u00e1 hi\u1ec7n t\u1ea1i (VN\u0110). None n\u1ebfu kh\u00f4ng c\u00f3.
        entry_price:                  Gi\u00e1 v\u00e0o l\u1ec7nh (VN\u0110). None n\u1ebfu ch\u01b0a c\u00f3.
        target_price:                 Gi\u00e1 m\u1ee5c ti\u00eau (VN\u0110). None n\u1ebfu ch\u01b0a set.

    Returns:
        Formatted user prompt string.
    """
    lines: list[str] = [
        f"## Thesis Review Request: {ticker}",
        "",
        f"**Ticker:** {ticker}",
        f"**Title:** {thesis_title}",
        "",
        "### Thesis Summary",
        thesis_summary or "(kh\u00f4ng c\u00f3 t\u00f3m t\u1eaft)",
        "",
    ]

    # Price context
    price_parts: list[str] = []
    if current_price is not None:
        price_parts.append(f"Gi\u00e1 hi\u1ec7n t\u1ea1i: {current_price:,.0f} VN\u0110")
    if entry_price is not None:
        price_parts.append(f"Gi\u00e1 v\u00e0o l\u1ec7nh: {entry_price:,.0f} VN\u0110")
        if current_price is not None:
            pnl_pct = (current_price - entry_price) / entry_price * 100
            sign = "+" if pnl_pct >= 0 else ""
            price_parts.append(f"P&L ch\u01b0a th\u1ef1c hi\u1ec7n: {sign}{pnl_pct:.1f}%")
    if target_price is not None:
        price_parts.append(f"Gi\u00e1 m\u1ee5c ti\u00eau: {target_price:,.0f} VN\u0110")
        if current_price is not None:
            upside = (target_price - current_price) / current_price * 100
            price_parts.append(f"Upside c\u00f2n l\u1ea1i: {upside:.1f}%")

    if price_parts:
        lines += ["### Th\u00f4ng tin gi\u00e1", *price_parts, ""]

    # Assumptions
    if assumptions_with_ids:
        lines.append("### Assumptions (\u0111ang theo d\u00f5i)")
        for a in assumptions_with_ids:
            lines.append(f"- [ID {a['id']}] {a['description']}")
        lines.append("")
    else:
        lines += ["### Assumptions", "(kh\u00f4ng c\u00f3 assumption n\u00e0o \u0111ang active)", ""]

    # Pending catalysts
    if catalysts_with_ids:
        lines.append("### Catalysts (ch\u1edd x\u1ea3y ra)")
        for c in catalysts_with_ids:
            lines.append(f"- [ID {c['id']}] {c['description']}")
        lines.append("")

    # Triggered catalysts
    if triggered_catalysts_with_ids:
        lines.append("### Catalysts \u0111\u00e3 x\u1ea3y ra")
        for c in triggered_catalysts_with_ids:
            lines.append(f"- [ID {c['id']}] {c['description']}")
        lines.append("")

    lines += [
        "### Y\u00eau c\u1ea7u",
        "D\u1ef1a tr\u00ean th\u00f4ng tin tr\u00ean, h\u00e3y:",
        "1. \u0110\u00e1nh gi\u00e1 t\u1eebng assumption c\u00f2n gi\u00e1 tr\u1ecb hay kh\u00f4ng (VALID / INVALID / UNCERTAIN).",
        "2. C\u1eadp nh\u1eadt tr\u1ea1ng th\u00e1i catalyst n\u1ebfu c\u1ea7n (PENDING / TRIGGERED / EXPIRED).",
        "3. \u0110\u01b0a ra verdict t\u1ed5ng th\u1ec3 (BULLISH / NEUTRAL / WEAKENING / BEARISH / INVALIDATED).",
        "4. Li\u1ec7t k\u00ea risk signals v\u1edbi severity (LOW / MEDIUM / HIGH).",
        "5. \u0110\u01b0a ra next_watch_items c\u1ee5 th\u1ec3 cho 2\u20134 tu\u1ea7n t\u1edbi.",
        "6. Reasoning ng\u1eafn g\u1ecdn (t\u1ed1i \u0111a 300 t\u1eeb) gi\u1ea3i th\u00edch verdict.",
        "",
        "Tr\u1ea3 v\u1ec1 JSON theo schema \u0111\u00e3 \u0111\u1ecbnh ngh\u0129a. Kh\u00f4ng gi\u1ea3i th\u00edch ngo\u00e0i JSON.",
    ]

    return "\n".join(lines)


def build_review_prompt(
    ticker: str,
    thesis_title: str,
    thesis_summary: str,
    assumptions_with_ids: list[dict[str, Any]],
    catalysts_with_ids: list[dict[str, Any]],
    triggered_catalysts_with_ids: list[dict[str, Any]],
    current_price: float | None = None,
    entry_price: float | None = None,
    target_price: float | None = None,
    memory_context: str = "",
) -> str:
    """Alias of build_user_prompt with optional memory_context injection.

    Used by ThesisReviewAgent which injects investor memory into the prompt.
    memory_context is appended as a separate section when non-empty.
    """
    base = build_user_prompt(
        ticker=ticker,
        thesis_title=thesis_title,
        thesis_summary=thesis_summary,
        assumptions_with_ids=assumptions_with_ids,
        catalysts_with_ids=catalysts_with_ids,
        triggered_catalysts_with_ids=triggered_catalysts_with_ids,
        current_price=current_price,
        entry_price=entry_price,
        target_price=target_price,
    )
    if memory_context:
        base += f"\n\n### B\u1ed1i c\u1ea3nh nh\u00e0 \u0111\u1ea7u t\u01b0 (memory)\n{memory_context}"
    return base
