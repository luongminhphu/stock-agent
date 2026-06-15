"""Schema for RRGChartSummaryAgent output.

Owner: ai segment.
Downstream: api/routes/rrg.py → FE rrg-chart.js summary bar.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator


class RRGTickerInsight(BaseModel):
    """Per-ticker insight in the chart summary."""

    ticker: str
    insight: str = Field(
        description="1 câu ngắn gọn về cơ hội hoặc rủi ro của ticker này, tối đa 100 ký tự"
    )
    action: str = Field(
        description="BUY | WATCH | HOLD | REDUCE | AVOID"
    )

    @field_validator("action", mode="before")
    @classmethod
    def normalise_action(cls, v: object) -> str:
        mapping = {
            "STRONG_BUY": "BUY", "ACCUMULATE": "BUY",
            "MONITOR": "WATCH", "CAUTION": "WATCH",
            "NEUTRAL": "HOLD",
            "SELL": "REDUCE", "TRIM": "REDUCE",
            "STRONG_SELL": "AVOID", "EXIT": "AVOID",
        }
        s = str(v).upper().strip()
        return mapping.get(s, s)

    @field_validator("insight", mode="before")
    @classmethod
    def coerce_str(cls, v: object) -> str:
        if isinstance(v, list):
            return " ".join(str(i) for i in v)
        return str(v) if v is not None else ""


class RRGChartSummary(BaseModel):
    """AI summary of the full RRG chart — opportunities, risks, held context."""

    @model_validator(mode="before")
    @classmethod
    def _normalise_keys(cls, data: object) -> object:  # noqa: N805
        """Absorb camelCase / alternate-structure responses from older prompts."""
        if not isinstance(data, dict):
            return data
        d = dict(data)

        # market_read aliases
        if "market_read" not in d:
            for alias in ("marketRead", "overall", "summary", "overview"):
                if alias in d:
                    d["market_read"] = d[alias]
                    break

        # opportunities: accept bestOpportunity (single object) or camelCase list
        if "opportunities" not in d:
            if "bestOpportunity" in d:
                bo = d["bestOpportunity"]
                if isinstance(bo, dict):
                    # normalize inner keys
                    d["opportunities"] = [{
                        "ticker":  bo.get("ticker", ""),
                        "insight": bo.get("reason", bo.get("insight", "")),
                        "action":  bo.get("action", "WATCH"),
                    }]
            elif "topOpportunities" in d:
                d["opportunities"] = d["topOpportunities"]

        # risks: accept highestRisk (single object)
        if "risks" not in d:
            if "highestRisk" in d:
                hr = d["highestRisk"]
                if isinstance(hr, dict):
                    d["risks"] = [{
                        "ticker":  hr.get("ticker", ""),
                        "insight": hr.get("reason", hr.get("insight", "")),
                        "action":  hr.get("action", "REDUCE"),
                    }]
            elif "topRisks" in d:
                d["risks"] = d["topRisks"]

        # rotate fields: accept rotationSuggestions[0] as fallback
        if "rotate_from" not in d and "rotationSuggestions" in d:
            suggestions = d.get("rotationSuggestions") or []
            if suggestions and isinstance(suggestions[0], dict):
                first = suggestions[0]
                d.setdefault("rotate_from",   first.get("fromTicker", ""))
                d.setdefault("rotate_to",     first.get("toTicker", ""))
                d.setdefault("rotate_reason", first.get("reason", ""))

        # Normalize insight lists: each item may use "reason" instead of "insight"
        for key in ("opportunities", "risks"):
            items = d.get(key, [])
            if isinstance(items, list):
                normalized = []
                for item in items:
                    if isinstance(item, dict) and "insight" not in item and "reason" in item:
                        item = {**item, "insight": item["reason"]}
                    normalized.append(item)
                d[key] = normalized

        return d

    # Top opportunities (max 2)
    opportunities: list[RRGTickerInsight] = Field(
        default_factory=list,
        description=(
            "Tối đa 2 ticker có cơ hội tốt nhất lúc này: "
            "đang vào Leading, Improving mạnh, hoặc có momentum tăng rõ."
        ),
    )

    # Top risks (max 2)
    risks: list[RRGTickerInsight] = Field(
        default_factory=list,
        description=(
            "Tối đa 2 ticker có rủi ro cao nhất: "
            "đang Weakening nhanh, Lagging sâu, hoặc cần giảm tỷ trọng."
        ),
    )

    # Portfolio alert — only populated if held tickers are in bad quadrants
    portfolio_alert: str = Field(
        default="",
        description=(
            "Cảnh báo tập trung nếu nhà đầu tư đang hold nhiều ticker "
            "cùng Weakening/Lagging. Để trống nếu danh mục ổn."
        ),
    )

    # One-line overall market read
    market_read: str = Field(
        default="",
        description=(
            "Nhận định tổng quan về toàn bộ chart trong 1 câu: "
            "xu hướng đang tập trung ở quadrant nào, động lực tổng thể."
        )
    )

    # Rotate suggestion — only if a held ticker is weakening AND another is improving
    rotate_from: str = Field(default="", description="Ticker đang hold nên cân nhắc giảm")
    rotate_to:   str = Field(default="", description="Ticker trong watchlist nên cân nhắc tăng")
    rotate_reason: str = Field(default="", description="Lý do rotate ngắn gọn")

    @field_validator("market_read", "portfolio_alert", "rotate_from", "rotate_to", "rotate_reason", mode="before")
    @classmethod
    def coerce_str(cls, v: object) -> str:
        if isinstance(v, list):
            return " ".join(str(i) for i in v)
        return str(v) if v is not None else ""
