"""AI segment — AI client, prompt packs, structured schemas, agents.

Public API:
    AIClient                     — async HTTP client with retry (canonical name)
    PerplexityClient             — alias for AIClient, kept for backward compat
    AIError                      — base exception
    AIRateLimitError
    AIUnavailableError
    ThesisReviewAgent            — reviews a thesis, returns ThesisReviewOutput
    ThesisReviewOutput           — structured schema
    BriefOutput                  — structured schema
    Verdict, RiskLevel, MarketSentiment  — enums
"""

from src.ai.agents.thesis_review import ThesisReviewAgent
from src.ai.client import (
    AIClient,
    AIError,
    AIRateLimitError,
    AIUnavailableError,
    PerplexityClient,
    PerplexityError,
    PerplexityRateLimitError,
    PerplexityUnavailableError,
)
from src.ai.schemas import (
    BriefOutput,
    MarketSentiment,
    RiskLevel,
    ThesisReviewOutput,
    Verdict,
)

__all__ = [
    # Canonical names
    "AIClient",
    "AIError",
    "AIRateLimitError",
    "AIUnavailableError",
    # Legacy aliases
    "PerplexityClient",
    "PerplexityError",
    "PerplexityRateLimitError",
    "PerplexityUnavailableError",
    # Agents
    "ThesisReviewAgent",
    # Schemas
    "ThesisReviewOutput",
    "BriefOutput",
    "Verdict",
    "RiskLevel",
    "MarketSentiment",
]
