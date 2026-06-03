"""Core Intelligence Engine — bounded context.

Owner: core segment.
Responsible for: cross-segment snapshot, signal synthesis, verdict dispatch,
feedback recording, self-improvement suggestions, and closing the feedback
loop via UserActionFeedbackListener (Wave E).

This segment is a consumer of all other segments.
It must never be imported by other segments (dependency goes one way only).

Boot wiring (call once in lifespan / startup hook)::

    from src.core.user_action_listener import UserActionFeedbackListener
    UserActionFeedbackListener().register()

Producing a UserActionEvent (from bot / api)::

    from src.platform.events import UserActionEvent
    from src.platform.event_bus import get_event_bus

    await get_event_bus().publish(
        UserActionEvent(
            user_id=user_id,
            action_type="SELL",
            ticker="VNM",
            thesis_id=42,
            price=85_000,
            note="Target reached",
        )
    )
"""

from src.core.user_action_listener import UserActionFeedbackListener

__all__ = ["UserActionFeedbackListener"]
