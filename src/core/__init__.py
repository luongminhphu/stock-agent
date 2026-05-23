"""Core Intelligence Engine — bounded context.

Owner: core segment.

Responsibilities:
    - Cross-segment SystemSnapshot aggregation
    - Signal ranking and filtering
    - AI verdict synthesis (Wave 2)
    - Feedback ingestion and signal reweighting (Wave 3)
    - Self-improvement suggestion generation (Wave 4)

Segment rules:
    - core reads from all segments; it does NOT own their data
    - core never writes directly to watchlist, thesis, briefing, or portfolio
    - core communicates outward via events only (IntelligenceEngineCompletedEvent)
    - bot and api are thin adapters — they trigger core via IntelligenceEngineRequestedEvent
"""
