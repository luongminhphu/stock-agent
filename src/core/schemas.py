"""Core — contract phản hồi của người dùng với khuyến nghị (FeedbackStore / evolution).

Owner: core segment. Các contract của Intelligence Engine đã gỡ cùng engine.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

FeedbackOutcome = Literal[
    "correct",
    "incorrect",
    "partial",
    "not_acted",
    "acted",  # user took the recommended action
    "rejected",  # user explicitly dismissed the verdict
]


class FeedbackEntry(BaseModel):
    """Immutable value object representing one feedback record.

    Produced by FeedbackStore.record() and consumed by evolution.py.
    """

    verdict_event_id: str
    user_id: str = ""
    verdict: str = ""  # e.g. "BUY_SIGNAL", "HOLD"
    outcome: FeedbackOutcome = "not_acted"
    trigger_source: str = ""  # "bot", "api", "scheduler"
    user_note: str | None = None
    delta_score: float = 0.0  # reserved for evolution scoring
