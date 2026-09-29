from __future__ import annotations

import asyncio
from pathlib import Path

from stoney_verify.services import naming_identity
from stoney_verify.services import search_safe_naming
from stoney_verify.services import server_design_studio as design


ROOT = Path(__file__).resolve().parents[1]


class _FakeChannel:
    def __init__(self, guild: "_FakeGuild", channel_id: int, name: str) -> None:
        self.guild = guild
        self.id = int(channel_id)
        self.name = str(name)
        self.edits: list[str] = []

    async def edit(self, *, name: str, reason: str):
        assert "Search-Safe Naming" in reason
        self.name = str(name)
        self.edits.append(self.name)
        return self


class _FakeGuild:
    def __init__(self, guild_id: int = 999) -> None:
        self.id = int(guild_id)
        self.roles: list[object] = []
        self.channels: list[_FakeChannel] = []

    def get_channel(self, channel_id: int):
        return next((channel for channel in self.channels if channel.id == int(channel_id)), None)


def _styled(value: str) -> str:
    rendered, _substitutions = design.transform_text_safe(value, "double_struck")
    assert rendered != value
    return rendered


def test_search_safe_target_keeps_decorations_and_only_normalizes_letters(monkeypatch) -> None:
    guild = _FakeGuild()
    channel = _FakeChannel(guild, 1, f"【🎥】{_styled('videos')}")
    monkeypatch.setattr(search_safe_naming, "_channel_blocker", lambda _channel: "")

    row = search_safe_naming._target_row("channel", channel)  # noqa: SLF001

    assert row is not None
    assert row["before"].startswith("【🎥】")
    assert row["after"] == "【🎥】videos"
    assert row["editable"] is True


def test_repair_batch_is_hard_capped_to_25_existing_names(monkeypatch) -> None:
    guild = _FakeGuild()
    guild.channels = [
        _FakeChannel(guild, index, f"🎥・{_styled(f'video-{index}')}")
        for index in range(1, 31)
    ]
    monkeypatch.setattr(search_safe_naming, "_channel_blocker", lambda _channel: "")

    result = asyncio.run(search_safe_naming.apply_search_safe_batch(guild, limit=999))

    assert result["batch_limit"] == 25
    assert len(result["changed"]) == 25
    assert result["remaining_editable"] == 5
    assert sum(bool(channel.edits) for channel in guild.channels) == 25


def test_enabled_policy_enforces_new_styled_channel_without_guild_scan(monkeypatch) -> None:
    guild = _FakeGuild()
    channel = _FakeChannel(guild, 1, f"🎥・{_styled('videos')}")
    guild.channels = [channel]
    monkeypatch.setattr(search_safe_naming, "_channel_blocker", lambda _channel: "")

    calls: list[int] = []

    async def search_safe_policy(guild_id: int):
        calls.append(int(guild_id))
        return {
            "mode": naming_identity.NAMING_MODE_SEARCH_SAFE,
            "roles": True,
            "channels": True,
            "categories": False,
        }

    monkeypatch.setattr(naming_identity, "get_naming_policy", search_safe_policy)

    changed = asyncio.run(search_safe_naming.enforce_channel_name(channel))

    assert changed is True
    assert channel.name == "🎥・videos"
    assert calls == [guild.id]


def test_preserve_policy_leaves_styled_live_name_untouched(monkeypatch) -> None:
    guild = _FakeGuild()
    original = f"🎥・{_styled('videos')}"
    channel = _FakeChannel(guild, 1, original)
    guild.channels = [channel]
    monkeypatch.setattr(search_safe_naming, "_channel_blocker", lambda _channel: "")

    async def preserve_policy(_guild_id: int):
        return {
            "mode": naming_identity.NAMING_MODE_PRESERVE,
            "roles": True,
            "channels": True,
            "categories": False,
        }

    monkeypatch.setattr(naming_identity, "get_naming_policy", preserve_policy)

    changed = asyncio.run(search_safe_naming.enforce_channel_name(channel))

    assert changed is False
    assert channel.name == original
    assert channel.edits == []


def test_server_design_exposes_reviewed_search_safe_workflow() -> None:
    design_v2 = (ROOT / "stoney_verify/commands_ext/public_design_studio_v2.py").read_text(encoding="utf-8")
    ui = (ROOT / "stoney_verify/commands_ext/public_search_safe_naming.py").read_text(encoding="utf-8")

    assert 'label="Search-Safe Naming"' in design_v2
    assert 'custom_id="dank_design_v2:search_safe"' in design_v2
    assert "Preview Search-Safe Repair" in ui
    assert "Enable + Repair Next 25" in ui
    assert "Manage Roles" in ui
    assert "Categories keep their full visual styling" in ui
    assert "continuous 300,000-server polling" in ui
