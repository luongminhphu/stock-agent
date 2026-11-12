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


def test_exists_false_with_full_listing(reg: SymbolRegistry) -> None:
    # Đã có danh sách niêm yết đầy đủ → mã lạ là không tồn tại
    for i in range(600):
        reg.enrich(f"Z{i:03d}")
    assert reg.exists("XYZ") is False


def test_provisional_ticker_without_listing(reg: SymbolRegistry) -> None:
    # Chỉ có seed (không tải được danh sách) → mã đúng định dạng được tạm nhận
    info = reg.resolve("tcx")
    assert info.ticker == "TCX" and info.exchange == Exchange.HOSE
    assert reg.exists("TCX") is True
    # Sai định dạng vẫn bị từ chối
    assert reg.exists("ABCD1") is False


def test_parse_vci_listing_keeps_only_stocks() -> None:
    from src.market.registry_loader import parse_vci_listing

    rows = [
        {
            "symbol": "TCX",
            "type": "STOCK",
            "board": "HSX",
            "organName": "CTCP Chứng khoán Kỹ Thương",
        },
        {"symbol": "SHS", "type": "STOCK", "board": "HNX", "organName": "SHS"},
        {"symbol": "BSR", "type": "STOCK", "board": "UPCOM", "organName": "BSR"},
        {"symbol": "CFPT2501", "type": "CW", "board": "HSX"},
        {"symbol": "E1VFVN30", "type": "FUND", "board": "HSX"},
        "rác",
    ]
    out = parse_vci_listing(rows)
    assert set(out) == {"TCX", "SHS", "BSR"}
    assert out["TCX"].exchange == Exchange.HOSE and "Kỹ Thương" in out["TCX"].name
    assert out["SHS"].exchange == Exchange.HNX
    assert out["BSR"].exchange == Exchange.UPCOM
    assert parse_vci_listing({"not": "a list"}) == {}


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
