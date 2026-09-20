"""Boundary fix B5: BriefingListener giao brief cho BriefDelivery, không import bot/discord."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.briefing import briefing_listener as bl
from src.briefing.agenda_cache import AgendaBuckets, set_agenda
from src.platform.events import BriefingRequestedEvent


class _FakeDelivery:
    def __init__(self, ready: bool = True) -> None:
        self.ready = ready
        self.calls: list[tuple] = []
        self.client = None

    def is_ready(self, phase: str) -> bool:
        return self.ready

    async def deliver(self, brief, *, phase, agenda_summary=None) -> None:
        self.calls.append((brief, phase, agenda_summary))

    def set_client(self, client) -> None:
        self.client = client


def _event(phase: str = "morning") -> BriefingRequestedEvent:
    return BriefingRequestedEvent(brief_type=phase, triggered_by="test")


def test_briefing_listener_has_no_adapter_imports() -> None:
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(bl))
    imported = {
        (n.module or "")
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for n in [node]
    } | {a.name for node in ast.walk(tree) if isinstance(node, ast.Import) for a in node.names}
    assert not any(m == "discord" or m.startswith("src.bot") for m in imported), imported


def test_set_client_forwards_to_delivery() -> None:
    d = _FakeDelivery()
    listener = bl.BriefingListener(user_id="u1", delivery=d)
    listener.set_client("client-obj")
    assert d.client == "client-obj"


@pytest.mark.anyio
async def test_handle_skips_generation_when_delivery_not_ready(monkeypatch) -> None:
    d = _FakeDelivery(ready=False)
    listener = bl.BriefingListener(user_id="u1", delivery=d)
    svc_cls = AsyncMock()
    monkeypatch.setattr("src.briefing.service.BriefingService", svc_cls)
    await listener._handle(_event())
    assert d.calls == [] and svc_cls.call_count == 0


@pytest.mark.anyio
async def test_handle_generates_then_delivers_with_agenda(monkeypatch) -> None:
    d = _FakeDelivery()
    listener = bl.BriefingListener(user_id="u1", delivery=d)
    output = SimpleNamespace(summary="tóm tắt")
    fake_svc = SimpleNamespace(
        generate_morning_brief=AsyncMock(return_value=SimpleNamespace(output=output)),
        generate_eod_brief=AsyncMock(),
    )
    monkeypatch.setattr("src.briefing.service.BriefingService", lambda **kw: fake_svc)
    monkeypatch.setattr("src.watchlist.service.WatchlistService", lambda **kw: object())
    import src.platform.bootstrap as boot

    monkeypatch.setattr(boot.container, "quote_service", object())
    monkeypatch.setattr(boot.container, "pnl_service_class", lambda **kw: object())
    monkeypatch.setattr(boot.container, "briefing_agent", object())
    monkeypatch.setattr(boot.container, "sector_rotation_agent", object())
    set_agenda("u1", "DECIDE: HPG", AgendaBuckets(decide=["HPG"], watch=[], defer=[]))

    await listener._handle(_event("morning"))

    assert d.calls == [(output, "morning", "DECIDE: HPG")]
    fake_svc.generate_morning_brief.assert_awaited_once_with(user_id="u1")
