"""Prompt framework utilities for all AI prompt packs.
Owner: ai segment.

Provides:
  - schema_block()  : generate inline JSON schema string to inject into SYSTEM_PROMPT
  - PromptSpec      : lightweight dataclass exposing each prompt file's contract

Convention (enforced by code review, not ABC):
  1. Every prompt file MUST define SYSTEM_PROMPT: str at module level.
  2. SYSTEM_PROMPT MUST end with schema_block(OutputModel) so the model always sees the schema.
  3. Every prompt file MUST expose SPEC: PromptSpec at module level.
  4. build_*_prompt() functions return ONLY the user message string.
  5. Confidence MUST be float 0.0-1.0 in all Pydantic output schemas.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel


def schema_block(model: type[BaseModel]) -> str:
    """Generate an inline JSON schema block to embed at the end of SYSTEM_PROMPT.

    Ensures the model always sees the full output contract rather than a vague
    reference like "theo schema đã định nghĩa". The schema is derived directly
    from the Pydantic class so it stays in sync automatically when fields change.

    Usage::

        SYSTEM_PROMPT = f"""
        Bạn là ... (persona + rules)
        {schema_block(MyOutput)}
        """
    """
    schema = model.model_json_schema()
    schema_str = json.dumps(schema, ensure_ascii=False, indent=2)
    return f"""
### Output Format
Trả về raw JSON object hợp lệ — không bọc trong markdown, không thêm text bên ngoài JSON.
Schema bắt buộc:
```json
{schema_str}
```"""


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
    """

    agent_name: str
    system_prompt: str
    output_schema: type[BaseModel]
    response_schema: dict[str, Any] | None = field(default=None)
