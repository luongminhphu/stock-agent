"""Opportunity Screen Subscriber — ai segment event handler.

Owner: ai segment.
Trigger: OpportunityScreenCompletedEvent (emitted by market segment).

Responsibility:
  On event: open a DB session, build investor context, call
  SectorRotationAgent with the top candidates, format the analysis,
  and publish a summary to the Discord morning channel.

Boundary rules:
  - This file lives in ai segment — it CALLS SectorRotationAgent.
  - It does NOT contain screening logic (that's market segment).
  - It does NOT send Discord messages directly — it uses the
    discord.Client injected via set_client(). If no client is set,
    analysis is still run but output is only logged.
  - register() wires the handler onto the global EventBus.
    Called once from bootstrap() after bus.start().

Design:
  OpportunityScreenSubscriber
    .register()            ← subscribe OpportunityScreenCompletedEvent
    ._handle(event)        ← async handler (never raises — dead-letter safe)
    .set_client(bot)       ← inject discord.Client after bot login

Fault tolerance:
  Every step (session, agent call, Discord send) is wrapped in try/except.
  Failure at any step is logged; subsequent steps still attempt to run.
  Handler always returns None — bus worker never sees an exception from here.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from src.platform.logging import get_logger

if TYPE_CHECKING:
    from src.platform.events import OpportunityScreenCompletedEvent

logger = get_logger(__name__)

# Maximum candidates to inject into AI prompt — avoids token bloat
MAX_CANDIDATES_FOR_PROMPT = 8


class OpportunityScreenSubscriber:
    """Subscribes OpportunityScreenCompletedEvent and triggers AI analysis.

    Inject discord.Client after bot login::
        subscriber = get_opportunity_screen_subscriber()
        subscriber.set_client(bot)  ← in bot on_ready
    """

    def __init__(
        self,
        sector_rotation_agent: object,
        session_factory: object,
        morning_channel_id: int | None = None,
        user_id: str | None = None,
    ) -> None:
        self._agent = sector_rotation_agent
        self._session_factory = session_factory
        self._morning_channel_id = morning_channel_id
        self._user_id = user_id
        self._discord_client: object | None = None

    def set_client(self, client: object) -> None:
        """Inject discord.Client after bot login. Safe to call multiple times."""
        self._discord_client = client
        logger.info("opportunity_screen_subscriber.client_set")

    def register(self) -> None:
        """Subscribe _handle to OpportunityScreenCompletedEvent on global bus."""
        from src.platform.event_bus import get_event_bus
        from src.platform.events import OpportunityScreenCompletedEvent

        bus = get_event_bus()
        bus.subscribe_handler(OpportunityScreenCompletedEvent, self._handle)
        logger.info("opportunity_screen_subscriber.registered")

    async def _handle(self, event: "OpportunityScreenCompletedEvent") -> None:
        """Handle OpportunityScreenCompletedEvent — never raises."""
        logger.info(
            "opportunity_screen_subscriber.triggered",
            candidates_found=event.candidates_found,
            top_symbol=event.top_symbol,
            event_id=event.event_id,
        )

        if event.candidates_found == 0:
            logger.info("opportunity_screen_subscriber.no_candidates")
            return

        # Step 1: Re-fetch top candidates from market service for prompt
        candidates_block = await self._fetch_candidates_block()

        # Step 2: Build investor context
        investor_context = await self._fetch_investor_context()

        # Step 3: Call SectorRotationAgent
        analysis = await self._run_analysis(event, candidates_block, investor_context)

        # Step 4: Send to Discord (optional — if client is set)
        if analysis:
            await self._send_to_discord(analysis, event)

    async def _fetch_candidates_block(self) -> str:
        """Re-run a lightweight screen to get formatted candidate lines for the prompt.

        We re-screen rather than cache because:
        - Candidates are ephemeral (no DB persistence by design)
        - Re-screen cost is minimal (reuses cached quotes from registry)
        - Ensures freshness if handler fires with slight delay
        """
        try:
            from src.platform.bootstrap import get_quote_service
            from src.market.opportunity_screen_service import OpportunityScreenService

            svc = OpportunityScreenService(
                get_quote_service(), top_n=MAX_CANDIDATES_FOR_PROMPT
            )
            result = await svc.run()
            if not result.candidates:
                return "Không có candidate nào vượt ngưỡng screen."

            lines = [f"Top {len(result.candidates)} cơ hội hôm nay:"]
            for i, c in enumerate(result.candidates, 1):
                lines.append(f"  {i}. {c.format_for_prompt()}")
            return "\n".join(lines)
        except Exception as exc:
            logger.warning(
                "opportunity_screen_subscriber.candidates_block_failed", error=str(exc)
            )
            return "(Không lấy được danh sách candidate)"

    async def _fetch_investor_context(self) -> str:
        """Build investor context string via ContextBuilder."""
        if not self._user_id:
            return ""
        try:
            from src.platform.db import AsyncSessionLocal
            from src.ai.context_builder import ContextBuilder, render_for_agent

            async with AsyncSessionLocal() as session:
                ctx = await ContextBuilder(session).build(user_id=self._user_id)
            return render_for_agent(ctx)
        except Exception as exc:
            logger.warning(
                "opportunity_screen_subscriber.context_failed", error=str(exc)
            )
            return ""

    async def _run_analysis(
        self,
        event: "OpportunityScreenCompletedEvent",
        candidates_block: str,
        investor_context: str,
    ) -> str | None:
        """Call SectorRotationAgent with opportunity context."""
        try:
            # SectorRotationAgent.analyze() accepts a free-form context string
            # and returns a structured analysis string.
            prompt_context = (
                f"{investor_context}\n\n{candidates_block}" if investor_context
                else candidates_block
            )
            result = await self._agent.analyze(  # type: ignore[union-attr]
                context=prompt_context,
                event_trigger=f"daily_screen:{event.event_id[:8]}",
            )
            analysis_text = getattr(result, "text", None) or str(result)
            logger.info(
                "opportunity_screen_subscriber.analysis_done",
                length=len(analysis_text),
            )
            return analysis_text
        except Exception as exc:
            logger.warning(
                "opportunity_screen_subscriber.analysis_failed", error=str(exc)
            )
            return None

    async def _send_to_discord(self, analysis: str, event: "OpportunityScreenCompletedEvent") -> None:
        """Send analysis to Discord morning channel if client is available."""
        if self._discord_client is None or self._morning_channel_id is None:
            logger.debug(
                "opportunity_screen_subscriber.discord_skip",
                reason="no client or channel_id",
            )
            return
        try:
            channel = self._discord_client.get_channel(self._morning_channel_id)  # type: ignore[union-attr]
            if channel is None:
                logger.warning(
                    "opportunity_screen_subscriber.channel_not_found",
                    channel_id=self._morning_channel_id,
                )
                return

            header = (
                f"📊 **Opportunity Screen — {event.top_symbol or 'No top symbol'}**\n"
                f"Tìm thấy {event.candidates_found} candidate · "
                f"Criteria: {event.screen_criteria or 'N/A'}\n"
                f"{'─' * 40}\n"
            )
            full_message = header + analysis

            # Discord 2000-char limit — split if needed
            for chunk in _split_discord_message(full_message):
                await channel.send(chunk)  # type: ignore[union-attr]

            logger.info(
                "opportunity_screen_subscriber.discord_sent",
                channel_id=self._morning_channel_id,
            )
        except Exception as exc:
            logger.warning(
                "opportunity_screen_subscriber.discord_send_failed", error=str(exc)
            )


def _split_discord_message(text: str, limit: int = 1900) -> list[str]:
    """Split a message into chunks that fit within Discord's character limit."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        # Split at last newline within limit to avoid cutting mid-sentence
        split_at = text.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = limit
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


# ── singleton ─────────────────────────────────────────────────────────────────

_subscriber: OpportunityScreenSubscriber | None = None


def get_opportunity_screen_subscriber(
    sector_rotation_agent: object | None = None,
    session_factory: object | None = None,
    morning_channel_id: int | None = None,
    user_id: str | None = None,
) -> OpportunityScreenSubscriber:
    """Return the global OpportunityScreenSubscriber singleton.

    First call must provide all constructor args.
    Subsequent calls with no args return the cached instance.
    """
    global _subscriber
    if _subscriber is None:
        if sector_rotation_agent is None or session_factory is None:
            raise RuntimeError(
                "OpportunityScreenSubscriber not initialised — "
                "call get_opportunity_screen_subscriber(agent, factory, ...) first."
            )
        _subscriber = OpportunityScreenSubscriber(
            sector_rotation_agent=sector_rotation_agent,
            session_factory=session_factory,
            morning_channel_id=morning_channel_id,
            user_id=user_id,
        )
    return _subscriber
