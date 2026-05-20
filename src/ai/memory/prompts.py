"""Consolidation and pattern synthesis prompts for ai.memory.

Owner: ai segment — all prompts live here, not in domain services.
Callers: consolidator.py only.

Wave 7 additions:
  - PATTERN_SYNTHESIS_SYSTEM_PROMPT: on-demand pattern extraction.
  - build_pattern_synthesis_prompt(): groups episodes by agent_type
    for cleaner per-agent reasoning.
  - CONSOLIDATION_SYSTEM_PROMPT upgraded: agent-type glossary injected
    so AI understands the semantic meaning of each trigger type logged
    by Wave 6 agents.
  - build_consolidation_prompt() extended: includes trigger field.
"""

from __future__ import annotations

from collections import defaultdict

from src.ai.memory.models import AIInteractionLog

# ---------------------------------------------------------------------------
# Agent-type glossary — shared across both prompts
# ---------------------------------------------------------------------------

_AGENT_GLOSSARY = """\
## Agent type glossary (for interpretation)
agent=thesis_judge         → AI đánh giá luận điểm đầu tư: STRENGTHENING/WEAKENING/INVALIDATED/STABLE
agent=invalidation_detector → Xác nhận vi phạm stop-loss hoặc assumption breach; trigger=breach:<type>
agent=proactive_alert       → Cảnh báo chủ động nối nhiều tín hiệu; verdict=ALERT/NO_ALERT/NOISE
agent=briefing_agent        → Tổng hợp briefing sáng/cuối ngày; verdict=briefing_sent
agent=next_action_suggester → Kế hoạch hành động xếp hạng; trigger=next_action_plan
agent=sector_rotation       → Tín hiệu quay vòng ngành; trigger=sector_rotation:<regime>
                              regime: RISK_ON | RISK_OFF | TRANSITIONING | UNCLEAR

breach trigger types:
  breach:STOP_LOSS          → giá chạm stop-loss — pattern: kỷ luật giá
  breach:ASSUMPTION_RATIO   → > 50% giả định bị vô hiệu — pattern: chất lượng thesis
  breach:COMPOSITE          → rủi ro kép: giá + giả định — pattern: quản lý rủi ro tổng thể
  breach:WATCHDOG_CRITICAL  → tín hiệu kỹ thuật xấu — pattern: nhạy cảm tín hiệu thị trường
"""

# ---------------------------------------------------------------------------
# Weekly consolidation prompt (existing, upgraded)
# ---------------------------------------------------------------------------

CONSOLIDATION_SYSTEM_PROMPT = f"""Bạn là AI analyst chuyên phân tích hành vi và tâm lý đầu tư.
Nhiệm vụ của bạn là đọc lịch sử các AI interaction của một nhà đầu tư
và distill thành một bản tóm tắt ngắn gọn, sắc sảo về:
- Behavioral patterns: xu hướng hành vi lặp lại
- Cognitive biases: thiên kiến nhận thức nhận thấy được
- Strengths: điểm mạnh nhất quán
- Blind spots: điểm yếu / góc khuất thường bị bỏ qua
- Confidence calibration: mức độ khớp giữa confidence và kết quả thực tế

Quy tắc:
- Dựa 100% vào dữ liệu được cung cấp, không suy đoán.
- Mỗi mục tối đa 2 câu ngắn gọn bằng tiếng Việt.
- Nếu không đủ dữ liệu cho một mục, trả về null.
- Không phán xét — phân tích khách quan.

{_AGENT_GLOSSARY}"""


