"""AI segment — AI client, prompt packs, structured schemas, agents.

Public API:
    AIClient          — async HTTP client with retry (canonical name)
    AIError           — base exception
    AIRateLimitError
    AIUnavailableError
    ThesisReviewAgent — reviews a thesis, returns ThesisReviewOutput
    ThesisReviewOutput — structured schema
    BriefOutput       — structured schema
    Verdict, RiskLevel, MarketSentiment — enums
"""

from src.ai.agents.thesis_review import ThesisReviewAgent
from src.ai.client import (
    AIClient,
    AIError,
    AIRateLimitError,
    AIUnavailableError,
)
from src.ai.schemas import (
    BriefOutput,
    MarketSentiment,
    RiskLevel,
    ThesisReviewOutput,
    Verdict,
)

__all__ = [
    "AIClient",
    "AIError",
    "AIRateLimitError",
    "AIUnavailableError",
    "ThesisReviewAgent",
    "ThesisReviewOutput",
    "BriefOutput",
    "Verdict",
    "RiskLevel",
    "MarketSentiment",
]
