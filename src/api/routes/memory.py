"""Memory routes.

Owner: api segment.
Thin adapters over MemoryService, MemorySnapshotRepository, MemoryConsolidator.
No business logic lives here.

Endpoints:
  GET  /memory/snapshot  — read latest MemorySnapshot + MemoryContext (no AI)
  POST /memory/refresh   — trigger on-demand pattern synthesis (AI call)
                           Returns same shape as GET /snapshot so callers can
                           update UI directly without a follow-up refetch.
"""

from __future__ import annotations

import json
import re

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_ai_client, get_current_user_id, get_db

router = APIRouter(prefix="/memory", tags=["memory"])


# ── READ — no AI ───────────────────────────────────────────────────────────────────────────────────────

@router.get("/snapshot")
async def get_memory_snapshot(
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Return latest persisted MemorySnapshot + MemoryContext.

    Never calls AI. Used by dashboard Memory panel on load.
    Returns 404 when no snapshot exists yet.
    Returns MemoryContext fields even when snapshot is absent (episodes may exist).
    """
    from src.ai.memory.memory_service import MemoryService
    from src.ai.memory.repository import MemorySnapshotRepository

    mem_ctx = await MemoryService.get_memory_context(session, user_id=user_id)

    snapshot_repo = MemorySnapshotRepository(session)
    snapshot = await snapshot_repo.get_latest(user_id=user_id)

    if mem_ctx.is_empty() and snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No memory data yet. Use the system to accumulate episodes.",
        )

    response = _build_snapshot_response(snapshot)
    response["episodes"] = [_serialize_episode(ep) for ep in mem_ctx.recent_episodes]
    return response


# ── REFRESH — explicit AI trigger ──────────────────────────────────────────────────────────────────────────────────────

@router.post("/refresh")
async def refresh_memory(
    user_id: str = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_db),
    ai_client: object = Depends(get_ai_client),
) -> dict:
    """Trigger on-demand pattern synthesis and persist snapshot.

    Calls AI. Only on explicit user intent (button click).
    Returns {status: 'insufficient_data'} with HTTP 202 when < 5 episodes.

    On success returns the SAME shape as GET /snapshot so the caller can update
    the UI in a single round-trip — no follow-up GET needed.
    """
    from src.ai.memory.consolidator import MemoryConsolidator
    from src.ai.memory.memory_service import MemoryService
    from src.ai.memory.repository import MemorySnapshotRepository

    try:
        consolidator = MemoryConsolidator(client=ai_client, user_id=user_id)  # type: ignore[arg-type]
        output = await consolidator.synthesize_patterns(session)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Pattern synthesis failed: {exc}",
        ) from exc

    if output is None:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "status": "insufficient_data",
                "detail": "Cần tối thiểu 5 AI interactions trong 14 ngày gần nhất.",
            },
        )

    # Reload the just-persisted snapshot so we can return episode_count + period_end
    snapshot_repo = MemorySnapshotRepository(session)
    snapshot = await snapshot_repo.get_latest(user_id=user_id)

    mem_ctx = await MemoryService.get_memory_context(session, user_id=user_id)

    response = _build_snapshot_response(snapshot)
    response["episodes"] = [_serialize_episode(ep) for ep in mem_ctx.recent_episodes]
    response["status"] = "ok"
    return response


# ── Helpers ────────────────────────────────────────────────────────────────────────────────────

def _build_snapshot_response(snapshot: object | None) -> dict:
    """Serialise a MemorySnapshot ORM row into the canonical snapshot dict."""
    import json as _json

    patterns: list[str] = []
    bias_warnings: list[str] = []
    market_regime_reads: list[str] = []
    confidence: float = 0.0
    period_end: str | None = None
    episode_count: int = 0

    if snapshot is not None:
        episode_count = getattr(snapshot, "episode_count", 0) or 0
        raw_period_end = getattr(snapshot, "period_end", None)
        if raw_period_end:
            period_end = raw_period_end.strftime("%d/%m/%Y %H:%M")

        behavioral = getattr(snapshot, "behavioral_patterns", None)
        if behavioral:
            try:
                from src.ai.memory.consolidator import PatternSynthesisOutput
                stored = _json.loads(behavioral) if isinstance(behavioral, str) else behavioral
                synth = PatternSynthesisOutput(**stored)
                patterns = synth.patterns
                bias_warnings = synth.bias_warnings
                market_regime_reads = synth.market_regime_reads
                confidence = synth.confidence
            except Exception:
                pass

    return {
        "has_snapshot": snapshot is not None,
        "episode_count": episode_count,
        "confidence": confidence,
        "period_end": period_end,
        "patterns": patterns,
        "bias_warnings": bias_warnings,
        "market_regime_reads": market_regime_reads,
    }


def _serialize_episode(ep: object) -> dict:
    """Serialise one AIInteractionLog row for the dashboard episodic feed.

    Cleans ai_verdict and ai_risk_signals so the dashboard receives plain text
    instead of raw key=value blobs or Python repr strings.
    """
    tickers: list[str] = getattr(ep, "tickers", None) or []
    agent_type: str = getattr(ep, "agent_type", "") or ""
    raw_verdict: str | None = getattr(ep, "ai_verdict", None)
    ai_confidence: float | None = getattr(ep, "ai_confidence", None)
    ai_key_points: str | None = getattr(ep, "ai_key_points", None)
    raw_risk_signals: str | None = getattr(ep, "ai_risk_signals", None)
    thesis_id: int | None = getattr(ep, "thesis_id", None)
    trigger: str = getattr(ep, "trigger", "") or ""
    created_at = getattr(ep, "created_at", None)

    # ── Parse ai_verdict ───────────────────────────────────────────────────────────────────────
    # Some agents store verdict as "urgency=MONITORING confidence=0.63 ..."
    # We want: ai_verdict = "MONITORING", action = "HOLD" (default for monitoring)
    ai_verdict = _parse_verdict(raw_verdict)

    # ── Map verdict → action icon key ───────────────────────────────────────────────────────────────────────
    _verdict_to_action = {
        "BUY": "BUY", "STRONG_BUY": "BUY",
        "SELL": "SELL", "STRONG_SELL": "SELL",
        "HOLD": "HOLD", "WATCH": "HOLD", "NEUTRAL": "HOLD",
        "SKIP": "SKIP", "AVOID": "SKIP",
        "BULLISH": "BUY", "BEARISH": "SELL",
        "MONITORING": "HOLD", "ALERT": "HOLD",
    }
    action = _verdict_to_action.get((ai_verdict or "").upper(), "HOLD")

    # ── Parse ai_risk_signals → plain text snippet ──────────────────────────────────────────────────────
    ai_risk_signals = _parse_risk_signals(raw_risk_signals)

    ticker_label = ", ".join(tickers) if tickers else None
    description = f"{ticker_label} • {agent_type}" if ticker_label else agent_type

    return {
        "id": getattr(ep, "id", None),
        "description": description,
        "ticker": tickers[0] if tickers else None,
        "tickers": tickers,
        "agent_type": agent_type,
        "action": action,
        "ai_verdict": ai_verdict,
        "ai_confidence": ai_confidence,
        "ai_key_points": ai_key_points,
        "ai_risk_signals": ai_risk_signals,
        "thesis_id": thesis_id,
        "trigger": trigger,
        "outcome": None,
        "date": created_at.strftime("%d/%m/%Y %H:%M") if created_at else None,
        "created_at": created_at.strftime("%d/%m/%Y %H:%M") if created_at else None,
    }


def _parse_verdict(raw: str | None) -> str | None:
    """Extract clean verdict token from raw ai_verdict string.

    Handles:
    - Plain token: "HOLD", "BULLISH", "MONITORING"
    - key=value blob: "urgency=MONITORING confidence=0.63 strength=0.855..."
    - None / empty
    """
    if not raw:
        return None
    s = raw.strip()
    # key=value blob: extract urgency= first, then verdict=, then first value
    if "=" in s:
        m = re.search(r"(?:urgency|verdict)\s*=\s*([A-Z_]+)", s, re.IGNORECASE)
        if m:
            return m.group(1).upper()
        # fallback: grab first =VALUE token
        m2 = re.search(r"=\s*([A-Z_]+)", s, re.IGNORECASE)
        if m2:
            return m2.group(1).upper()
    # Plain token — return as-is (upper)
    return s.upper() if len(s) <= 30 else s[:30].upper()


def _parse_risk_signals(raw: str | None) -> str | None:
    """Extract first human-readable risk signal description from raw field.

    Handles:
    - None / empty / "[]"
    - Python repr: "[RiskSignal(description='Giá tăng mạnh ~6%', ...)]"
    - JSON array: [{"description": "..."}, ...]
    - Plain string
    """
    if not raw:
        return None
    s = str(raw).strip()
    if not s or s in ("[]", "null", "None"):
        return None

    # Python repr: RiskSignal(description='...')
    m = re.search(r"description\s*=\s*['\"]([^'\"]{1,200})", s, re.IGNORECASE)
    if m:
        return _truncate(m.group(1))

    # JSON array
    try:
        arr = json.loads(s)
        if isinstance(arr, list) and arr:
            first = arr[0]
            desc = first if isinstance(first, str) else (
                first.get("description") or first.get("signal") or ""
            )
            return _truncate(desc) if desc else None
    except (json.JSONDecodeError, TypeError, AttributeError):
        pass

    # Plain string fallback
    plain = s.split("\n")[0].strip()
    return _truncate(plain) if len(plain) > 2 else None


def _truncate(s: str, max_len: int = 80) -> str | None:
    s = str(s).strip()
    if not s:
        return None
    return s if len(s) <= max_len else s[: max_len - 1] + "…"
