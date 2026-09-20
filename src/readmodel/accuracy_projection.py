"""AccuracyProjection — "AI nói gì, tôi làm gì, kết quả ra sao" từ feedback ledger hợp nhất.

Owner: readmodel (Wave E3c — read-only projection, không ghi).

Nguồn:
  user_behavior_logs (ai.memory)  — ledger hợp nhất từ E3a: source core|briefing|thesis,
                                    ref_type verdict|brief|pretrade
  decision_logs (thesis)          — outcome_verdict + adherence cho PRETRADE_ADVICE (E3b)

Trả lời 3 câu hỏi của nhà đầu tư:
  1. Tôi phản hồi verdict core / brief thế nào (acted vs bỏ qua)?
  2. Tôi có làm theo lời khuyên /pretrade không?
  3. Khi tôi nghe AI và khi tôi bỏ qua AI — bên nào đúng hơn?

Contract output ổn định cho api/bot/dashboard:
{
  "user_id", "days", "generated_at",
  "by_source": {
    "core":     {"total", "acted", "rejected", "not_acted", "acted_rate"},
    "briefing": {"total", "acted", "watching", "skipped", "acted_rate"},
    "pretrade": {"total", "followed", "ignored", "follow_rate",
                 "evaluated", "followed_hit_rate", "ignored_hit_rate", "advice_hit_rate"}
  },
  "trend": [{"date": "YYYY-MM-DD", "total": n}, ...],  # theo ngày, days gần nhất
  "summary": {"verdict": "ai_edge|self_edge|mixed|low_adoption|insufficient|empty",
              "headline": str, "detail": str}           # câu trả lời 1 dòng cho dashboard/bot
}

summary được tính ở đây (không ở JS/bot) để mọi surface nói cùng một kết luận.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.platform.logging import get_logger

logger = get_logger(__name__)

_CORE_ACTED = {"acted", "correct", "partial"}
_CORE_REJECTED = {"rejected", "incorrect"}

# Ngưỡng kết luận — cần đủ mẫu mới dám nói "bên nào đúng hơn".
MIN_EVALUATED_FOR_VERDICT = 5
EDGE_GAP = 0.10  # chênh hit-rate ≥ 10 điểm % mới coi là có edge
LOW_ADOPTION_RATE = 0.40  # follow_rate dưới mức này → AI chưa được tận dụng


def _rate(num: int, den: int) -> float | None:
    return round(num / den, 3) if den else None


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{round(v * 100)}%"


def build_summary(by_source: dict[str, Any]) -> dict[str, str]:
    """Một câu kết luận cho vòng feedback: nghe AI hay bỏ qua AI — bên nào đúng hơn.

    Pure function trên contract by_source → dùng được cho dashboard, bot embed, test.
    """
    pre = by_source.get("pretrade", {}) or {}
    core = by_source.get("core", {}) or {}
    brief = by_source.get("briefing", {}) or {}
    total_signals = (
        int(core.get("total", 0)) + int(brief.get("total", 0)) + int(pre.get("total", 0))
    )
    if total_signals == 0:
        return {
            "verdict": "empty",
            "headline": "Chưa có phản hồi nào được ghi nhận",
            "detail": "Phản hồi verdict, đánh dấu brief hoặc dùng /pretrade trước khi đặt lệnh "
            "để hệ thống bắt đầu đo AI nói gì / bạn làm gì / kết quả.",
        }

    evaluated = int(pre.get("evaluated", 0))
    follow_rate = pre.get("follow_rate")
    f_hit = pre.get("followed_hit_rate")
    i_hit = pre.get("ignored_hit_rate")

    if evaluated < MIN_EVALUATED_FOR_VERDICT:
        return {
            "verdict": "insufficient",
            "headline": f"Chưa đủ dữ liệu kết quả ({evaluated}/{MIN_EVALUATED_FOR_VERDICT} lệnh)",
            "detail": "Cần tối thiểu 5 lệnh pretrade đã có kết quả để so sánh "
            "làm theo AI với bỏ qua AI.",
        }

    if f_hit is not None and i_hit is not None:
        gap = f_hit - i_hit
        if gap >= EDGE_GAP:
            return {
                "verdict": "ai_edge",
                "headline": f"Làm theo AI đúng {_pct(f_hit)} · bỏ qua đúng {_pct(i_hit)}",
                "detail": "Làm theo lời khuyên đang có lợi hơn. Ưu tiên giữ kỷ luật với "
                "/pretrade và ghi lý do mỗi lần bỏ qua.",
            }
        if gap <= -EDGE_GAP:
            return {
                "verdict": "self_edge",
                "headline": f"Bỏ qua AI đúng {_pct(i_hit)} · làm theo đúng {_pct(f_hit)}",
                "detail": "Phán đoán riêng đang tốt hơn AI trong kỳ này. Xem lại các lệnh "
                "làm theo bị sai để chỉnh thesis hoặc ngưỡng cảnh báo.",
            }
        return {
            "verdict": "mixed",
            "headline": f"Làm theo đúng {_pct(f_hit)} · bỏ qua đúng {_pct(i_hit)}",
            "detail": "Chênh lệch dưới 10 điểm — chưa phân định. Tiếp tục ghi nhận thêm lệnh.",
        }

    if follow_rate is not None and follow_rate < LOW_ADOPTION_RATE:
        return {
            "verdict": "low_adoption",
            "headline": f"Bạn chỉ làm theo {_pct(follow_rate)} lời khuyên pretrade",
            "detail": "AI đang không được tận dụng. Nếu lời khuyên thường sai, hãy phản hồi "
            "để hệ thống hiệu chỉnh; nếu đúng, cân nhắc kỷ luật hơn.",
        }

    return {
        "verdict": "mixed",
        "headline": f"Độ đúng lời khuyên {_pct(pre.get('advice_hit_rate'))} trên {evaluated} lệnh",
        "detail": "Chưa có đủ cả hai nhóm (làm theo / bỏ qua) để so sánh trực tiếp.",
    }


class AccuracyProjection:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: str, days: int = 30) -> dict[str, Any]:
        days = max(1, min(int(days), 365))
        since = datetime.now(UTC) - timedelta(days=days)
        try:
            ledger = await self._ledger_counts(user_id, since)
            pretrade = await self._pretrade_stats(user_id, since)
            trend = await self._trend(user_id, since)
        except Exception as exc:  # noqa: BLE001
            logger.warning("readmodel.accuracy_projection.failed", user_id=user_id, error=str(exc))
            ledger, pretrade, trend = {}, {}, []

        core = ledger.get("core", {})
        brief = ledger.get("briefing", {})
        core_acted = sum(v for k, v in core.items() if k in _CORE_ACTED)
        core_rejected = sum(v for k, v in core.items() if k in _CORE_REJECTED)
        core_total = sum(core.values())
        brief_total = sum(brief.values())

        by_source: dict[str, Any] = {
            "core": {
                "total": core_total,
                "acted": core_acted,
                "rejected": core_rejected,
                "not_acted": core.get("not_acted", 0),
                "acted_rate": _rate(core_acted, core_total),
            },
            "briefing": {
                "total": brief_total,
                "acted": brief.get("acted", 0),
                "watching": brief.get("watching", 0),
                "skipped": brief.get("skipped", 0),
                "acted_rate": _rate(brief.get("acted", 0), brief_total),
            },
            "pretrade": pretrade
            or {
                "total": 0,
                "followed": 0,
                "ignored": 0,
                "follow_rate": None,
                "evaluated": 0,
                "followed_hit_rate": None,
                "ignored_hit_rate": None,
                "advice_hit_rate": None,
            },
        }
        return {
            "user_id": user_id,
            "days": days,
            "generated_at": datetime.now(UTC).isoformat(),
            "by_source": by_source,
            "trend": trend,
            "summary": build_summary(by_source),
        }

    # ── queries ─────────────────────────────────────────────────────────────

    async def _ledger_counts(self, user_id: str, since: datetime) -> dict[str, dict[str, int]]:
        """{source: {outcome: count}} cho source core|briefing — outcome = phần sau dấu ':'."""
        from src.ai.memory.user_behavior_log import UserBehaviorLog

        stmt = (
            select(UserBehaviorLog.source, UserBehaviorLog.signal, func.count())
            .where(
                UserBehaviorLog.user_id == user_id,
                UserBehaviorLog.source.in_(("core", "briefing")),
                UserBehaviorLog.created_at >= since,
            )
            .group_by(UserBehaviorLog.source, UserBehaviorLog.signal)
        )
        out: dict[str, dict[str, int]] = {}
        for source, signal, n in (await self._session.execute(stmt)).all():
            outcome = signal.split(":", 1)[1] if ":" in signal else signal
            out.setdefault(source, {})[outcome] = out.get(source, {}).get(outcome, 0) + int(n)
        return out

    async def _pretrade_stats(self, user_id: str, since: datetime) -> dict[str, Any]:
        """Adherence + hit-rate từ decision_logs PRETRADE_ADVICE (thesis đã reconcile)."""
        from src.thesis.models import DecisionLog

        stmt = select(
            DecisionLog.adherence,
            DecisionLog.outcome_verdict,
            func.count(),
        ).where(
            DecisionLog.user_id == user_id,
            DecisionLog.decision_type == "PRETRADE_ADVICE",
            DecisionLog.decision_at >= since,
        )
        stmt = stmt.group_by(DecisionLog.adherence, DecisionLog.outcome_verdict)
        rows = (await self._session.execute(stmt)).all()

        total = followed = ignored = 0
        evaluated = correct = 0
        f_eval = f_correct = i_eval = i_correct = 0
        for adherence, verdict, n in rows:
            n = int(n)
            total += n
            v = getattr(verdict, "value", verdict)
            is_eval = v is not None
            is_correct = v == "CORRECT"
            if is_eval:
                evaluated += n
                correct += n if is_correct else 0
            if adherence == "followed_advice":
                followed += n
                if is_eval:
                    f_eval += n
                    f_correct += n if is_correct else 0
            elif adherence == "ignored_advice":
                ignored += n
                if is_eval:
                    i_eval += n
                    i_correct += n if is_correct else 0

        return {
            "total": total,
            "followed": followed,
            "ignored": ignored,
            "follow_rate": _rate(followed, followed + ignored),
            "evaluated": evaluated,
            "followed_hit_rate": _rate(f_correct, f_eval),
            "ignored_hit_rate": _rate(i_correct, i_eval),
            "advice_hit_rate": _rate(correct, evaluated),
        }

    async def _trend(self, user_id: str, since: datetime) -> list[dict[str, Any]]:
        """Số feedback/ngày (mọi source) — cho sparkline "mức độ tương tác với AI"."""
        from src.ai.memory.user_behavior_log import UserBehaviorLog

        stmt = (
            select(UserBehaviorLog.created_at)
            .where(UserBehaviorLog.user_id == user_id, UserBehaviorLog.created_at >= since)
            .order_by(UserBehaviorLog.created_at)
        )
        counts: dict[str, int] = {}
        for (ts,) in (await self._session.execute(stmt)).all():
            if ts is None:
                continue
            day = ts.date().isoformat()
            counts[day] = counts.get(day, 0) + 1
        return [{"date": d, "total": n} for d, n in sorted(counts.items())]
