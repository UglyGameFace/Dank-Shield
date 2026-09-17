from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import pytest

from stoney_verify.commands_ext import public_protection_contextual_permission_repair as repair
from stoney_verify.commands_ext import public_setup_gate as gate


def _full_cfg() -> dict[str, object]:
    ids = {
        key: str(index)
        for index, key in enumerate(repair.STAT_CHANNEL_PREFIXES, start=101)
    }
    return {
        "guild_id": "777",
        repair.SECURITY_STATS_ENABLED_KEY: True,
        repair.SECURITY_STATS_CATEGORY_ID_KEY: "100",
        repair.SECURITY_STATS_CHANNEL_IDS_KEY: ids,
    }


def test_protection_integration_owns_no_discord_overwrite_mutation() -> None:
    source = inspect.getsource(repair)
    assert "set_permissions(" not in source
    assert "core.apply_target_repair(" in source
    assert "clear_explicit_denies=False" in source


def test_live_stats_targets_use_only_exact_persisted_ids() -> None:
    specs = repair._target_specs(_full_cfg())
    assert specs[0].channel_id == 100
    assert specs[0].label == "Live Stats category"
    assert specs[0].feature == "tickets"
    assert specs[0].mode == "minimum"

    saved_ids = {
        int(value)
        for value in _full_cfg()[repair.SECURITY_STATS_CHANNEL_IDS_KEY].values()  # type: ignore[union-attr]
    }
    child_ids = {spec.channel_id for spec in specs[1:]}
    assert child_ids == saved_ids

    source = inspect.getsource(repair._target_specs)
    assert "SECURITY_STATS_CATEGORY_NAME" not in source
    assert "guild.channels" not in source
    assert "discord.utils.get" not in source


def test_disabled_live_stats_has_no_contextual_targets() -> None:
    cfg = _full_cfg()
    cfg[repair.SECURITY_STATS_ENABLED_KEY] = False
    assert repair._target_specs(cfg) == ()


def test_missing_saved_mapping_stays_manual_instead_of_guessing() -> None:
    cfg = _full_cfg()
    cfg[repair.SECURITY_STATS_CATEGORY_ID_KEY] = "0"
    cfg[repair.SECURITY_STATS_CHANNEL_IDS_KEY] = {}

    class FakeMember:
        guild_permissions = SimpleNamespace(
            manage_channels=True,
            manage_roles=True,
            administrator=False,
        )

    guild = SimpleNamespace(me=FakeMember())
    original_member = repair.discord.Member
    try:
        repair.discord.Member = FakeMember  # type: ignore[assignment]
        lines = repair._manual_prerequisites(guild, cfg)
    finally:
        repair.discord.Member = original_member  # type: ignore[assignment]

    assert any("saved stats category mapping is missing" in line for line in lines)
    assert any("saved counter mappings are missing" in line for line in lines)


def test_server_level_live_stats_permissions_remain_manual() -> None:
    cfg = _full_cfg()

    class FakeMember:
        guild_permissions = SimpleNamespace(
            manage_channels=False,
            manage_roles=False,
            administrator=False,
        )

    guild = SimpleNamespace(me=FakeMember())
    original_member = repair.discord.Member
    try:
        repair.discord.Member = FakeMember  # type: ignore[assignment]
        lines = repair._manual_prerequisites(guild, cfg)
    finally:
        repair.discord.Member = original_member  # type: ignore[assignment]

    text = " ".join(lines)
    assert "server-level Manage Channels" in text
    assert "server-level Manage Roles / Manage Permissions" in text


