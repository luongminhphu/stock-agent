"""Discord handler for Daily Agenda.

Owner: bot segment.
Boundary: receives DailyAgendaResult, formats into Discord message, sends.
Contains no business logic — pure presentation adapter.
"""
from __future__ import annotations

from src.ai.prompts.agenda import AgendaItem, DailyAgendaResult
from src.platform.logging import get_logger

logger = get_logger(__name__)

_PRIORITY_EMOJI = {"DECIDE": "🔴", "WATCH": "🟡", "DEFER": "🟢"}
_PRIORITY_LABEL = {"DECIDE": "QUYẾT ĐỊNH", "WATCH": "THEO DÕI", "DEFER": "ĐỂ SAU"}


class AgendaHandler:
    """Renders and pushes a DailyAgendaResult to a Discord channel."""

    def __init__(self, channel) -> None:
        self._channel = channel  # discord.TextChannel or equivalent

    async def push_agenda(self, user_id: str, agenda: DailyAgendaResult) -> None:
        """Render agenda and send to Discord. Logs warning on send failure."""
        msg = self._format(agenda)
        try:
            await self._channel.send(msg)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "agenda_handler.send_failed", user_id=user_id, error=str(exc)
            )

    def _format(self, agenda: DailyAgendaResult) -> str:
        lines = ["📋 **AGENDA HÔM NAY**", ""]
        lines.append(f"_{agenda.opening_line}_")
        lines.append("")

        for priority in ("DECIDE", "WATCH", "DEFER"):
            items: list[AgendaItem] = getattr(agenda, priority.lower())
            if not items:
                continue
            emoji = _PRIORITY_EMOJI[priority]
            label = _PRIORITY_LABEL[priority]
            lines.append(f"{emoji} **{label}**")
            for item in items:
                deadline_str = (
                    f" _(hạn: {item.deadline})_" if item.deadline else ""
                )
                lines.append(
                    f"• **{item.ticker}** — {item.reason}{deadline_str}"
                )
                lines.append(f"  → _{item.action_hint}_")
            lines.append("")

        return "\n".join(lines)
