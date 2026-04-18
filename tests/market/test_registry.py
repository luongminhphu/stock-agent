import pytest

from src.market.registry import (
    Exchange,
    Sector,
    SymbolNotFoundError,
    SymbolRegistry,
)


@pytest.fixture
def reg() -> SymbolRegistry:
    return SymbolRegistry()


def test_resolve_known_ticker(reg: SymbolRegistry) -> None:
    info = reg.resolve("VNM")
    assert info.ticker == "VNM"
    assert info.exchange == Exchange.HOSE
    assert info.sector == Sector.CONSUMER_GOODS


def test_resolve_case_insensitive(reg: SymbolRegistry) -> None:
    info = reg.resolve("vnm")
    assert info.ticker == "VNM"


def test_resolve_unknown_raises(reg: SymbolRegistry) -> None:
    with pytest.raises(SymbolNotFoundError):
        reg.resolve("UNKNOWN")


def test_exists_true(reg: SymbolRegistry) -> None:
    assert reg.exists("FPT") is True


def test_exists_false(reg: SymbolRegistry) -> None:
    assert reg.exists("XYZ") is False


def test_list_by_exchange_hose(reg: SymbolRegistry) -> None:
    hose = reg.list_by_exchange(Exchange.HOSE)
    tickers = [s.ticker for s in hose]
    assert "VCB" in tickers
    assert "SHB" not in tickers  # SHB is HNX


def test_list_by_sector_banking(reg: SymbolRegistry) -> None:
    banks = reg.list_by_sector(Sector.BANKING)
    tickers = [s.ticker for s in banks]
    assert "VCB" in tickers
    assert "FPT" not in tickers


def test_all_tickers_non_empty(reg: SymbolRegistry) -> None:
    all_t = reg.all_tickers()
    assert len(all_t) > 0
    assert all(isinstance(t, str) for t in all_t)
