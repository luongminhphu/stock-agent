"""AI segment — Perplexity client, prompt packs, structured schemas, agents.

Public API:
    PerplexityClient             — async HTTP client with retry
    PerplexityError              — base exception
    PerplexityRateLimitError
    PerplexityUnavailableError
    ThesisReviewAgent            — reviews a thesis, returns ThesisReviewOutput
    ThesisReviewOutput           — structured schema
    BriefOutput                  — structured schema
    Verdict, RiskLevel, MarketSentiment  — enums
"""

from src.ai.agents.thesis_review import ThesisReviewAgent
from src.ai.client import (
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
    "PerplexityClient",
    "PerplexityError",
    "PerplexityRateLimitError",
    "PerplexityUnavailableError",
    "ThesisReviewAgent",
    "ThesisReviewOutput",
    "BriefOutput",
    "Verdict",
    "RiskLevel",
    "MarketSentiment",
]
