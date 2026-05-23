"""UserBehaviorLog — tracks real investor actions in response to AI output.

Owner: ai segment.

Wave B rationale:
  AIInteractionLog records what AI agents *produced*.
  UserBehaviorLog records what the *investor actually did* after seeing output.

  Keeping them separate avoids the confusion where pattern synthesis
  reads AI verdicts and mistakenly attributes them as investor behaviour.

Write path:
  bot.SignalReactionListener → MemoryService.log_user_signal()

Read path:
  consolidator.build_pattern_synthesis_prompt() — joins/queries this
  table when assessing user_signal coverage per period.
  (Future) dashboard Memory panel — "Your recent reactions" feed.

Schema notes:
  - interaction_log_id: nullable FK to AIInteractionLog.id so we can
    trace which AI output triggered the reaction. Null if signal came
    from a non-AI surface (e.g. manual command).
  - signal: mirrors AIInteractionLog.user_signal values for consistency:
      bought | sold | watched | ignored | flagged
  - source: where the signal came from (discord_reaction | command | api)
  - ticker / agent_type: denormalised from the linked log row so queries
    don't always need a join.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.platform.db import Base


class UserBehaviorLog(Base):
    """Layer 1.5 — Explicit investor action signal.

    One row per deliberate investor reaction to an AI output.
    Complements AIInteractionLog (which only records AI activity).
    """

    __tablename__ = "user_behavior_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # What the investor did
    signal: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="bought | sold | watched | ignored | flagged",
    )

    # Where the signal came from
    source: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="discord_reaction",
        comment="discord_reaction | command | api",
    )

    # Link back to the AI output that was reacted to (nullable)
    interaction_log_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        index=True,
        comment="FK to AIInteractionLog.id — null if signal has no AI source",
    )

    # Denormalised context (avoids join for common queries)
    ticker: Mapped[str | None] = mapped_column(
        String(16), nullable=True, index=True
    )
    agent_type: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )

    # Optional free-text note (e.g. from /signal command)
    note: Mapped[str | None] = mapped_column(
        String(512), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    def __repr__(self) -> str:
        return (
            f"<UserBehaviorLog id={self.id} user={self.user_id!r} "
            f"signal={self.signal!r} ticker={self.ticker!r} "
            f"source={self.source!r}>"
        )
