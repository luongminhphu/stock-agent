"""
Schemas for ThesisReviewAgent.

Owner: ai segment.
Triggered by: manual /review command or scheduled weekly review.
Distinct from ThesisJudgeOutput: full deep review vs fast signal cross-check.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from src.ai.schemas._base import Verdict, _coerce_confidence


class AssumptionRecommendation(BaseModel):
    """Recommendation for a single thesis assumption."""

    assumption_id: int
    status: Literal["VALID", "WEAKENED", "INVALIDATED", "NEEDS_MONITORING"]
    evidence: str = Field(description="Evidence supporting the status assessment")
    updated_text: str = Field(
        default="",
        description="Suggested updated assumption text if revision needed",
    )
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="before")
    @classmethod
    def coerce_aliases(cls, data: Any) -> Any:
        """Coerce legacy/alias field names from model output."""
        if not isinstance(data, dict):
            return data
        d = dict(data)
        if "assumption_id" not in d and "target_id" in d:
            d["assumption_id"] = d["target_id"]
        if "status" not in d and "recommended_status" in d:
            raw = str(d["recommended_status"]).upper()
            _status_map = {
                "VALID": "VALID",
                "INVALID": "INVALIDATED",
                "UNCERTAIN": "NEEDS_MONITORING",
                "WEAKENED": "WEAKENED",
                "INVALIDATED": "INVALIDATED",
                "NEEDS_MONITORING": "NEEDS_MONITORING",
            }
            d["status"] = _status_map.get(raw, "NEEDS_MONITORING")
        elif "status" in d:
            d["status"] = str(d["status"]).upper()
        if not d.get("evidence"):
            for alias in ("reason", "rationale", "description"):
                if d.get(alias):
                    d["evidence"] = d[alias]
                    break
        if not d.get("evidence"):
            d["evidence"] = ""
        return d

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_conf(cls, v: object) -> float:
        return _coerce_confidence(v)


class CatalystRecommendation(BaseModel):
    """Recommendation for a single thesis catalyst."""

    catalyst_id: int
    status: Literal["ACTIVE", "TRIGGERED", "DELAYED", "CANCELLED", "NEEDS_MONITORING"]
    updated_timeline: str = Field(
        default="",
        description="Updated timeline if changed from original",
    )
    notes: str = Field(default="", description="Additional context on catalyst status")
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="before")
    @classmethod
    def coerce_aliases(cls, data: Any) -> Any:
        """Coerce legacy/alias field names from model output."""
        if not isinstance(data, dict):
            return data
        d = dict(data)
        if "catalyst_id" not in d and "target_id" in d:
            d["catalyst_id"] = d["target_id"]
        if "status" not in d and "recommended_status" in d:
            raw = str(d["recommended_status"]).upper()
            _status_map = {
                "ACTIVE": "ACTIVE",
                "TRIGGERED": "TRIGGERED",
                "DELAYED": "DELAYED",
                "CANCELLED": "CANCELLED",
                "EXPIRED": "CANCELLED",
                "COMPLETED": "CANCELLED",
                "NEEDS_MONITORING": "NEEDS_MONITORING",
                "PENDING": "NEEDS_MONITORING",
                "UNCERTAIN": "NEEDS_MONITORING",
                "WATCH": "NEEDS_MONITORING",
            }
            d["status"] = _status_map.get(raw, "ACTIVE")
        elif "status" in d:
            raw = str(d["status"]).upper()
            _status_map = {
                "ACTIVE": "ACTIVE",
                "TRIGGERED": "TRIGGERED",
                "DELAYED": "DELAYED",
                "CANCELLED": "CANCELLED",
                "EXPIRED": "CANCELLED",
                "COMPLETED": "CANCELLED",
                "NEEDS_MONITORING": "NEEDS_MONITORING",
                "PENDING": "NEEDS_MONITORING",
                "UNCERTAIN": "NEEDS_MONITORING",
                "WATCH": "NEEDS_MONITORING",
            }
            d["status"] = _status_map.get(raw, raw)
        if not d.get("notes"):
            for alias in ("reason", "rationale", "description"):
                if d.get(alias):
                    d["notes"] = d[alias]
                    break
        return d

    @field_validator("confidence", mode="before")
    @classmethod
    def coerce_conf(cls, v: object) -> float:
        return _coerce_confidence(v)


class ThesisReviewOutput(BaseModel):
    """Structured output from ThesisReviewAgent.

    Owner: ai segment.
    Triggered by: manual /review command or scheduled weekly review.
    Distinct from ThesisJudgeOutput: full deep review vs fast signal cross-check.
    """

    overall_verdict: Verdict
    conviction_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Updated conviction score for the thesis (0.0-1.0)",
    )
    assumption_recommendations: list[AssumptionRecommendation] = Field(
        default_factory=list
    )
    catalyst_recommendations: list[CatalystRecommendation] = Field(
        default_factory=list
    )
    key_risks: list[str] = Field(
        default_factory=list,
        description="Current key risks to monitor",
    )
    action_recommendation: Literal[
        "HOLD", "ADD", "REDUCE", "EXIT", "WAIT_FOR_CATALYST"
    ] = Field(description="Recommended portfolio action")
    summary: str = Field(description="2-3 sentence summary of thesis health")
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="AI confidence in this review (0.0-1.0)",
    )

    @model_validator(mode="before")
    @classmethod
    def coerce_aliases(cls, data: Any) -> Any:
        """Coerce legacy/alias field names from model output."""
        if not isinstance(data, dict):
            return data
        d = dict(data)
        if "overall_verdict" not in d and "verdict" in d:
            d["overall_verdict"] = d["verdict"]
        if "conviction_score" not in d and "confidence" in d:
            d["conviction_score"] = d["confidence"]
        if not d.get("summary"):
            for alias in ("reasoning", "reason"):
                if d.get(alias):
                    d["summary"] = d[alias]
                    break
        if not d.get("key_risks") and d.get("risk_signals"):
            d["key_risks"] = d["risk_signals"]
        if "action_recommendation" not in d:
            d["action_recommendation"] = "HOLD"
        return d

    @field_validator("conviction_score", "confidence", mode="before")
    @classmethod
    def coerce_conf(cls, v: object) -> float:
        return _coerce_confidence(v)

    @field_validator("overall_verdict", mode="before")
    @classmethod
    def normalise_verdict(cls, v: object) -> object:
        if isinstance(v, str):
            return v.upper()
        return v

    @field_validator("assumption_recommendations", "catalyst_recommendations", mode="before")
    @classmethod
    def ensure_lists(cls, v: object) -> list[object]:
        if not isinstance(v, list):
            return []
        return v  # type: ignore[return-value]
