from __future__ import annotations

import asyncio
from types import SimpleNamespace

import stoney_verify.profile_card_setup_ui as setup


class _TextChannel:
    def __init__(self, channel_id: int) -> None:
        self.id = int(channel_id)
        self.mention = f"<#{self.id}>"


class _Guild:
    def __init__(self, *channel_ids: int) -> None:
        self.id = 777
        self._channels = {int(channel_id): _TextChannel(channel_id) for channel_id in channel_ids}

    def get_channel(self, channel_id: int):
        return self._channels.get(int(channel_id))


class _Response:
    def __init__(self) -> None:
        self.deferred = False

    def is_done(self) -> bool:
        return self.deferred

    async def defer(self) -> None:
        self.deferred = True


class _View:
    def __init__(self) -> None:
        self.pending_channel_ids: set[int] = set()
        self.refresh_calls: list[dict[str, object]] = []

    async def refresh(self, _interaction, *, config=None, notice: str = "") -> None:
        self.refresh_calls.append({"config": config, "notice": notice})


class _Runtime:
    def __init__(self) -> None:
        self.disabled: list[tuple[int, int]] = []
        self.reconciles = 0

    async def disable_channel(self, guild, channel) -> None:
        self.disabled.append((int(guild.id), int(channel.id)))

    async def reconcile(self) -> None:
        self.reconciles += 1


def test_selected_channels_persist_and_enable_without_a_second_button(monkeypatch):
    async def scenario() -> None:
        guild = _Guild(11, 22)
        interaction = SimpleNamespace(guild=guild, response=_Response(), client=object())
        view = _View()
        runtime = _Runtime()
        writes: list[tuple[int, dict[str, object]]] = []

        async def get_config(guild_id: int, *, refresh: bool = False):
            assert guild_id == guild.id
            assert refresh is True
            return {
                setup.LIVE_ENABLED_KEY: False,
                setup.LIVE_CHANNEL_IDS_KEY: ["11"],
            }

        async def upsert_config(guild_id: int, patch: dict[str, object]):
            writes.append((guild_id, dict(patch)))
            return {
                setup.LIVE_ENABLED_KEY: bool(patch[setup.LIVE_ENABLED_KEY]),
                setup.LIVE_CHANNEL_IDS_KEY: list(patch[setup.LIVE_CHANNEL_IDS_KEY]),
            }

        monkeypatch.setattr(setup.discord, "TextChannel", _TextChannel)
        monkeypatch.setattr(setup, "_channel_permission_issues", lambda _channel: [])
        monkeypatch.setattr(setup, "get_guild_config", get_config)
        monkeypatch.setattr(setup, "upsert_guild_config", upsert_config)
        monkeypatch.setattr(setup, "_runtime", lambda _client: runtime)

        await setup._save_selected_channels(interaction, view, {22})

        assert interaction.response.deferred is True
        assert writes == [
            (
                guild.id,
                {
                    setup.LIVE_ENABLED_KEY: True,
                    setup.LIVE_CHANNEL_IDS_KEY: ["22"],
                },
            )
        ]
        assert runtime.disabled == [(guild.id, 11)]
        assert runtime.reconciles == 1
        assert view.pending_channel_ids == {22}
        assert len(view.refresh_calls) == 1
        assert view.refresh_calls[0]["config"] == {
            setup.LIVE_ENABLED_KEY: True,
            setup.LIVE_CHANNEL_IDS_KEY: ["22"],
        }
        assert "Saved and enabled immediately" in str(view.refresh_calls[0]["notice"])

    asyncio.run(scenario())


def test_permission_failure_stops_before_defer_or_database_write(monkeypatch):
    async def scenario() -> None:
        guild = _Guild(22)
        interaction = SimpleNamespace(guild=guild, response=_Response(), client=object())
        view = _View()
        messages: list[tuple[str, bool]] = []

        async def private_message(_interaction, message: str, *, ok: bool = True):
            messages.append((message, ok))

        async def forbidden_write(*_args, **_kwargs):
            raise AssertionError("database write must not run when permissions are incomplete")

        monkeypatch.setattr(setup.discord, "TextChannel", _TextChannel)
        monkeypatch.setattr(setup, "_channel_permission_issues", lambda _channel: ["Attach Files"])
        monkeypatch.setattr(setup, "_private_message", private_message)
        monkeypatch.setattr(setup, "upsert_guild_config", forbidden_write)

        await setup._save_selected_channels(interaction, view, {22})

        assert interaction.response.deferred is False
        assert messages == [
            (
                "Fix these channel permissions before enabling live signatures:\n<#22>: Attach Files",
                False,
            )
        ]
        assert view.refresh_calls == []

    asyncio.run(scenario())
