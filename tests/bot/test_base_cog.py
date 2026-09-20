"""Unit tests for BaseCog helpers (no Discord connection required)."""

from unittest.mock import AsyncMock, MagicMock

import discord

from src.bot.commands.base import BaseCog


def _make_interaction(user_id: int = 123456789) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock()
    interaction.user.id = user_id
    interaction.response = MagicMock()
    interaction.response.is_done.return_value = False
    interaction.response.send_message = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def test_user_id_returns_string() -> None:
    interaction = _make_interaction(user_id=987654321)
    assert BaseCog.user_id(interaction) == "987654321"
    assert isinstance(BaseCog.user_id(interaction), str)


async def test_send_error_uses_followup() -> None:
    """Convention: mọi command safe_defer() trước → helper luôn gửi qua followup."""
    interaction = _make_interaction()
    await BaseCog.send_error(interaction, title="Oops", description="Something broke")
    interaction.followup.send.assert_called_once()
    interaction.response.send_message.assert_not_called()
    call_kwargs = interaction.followup.send.call_args.kwargs
    assert call_kwargs["ephemeral"] is True
    embed = call_kwargs["embed"]
    assert "Oops" in embed.title


async def test_send_ok_uses_followup() -> None:
    interaction = _make_interaction()
    await BaseCog.send_ok(interaction, title="Done", description="All good")
    interaction.followup.send.assert_called_once()
    call_kwargs = interaction.followup.send.call_args.kwargs
    assert call_kwargs["ephemeral"] is True
    embed = call_kwargs["embed"]
    assert "Done" in embed.title


async def test_send_error_uses_followup_when_response_done() -> None:
    interaction = _make_interaction()
    interaction.response.is_done.return_value = True
    await BaseCog.send_error(interaction, title="Late error", description="Already deferred")
    interaction.followup.send.assert_called_once()
    interaction.response.send_message.assert_not_called()
