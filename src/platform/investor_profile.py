"""Compat re-export (1 wave) — InvestorProfile đã chuyển sang ``src.ai.memory.investor_profile``.

Owner: ai segment. Xoá shim này ở wave sau khi không còn consumer import từ platform.
"""

from __future__ import annotations

from src.ai.memory.investor_profile import (  # noqa: F401
    InvestorContext,
    InvestorProfileService,
    InvestorProfileSnapshot,
    StaticProfile,
)

__all__ = ["InvestorContext", "InvestorProfileService", "InvestorProfileSnapshot", "StaticProfile"]
