"""Consolidation prompt for ai.memory.

Owner: ai segment — all prompts live here, not in domain services.
Caller: consolidator.py only.
"""

from __future__ import annotations

from src.ai.memory.models import AIInteractionLog

CONSOLIDATION_SYSTEM_PROMPT = """Bạn là AI analyst chuyên phân tích hành vi và tâm lý đầu tư.
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
- Không phán xét — phân tích khách quan."""


def build_consolidation_prompt(
    episodes: list[AIInteractionLog],
    period_start: str,
    period_end: str,
) -> str:
    """Build the user prompt for consolidation from episode list."""
    episode_lines: list[str] = []
    for ep in episodes:
        parts = [
            f"- [{ep.created_at.strftime('%Y-%m-%d %H:%M')}]",
            f"agent={ep.agent_type}",
        ]
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

    return f"""Phân tích lịch sử AI interactions từ {period_start} đến {period_end}.
Tổng số: {len(episodes)} interactions.

{episodes_block}

Hãy distill thành MemorySnapshot theo schema được yêu cầu."""