def test_button_contract_has_three_expected_states() -> None:
    healthy = repair.ProtectionStatsAudit(
        rows=[
            repair.ProtectionStatsRow(
                spec=repair.ProtectionStatsTarget(1, "Stats", "tickets", "minimum"),
                audit=SimpleNamespace(missing=[]),
            )
        ]
    )
    assert repair._button_state(healthy)[:2] == ("Access Healthy", "✅")
    assert repair._button_state(healthy)[3] is True

    repairable_audit = SimpleNamespace(missing=["view_channel"], can_apply=True)
    repairable = repair.ProtectionStatsAudit(
        rows=[
            repair.ProtectionStatsRow(
                spec=repair.ProtectionStatsTarget(1, "Stats", "tickets", "minimum"),
                audit=repairable_audit,
            )
        ]
    )
    assert repair._button_state(repairable)[:2] == ("Fix Issues", "🛠️")

    manual = repair.ProtectionStatsAudit(
        rows=repairable.rows,
        manual_issues=["Server permission missing"],
    )
    assert repair._button_state(manual)[:2] == ("Manual Fix Needed", "⚠️")


def test_repair_uses_shared_core_and_fresh_reaudit(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _full_cfg()

    class FakeChannel:
        def __init__(self, channel_id: int) -> None:
            self.id = channel_id
            self.name = f"target-{channel_id}"
            self.mention = f"<#{channel_id}>"
            self.fixed = False

    class FakeMember:
        guild_permissions = SimpleNamespace(
            manage_channels=True,
            manage_roles=True,
            administrator=False,
        )

    specs = repair._target_specs(cfg)
    channels = {spec.channel_id: FakeChannel(spec.channel_id) for spec in specs}

    class FakeGuild:
        id = 777
        me = FakeMember()

        def __init__(self) -> None:
            self.fetches: list[int] = []

        def get_channel(self, channel_id: int):
            return channels.get(channel_id)

        async def fetch_channel(self, channel_id: int):
            self.fetches.append(channel_id)
            return channels.get(channel_id)

    guild = FakeGuild()
    calls: list[tuple[int, str, str]] = []

    def fake_audit(_guild, channel, *, feature: str, mode: str):
        missing = [] if channel.fixed else ["view_channel"]
        return SimpleNamespace(
            target_id=channel.id,
            missing=missing,
            blockers=[],
            explicit_denies=[],
            warnings=[],
            can_apply=bool(missing),
        )

    async def fake_apply(
        _guild,
        channel,
        *,
        actor_id: int,
        feature: str,
        mode: str,
        include_children: bool,
        clear_explicit_denies: bool,
    ):
        assert actor_id == 99
        assert include_children is False
        assert clear_explicit_denies is False
        calls.append((channel.id, feature, mode))
        channel.fixed = True
        return SimpleNamespace(
            changed_targets=[f"{channel.mention} repaired"],
            failed_targets=[],
        )

    original_channel = repair.discord.abc.GuildChannel
    original_member = repair.discord.Member
    monkeypatch.setattr(repair.discord.abc, "GuildChannel", FakeChannel)
    monkeypatch.setattr(repair.discord, "Member", FakeMember)
    monkeypatch.setattr(repair.core, "audit_target", fake_audit)
    monkeypatch.setattr(repair.core, "apply_target_repair", fake_apply)
    try:
        after, changed, failed = asyncio.run(
            repair.repair_protection_stats(guild, cfg, actor_id=99)
        )
    finally:
        repair.discord.abc.GuildChannel = original_channel  # type: ignore[assignment]
        repair.discord.Member = original_member  # type: ignore[assignment]

    assert after.healthy is True
    assert failed == []
    assert len(changed) == len(specs)
    assert len(calls) == len(specs)
    assert guild.fetches
    assert calls[0] == (100, "tickets", "minimum")
    assert all(mode == "full" for _cid, _feature, mode in calls[1:])


def test_late_public_bootstrap_activates_protection_contextual_repair() -> None:
    source = inspect.getsource(gate.register_public_setup_gate)
    assert "apply_protection_contextual_permission_repair" in source
    assert "protection_contextual_repair" in source


def test_protection_view_is_composed_without_replacing_component_callbacks() -> None:
    source = inspect.getsource(repair.apply_protection_contextual_permission_repair)
    assert "center.ProtectionCenterView = ContextualProtectionCenterView" in source
    assert ".callback =" not in source
    assert "ProtectionCenterView.__init__ =" not in source