def build_consolidation_prompt(
    episodes: list[AIInteractionLog],
    period_start: str,
    period_end: str,
) -> str:
    """Build the user prompt for weekly consolidation from episode list.

    Wave 7: includes trigger field in each episode line for richer
    signal grouping by the AI.
    """
    episode_lines: list[str] = []
    for ep in episodes:
        parts = [
            f"- [{ep.created_at.strftime('%Y-%m-%d %H:%M')}]",
            f"agent={ep.agent_type}",
        ]
        if getattr(ep, "trigger", None):
            parts.append(f"trigger={ep.trigger}")
        if ep.tickers:
            parts.append(f"tickers={','.join(ep.tickers)}")
        if ep.ai_verdict:
            parts.append(f"verdict={ep.ai_verdict}")
        if ep.ai_confidence is not None:
            parts.append(f"conf={ep.ai_confidence:.0%}")
        episode_lines.append(" ".join(parts))
        if ep.ai_key_points:
            for line in ep.ai_key_points.splitlines()[:3]:
                episode_lines.append(f"  key_point: {line.strip()}")
        if ep.ai_risk_signals:
            for line in ep.ai_risk_signals.splitlines()[:2]:
                episode_lines.append(f"  risk: {line.strip()}")

    episodes_block = "\n".join(episode_lines) if episode_lines else "(no interactions)"

    return (
        f"Phân tích lịch sử AI interactions từ {period_start} đến {period_end}.\n"
        f"Tổng số: {len(episodes)} interactions.\n\n"
        f"{episodes_block}\n\n"
        f"Hãy distill thành MemorySnapshot theo schema được yêu cầu."
    )


# ---------------------------------------------------------------------------
# On-demand pattern synthesis prompt (Wave 7)
# ---------------------------------------------------------------------------

PATTERN_SYNTHESIS_SYSTEM_PROMPT = f"""Bạn là AI analyst chuyên nhận diện pattern hành vi đầu tư.
Nhiệm vụ: đọc các episodic memory entries và extract patterns có giá trị
cho việc cải thiện quyết định đầu tư trong tương lai.

Quy tắc:
- patterns: mảng các chuỗi mô tả pattern (tiếng Việt), tối đa 5.
  Chỉ liệt kê nếu có ít nhất 2 episodes cùng xác nhận — không suy diễn từ 1 sự kiện.
- bias_warnings: cảnh báo thiên kiến cụ thể (tiếng Việt, tối đa 3).
  Mỗi cảnh báo phải dạng: "Điều kiện X → bạn có xu hướng Y"
- market_regime_reads: liệt kê các regime đã gặp (đếm), ví dụ ["RISK_ON x3", "TRANSITIONING x2"]
- confidence: float 0.0–1.0 thể hiện mức độ tin cậy của các pattern này.
  < 0.5 nếu ít hơn 5 episodes, >= 0.7 nếu >= 10 episodes có dấu hiệu rõ ràng.
- Không bịa đặt, không suy đoán ngoài dữ liệu.
- Output: JSON theo schema PatternSynthesisOutput.

{_AGENT_GLOSSARY}"""


def build_pattern_synthesis_prompt(
    episodes: list[AIInteractionLog],
    period_label: str,
) -> str:
    """Build prompt for on-demand pattern synthesis.

    Groups episodes by agent_type so the AI can reason per-agent
    before synthesizing cross-agent patterns.

    Args:
        episodes:     Episodes to synthesise (caller pre-filters by user/date).
        period_label: Human-readable period, e.g. "last 14 days" or "2026-05-01 → 2026-05-14".
    """
    # Group by agent_type
    groups: dict[str, list[AIInteractionLog]] = defaultdict(list)
    for ep in episodes:
        groups[ep.agent_type].append(ep)

    lines: list[str] = [
        f"## Episodic memory — {period_label} ({len(episodes)} entries)",
        "",
    ]

    for agent_type, eps in sorted(groups.items()):
        lines.append(f"### {agent_type} ({len(eps)} entries)")
        for ep in eps:
            row_parts = [f"  [{ep.created_at.strftime('%Y-%m-%d %H:%M')}]"]
            if getattr(ep, "trigger", None):
                row_parts.append(f"trigger={ep.trigger}")
            if ep.tickers:
                row_parts.append(f"tickers={','.join(ep.tickers)}")
            if ep.ai_verdict:
                row_parts.append(f"verdict={ep.ai_verdict}")
            if ep.ai_confidence is not None:
                row_parts.append(f"conf={ep.ai_confidence:.0%}")
            lines.append(" ".join(row_parts))
            if ep.ai_key_points:
                for kp in ep.ai_key_points.splitlines()[:2]:
                    lines.append(f"    → {kp.strip()}")
        lines.append("")

    lines += [
        "## Task",
        "Extract investor patterns from the above. Return PatternSynthesisOutput JSON.",
    ]
    return "\n".join(lines)
