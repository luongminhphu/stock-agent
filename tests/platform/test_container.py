"""Wave E2b: AppContainer thay module-globals của bootstrap."""

import pytest

from src.platform import bootstrap as _bs
from src.platform.container import AppContainer


def test_require_raises_before_bootstrap() -> None:
    c = AppContainer()
    with pytest.raises(RuntimeError, match="bootstrap\\(\\) has not been called"):
        c.require("quote_service")
    c.quote_service = object()
    assert c.require("quote_service") is c.quote_service


def test_reset_clears_every_field() -> None:
    c = AppContainer()
    for name in c.field_names:
        setattr(c, name, object())
    c.reset()
    assert all(getattr(c, name) is None for name in c.field_names)


def test_getters_delegate_to_container(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = object()
    monkeypatch.setattr(_bs.container, "ticker_context_service", sentinel)
    assert _bs.get_ticker_context_service() is sentinel
    assert _bs._ticker_context_service is sentinel  # compat đọc qua module __getattr__
    with pytest.raises(AttributeError):
        _ = _bs._khong_ton_tai
