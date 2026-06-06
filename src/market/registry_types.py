"""Shared types for SymbolRegistry — extracted to avoid circular imports.

Owner: market segment.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Exchange(StrEnum):
    HOSE  = "HOSE"
    HNX   = "HNX"
    UPCOM = "UPCOM"


class Sector(StrEnum):
    BANKING       = "Banking"
    REAL_ESTATE   = "Real Estate"
    CONSUMER_GOODS= "Consumer Goods"
    INDUSTRIALS   = "Industrials"
    TECHNOLOGY    = "Technology"
    ENERGY        = "Energy"
    MATERIALS     = "Materials"
    HEALTHCARE    = "Healthcare"
    UTILITIES     = "Utilities"
    FINANCIALS    = "Financials"
    TELECOMS      = "Telecoms"
    OTHER         = "Other"


@dataclass(frozen=True)
class SymbolInfo:
    ticker:      str
    name:        str
    exchange:    Exchange
    sector:      Sector
    key_metrics: str = ""   # AI context injection — populated for high-coverage tickers
