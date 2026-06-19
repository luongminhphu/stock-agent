"""Prompt framework utilities for all AI prompt packs.
Owner: ai segment.

Provides:
  - schema_block()            : generate inline JSON schema string to inject into SYSTEM_PROMPT
  - with_persona()            : prepend VETERAN_INVESTOR_PERSONA to any system prompt
  - VETERAN_INVESTOR_PERSONA  : shared persona constant — sói già Phố Wall
  - PromptSpec                : lightweight dataclass exposing each prompt file's contract

Convention (enforced by code review, not ABC):
  1. Every prompt file MUST define SYSTEM_PROMPT: str at module level.
  2. SYSTEM_PROMPT MUST end with schema_block(OutputModel) so the model always sees the schema.
  3. Every prompt file MUST expose SPEC: PromptSpec at module level.
  4. build_*_prompt() functions return ONLY the user message string.
  5. Confidence MUST be float 0.0-1.0 in all Pydantic output schemas.
  6. High-stakes prompts (verdict, pretrade, thesis_review, stress_test) SHOULD use
     with_persona() to prepend VETERAN_INVESTOR_PERSONA before domain-specific rules.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Veteran Investor Persona
# ---------------------------------------------------------------------------

VETERAN_INVESTOR_PERSONA = """\
Bạn là một nhà đầu tư chứng khoán kỳ cựu với hơn 25 năm kinh nghiệm thực chiến \
tại thị trường Việt Nam (HOSE/HNX/UPCoM) và các thị trường quốc tế. \
Bạn đã sống sót và kiếm lợi qua khủng hoảng 2008, COVID-2020, và nhiều chu kỳ \
bong bóng-vỡ của VN-Index. Bạn quản lý danh mục cho chính mình — không phải \
viết báo cáo cho khách hàng.

Phong cách tư duy và giao tiếp:
- Nói thẳng, không hedge mọi câu bằng "có thể", "nên cân nhắc", "tham khảo thêm".
- Khi setup xấu → nói xấu, chỉ rõ lý do, không xã giao.
- Khi cơ hội tốt → nói tốt, đặt conviction rõ ràng, không viết "khả quan".
- Luôn phân biệt "noise" và "signal" — không mọi biến động đều cần phản ứng.
- Ưu tiên an toàn vốn tuyệt đối: sai một lần to hơn đúng mười lần nhỏ.
- Mọi nhận định phải đi kèm điều kiện invalidation: "Tôi sai khi nào?"
- Không bao giờ đưa ra verdict mà không có lý do cụ thể từ dữ liệu thực tế.
- Ngôn ngữ: tiếng Việt, súc tích, dùng thuật ngữ chứng khoán chuẩn xác.
"""


def with_persona(domain_rules: str) -> str:
    """Prepend VETERAN_INVESTOR_PERSONA to a domain-specific system prompt.

    Use for high-stakes agents: verdict, pretrade, thesis_review, stress_test,
    thesis_debate, signal_engine, proactive_alert.

    Usage::

        _SYSTEM = with_persona(\"\"\"
        Nhiệm vụ cụ thể của agent này...
        Quy tắc 1: ...
        {schema_block(MyOutput)}
        \"\"\")
    """
    return f"{VETERAN_INVESTOR_PERSONA}\n---\n{domain_rules}"


# ---------------------------------------------------------------------------
# Schema block
# ---------------------------------------------------------------------------


def _strip_descriptions(obj: Any) -> Any:
    """Recursively remove 'description' keys from a JSON schema dict.

    Used by schema_block(compact=True) to shrink system prompt size.
    Removes ~30-40% of schema token count while preserving field names,
    types, and required constraints that the model actually needs.
    """
    if isinstance(obj, dict):
        return {k: _strip_descriptions(v) for k, v in obj.items() if k != "description"}
    if isinstance(obj, list):
        return [_strip_descriptions(i) for i in obj]
    return obj


def schema_block(model: type[BaseModel], compact: bool = False) -> str:
    """Generate an inline JSON schema block to embed at the end of SYSTEM_PROMPT.

    Ensures the model always sees the full output contract rather than a vague
    reference like "theo schema đã định nghĩa". The schema is derived directly
    from the Pydantic class so it stays in sync automatically when fields change.

    Args:
        model:   Pydantic output class.
        compact: If True, strip 'description' fields from the schema to reduce
                 token count (~300-500 tokens saved for large schemas like
                 SignalEngineOutput). Default False preserves full schema.

    Usage::

        SYSTEM_PROMPT = f\"\"\"
        Bạn là ... (persona + rules)
        {schema_block(MyOutput)}
        \"\"\"

        # For token-heavy schemas in high-frequency agents:
        {schema_block(MyOutput, compact=True)}
    """
    schema = model.model_json_schema()
    if compact:
        schema = _strip_descriptions(schema)
    schema_str = json.dumps(schema, ensure_ascii=False, indent=2)
    return f"""
### Output Format
Trả về raw JSON object hợp lệ — không bọc trong markdown, không thêm text bên ngoài JSON.
Schema bắt buộc:
```json
{schema_str}
```"""


# ---------------------------------------------------------------------------
# PromptSpec
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PromptSpec:
    """Contract object mỗi prompt file phải expose as SPEC.

    Dùng bởi AIClient và tests để:
    - Lấy system_prompt mà không cần import từng file riêng.
    - Validate response JSON bằng output_schema.parse_raw().
    - Kiểm tra coverage: mọi agent đều có schema rõ ràng.

    Attributes:
        agent_name:      Tên agent (dùng cho logging/tracing). VD: "WhyAgent".
        system_prompt:   Full system prompt string (đã bao gồm schema_block).
        output_schema:   Pydantic class để parse structured response.
        response_schema: JSON Schema dict cho json_schema mode (chỉ một số agent
                         như SuggestAgent cần). None = dùng text mode + parse thủ công.
        max_tokens:      Output token ceiling. Calibrated per-agent to avoid
                         over-allocating (default 4096 wastes latency budget).
                         Set to ~1.4× the expected output JSON size in tokens.
        temperature:     Sampling temperature. Default 0.2 (deterministic).
    """

    agent_name: str
    system_prompt: str
    output_schema: type[BaseModel]
    response_schema: dict[str, Any] | None = field(default=None)
    max_tokens: int = field(default=4096)   # override per-agent for token efficiency
    temperature: float = field(default=0.2)
