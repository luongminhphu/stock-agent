"""
Evolution Advisor prompt pack.
Owner: ai segment.

Used by: src.core.evolution.SelfImprovementAdvisor._call_ai()

Responsibility:
  - build_system_prompt(): instructs AI to act as a quantitative
    system analyst reviewing engine verdict accuracy.
  - build_user_prompt():   serialises PatternReport into a structured
    JSON block for the AI to analyse.
  - parse_ai_response():   parses the AI JSON response into a list of
    ImprovementSuggestion dataclasses. Never raises — returns [] on
    any parse failure.

Output contract (AI must return):
  {
    "suggestions": [
      {
        "target": "prompt | signal_weight | dispatch_rule | schema | heuristic",
        "description": "<what the problem is>",
        "evidence_summary": "<which metrics led to this>",
        "proposed_change": "<concrete text-diff or rule change>",
        "risk_level": "low | medium | high"
      }
    ]
  }

  Maximum 5 suggestions per run. Fewer is better.
  Suggestions are NEVER auto-applied — owner reviews each one.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.core.evolution import ImprovementSuggestion, PatternReport

_VALID_TARGETS   = {"prompt", "signal_weight", "dispatch_rule", "schema", "heuristic"}
_VALID_RISK_LEVELS = {"low", "medium", "high"}
_MAX_SUGGESTIONS = 5


def build_system_prompt() -> str:
    return """
Ban la mot quantitative systems analyst chuyen review hieu qua cua AI trading signal engine.
Nhiem vu cua ban la phan tich pattern loi, tim nguyen nhan goc re, va de xuat cai tien cu the.

Nguyen tac bat buoc:
- Chi de xuat khi co bang chung ro rang tu du lieu (>= 3 samples).
- Moi suggestion phai co proposed_change la text cu the (pseudo-code, rule, hoac diff).
- Khong de xuat viec rewrite toan bo system. Chi touch 1 diem yeu cu the.
- De xuat toi da 5 suggestions. It hon la tot hon.
- Khong bao gio noi "auto-apply" hoac "tu dong thay doi" — tat ca phai qua human review.
- risk_level: low = chi sua config/threshold, medium = sua logic, high = sua schema/contract.

Format output bat buoc (JSON):
{
  "suggestions": [
    {
      "target": "prompt | signal_weight | dispatch_rule | schema | heuristic",
      "description": "Mo ta ngan: van de la gi?",
      "evidence_summary": "Du lieu cu the dan den suggestion nay",
      "proposed_change": "Thay doi cu the can lam (pseudo-code hoac mo ta step-by-step)",
      "risk_level": "low | medium | high"
    }
  ]
}
""".strip()


def build_user_prompt(report: "PatternReport") -> str:
    data = report.to_dict()
    return f"""
Day la PatternReport tu {report.period_days} ngay qua cua Intelligence Engine:

```json
{json.dumps(data, indent=2, ensure_ascii=False)}
```

Hay phan tich va tra ve JSON chua list suggestions theo format da quy dinh.
Tap trung vao weak_verdicts va dominant_bad_triggers truoc.
Neu overall_accuracy >= 0.70 va khong co weak verdicts, tra ve suggestions = [].
""".strip()


def parse_ai_response(
    raw: str | dict[str, Any],
) -> list["ImprovementSuggestion"]:
    """Parse AI JSON response into ImprovementSuggestion list.

    Never raises. Returns [] on any parse/validation failure.
    """
    from src.core.evolution import ImprovementSuggestion

    try:
        if isinstance(raw, str):
            data = json.loads(raw)
        else:
            data = raw

        items = data.get("suggestions", [])
        if not isinstance(items, list):
            return []

        suggestions: list[ImprovementSuggestion] = []
        for item in items[:_MAX_SUGGESTIONS]:
            target     = str(item.get("target", "heuristic"))
            risk_level = str(item.get("risk_level", "low"))

            # sanitise enum values
            if target not in _VALID_TARGETS:
                target = "heuristic"
            if risk_level not in _VALID_RISK_LEVELS:
                risk_level = "low"

            description     = str(item.get("description", "")).strip()
            evidence_summary = str(item.get("evidence_summary", "")).strip()
            proposed_change  = str(item.get("proposed_change", "")).strip()

            if not description or not proposed_change:
                continue  # skip malformed item

            suggestions.append(
                ImprovementSuggestion(
                    target=target,          # type: ignore[arg-type]
                    description=description,
                    evidence_summary=evidence_summary,
                    proposed_change=proposed_change,
                    risk_level=risk_level,  # type: ignore[arg-type]
                )
            )

        return suggestions

    except Exception:
        return []
