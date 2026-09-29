from __future__ import annotations

import asyncio
import gc
from pathlib import Path
from types import SimpleNamespace

from stoney_verify.services import naming_identity
from stoney_verify.services import naming_mutation_locks
from stoney_verify.services import role_mutation_authority
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

    rows = search_safe_naming.scan_search_safe_targets(
        guild,
        actor=SimpleNamespace(id=123),
    )
    reviewed = search_safe_naming.reviewed_search_safe_batch(rows, limit=999)
    result = asyncio.run(
        search_safe_naming.apply_search_safe_batch(
            guild,
            actor=SimpleNamespace(id=123),
            reviewed_rows=reviewed,
            limit=999,
        )
    )

    assert result["batch_limit"] == 25
    assert len(result["changed"]) == 25
    assert result["remaining_editable"] == 5
    assert sum(bool(channel.edits) for channel in guild.channels) == 25


def test_reviewed_batch_does_not_rescan_and_include_new_resource(monkeypatch) -> None:
    guild = _FakeGuild()
    reviewed_channel = _FakeChannel(guild, 1, f"🎥・{_styled('videos')}")
    guild.channels = [reviewed_channel]
    monkeypatch.setattr(search_safe_naming, "_channel_blocker", lambda _channel: "")

    rows = search_safe_naming.scan_search_safe_targets(
        guild,
        actor=SimpleNamespace(id=123),
    )
    reviewed = search_safe_naming.reviewed_search_safe_batch(rows, limit=25)

    surprise = _FakeChannel(guild, 2, f"🎵・{_styled('music')}")
    guild.channels.insert(0, surprise)

    result = asyncio.run(
        search_safe_naming.apply_search_safe_batch(
            guild,
            actor=SimpleNamespace(id=123),
            reviewed_rows=reviewed,
            limit=25,
        )
    )

    assert reviewed_channel.edits == ["🎥・videos"]
    assert surprise.edits == []
    assert len(result["changed"]) == 1
    assert result["changed"][0]["id"] == reviewed_channel.id


def test_reviewed_batch_rejects_name_changed_after_preview(monkeypatch) -> None:
    guild = _FakeGuild()
    channel = _FakeChannel(guild, 1, f"🎥・{_styled('videos')}")
    guild.channels = [channel]
    monkeypatch.setattr(search_safe_naming, "_channel_blocker", lambda _channel: "")

    rows = search_safe_naming.scan_search_safe_targets(
        guild,
        actor=SimpleNamespace(id=123),
    )
    reviewed = search_safe_naming.reviewed_search_safe_batch(rows, limit=25)

    changed_after_preview = f"🎥・{_styled('clips')}"
    channel.name = changed_after_preview

    result = asyncio.run(
        search_safe_naming.apply_search_safe_batch(
            guild,
            actor=SimpleNamespace(id=123),
            reviewed_rows=reviewed,
            limit=25,
        )
    )

    assert channel.name == changed_after_preview
    assert channel.edits == []
    assert result["changed"] == []
    assert len(result["failed"]) == 1
    assert "changed after preview" in result["failed"][0]["error"].lower()


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
    assert "Preview Next 25" in ui
    assert "reviewed_rows=self.reviewed_rows" in ui
    assert "Manage Roles" in ui
    assert "Categories keep their full visual styling" in ui
    assert "continuous 300,000-server polling" in ui


class _AuthPerms:
    def __init__(self, *, administrator: bool = False, manage_roles: bool = False) -> None:
        self.administrator = administrator
        self.manage_roles = manage_roles


class _AuthRole:
    def __init__(
        self,
        guild: object,
        role_id: int,
        name: str,
        position: int,
        *,
        managed: bool = False,
        default: bool = False,
    ) -> None:
        self.guild = guild
        self.id = int(role_id)
        self.name = str(name)
        self.position = int(position)
        self.managed = bool(managed)
        self._default = bool(default)
        self.edits: list[str] = []

    def is_default(self) -> bool:
        return self._default

    def __ge__(self, other: object) -> bool:
        return self.position >= int(getattr(other, "position", -1))

    async def edit(self, *, name: str, reason: str):
        assert "Search-Safe Naming" in reason
        self.name = str(name)
        self.edits.append(self.name)
        return self


class _AuthMember:
    def __init__(
        self,
        member_id: int,
        top_role: _AuthRole,
        *,
        administrator: bool = False,
        manage_roles: bool = True,
    ) -> None:
        self.id = int(member_id)
        self.top_role = top_role
        self.guild_permissions = _AuthPerms(
            administrator=administrator,
            manage_roles=manage_roles,
        )


class _AuthGuild:
    def __init__(self, guild_id: int = 700) -> None:
        self.id = int(guild_id)
        self.owner_id = 1
        self.owner = None
        self.roles: list[_AuthRole] = []
        self.channels: list[object] = []
        self.me: object | None = None

    def get_role(self, role_id: int):
        return next((role for role in self.roles if role.id == int(role_id)), None)

    def get_channel(self, _channel_id: int):
        return None


def test_shared_role_authority_blocks_manage_roles_actor_below_target(monkeypatch) -> None:
    guild = _AuthGuild()
    actor_top = _AuthRole(guild, 10, "Moderator", 5)
    bot_top = _AuthRole(guild, 20, "Dank Shield", 50)
    target = _AuthRole(guild, 30, _styled("staff"), 25)
    actor = _AuthMember(2, actor_top, manage_roles=True)
    bot_member = _AuthMember(999, bot_top, manage_roles=True)
    guild.me = bot_member

    monkeypatch.setattr(role_mutation_authority.discord, "Member", _AuthMember)

    blockers = role_mutation_authority.role_mutation_blockers(guild, actor, target)

    assert "Your highest role must stay above the role you edit." in blockers
    assert all("Dank Shield's highest role" not in item for item in blockers)


def test_search_safe_preview_marks_role_above_actor_as_blocked(monkeypatch) -> None:
    guild = _AuthGuild()
    actor_top = _AuthRole(guild, 10, "Moderator", 5)
    bot_top = _AuthRole(guild, 20, "Dank Shield", 50)
    target = _AuthRole(guild, 30, _styled("staff"), 25)
    actor = _AuthMember(2, actor_top, manage_roles=True)
    guild.me = _AuthMember(999, bot_top, manage_roles=True)
    guild.roles = [target]

    monkeypatch.setattr(role_mutation_authority.discord, "Member", _AuthMember)

    rows = search_safe_naming.scan_search_safe_targets(guild, actor=actor)

    assert len(rows) == 1
    assert rows[0]["kind"] == "role"
    assert rows[0]["editable"] is False
    assert "Your highest role must stay above the role you edit." in rows[0]["blocker"]


def test_search_safe_batch_rechecks_role_authority_inside_resource_lock(monkeypatch) -> None:
    guild = _AuthGuild()
    target = _AuthRole(guild, 30, _styled("staff"), 10)
    guild.roles = [target]
    actor = SimpleNamespace(id=2)

    monkeypatch.setattr(search_safe_naming.discord, "Role", _AuthRole)

    checks = {"count": 0}
    reviewed = [
        {
            "kind": "role",
            "id": target.id,
            "before": target.name,
            "after": naming_identity.search_safe_display_name(target.name),
            "editable": True,
        }
    ]

    def changing_blocker(_role, *, actor=None):
        assert actor is not None
        checks["count"] += 1
        if checks["count"] >= 2:
            return "Your highest role must stay above the role you edit."
        return ""

    monkeypatch.setattr(search_safe_naming, "_role_blocker", changing_blocker)

    result = asyncio.run(
        search_safe_naming.apply_search_safe_batch(
            guild,
            actor=actor,
            reviewed_rows=reviewed,
            limit=25,
        )
    )

    assert target.edits == []
    assert result["changed"] == []
    assert len(result["failed"]) == 1
    assert "Your highest role must stay above the role you edit." in result["failed"][0]["error"]


def test_search_safe_ui_binds_preview_and_apply_to_interaction_actor() -> None:
    ui = (ROOT / "stoney_verify/commands_ext/public_search_safe_naming.py").read_text(encoding="utf-8")

    assert "scan_search_safe_targets(guild, actor=interaction.user)" in ui
    assert ui.count("actor=interaction.user") >= 5


def test_design_plan_normalization_binds_effective_policy_snapshot(monkeypatch) -> None:
    async def policy(_guild_id: int):
        return {
            "mode": naming_identity.NAMING_MODE_SEARCH_SAFE,
            "roles": True,
            "channels": True,
            "categories": False,
        }

    monkeypatch.setattr(naming_identity, "get_naming_policy", policy)
    items = [
        {
            "kind": "text",
            "channel_id": "1",
            "before": "general",
            "after": _styled("general"),
            "status": "changed",
            "warnings": [],
        },
        {
            "kind": "category",
            "channel_id": "2",
            "before": "staff",
            "after": _styled("staff"),
            "status": "changed",
            "warnings": [],
        },
    ]

    normalized, snapshot = asyncio.run(
        search_safe_naming.normalize_design_plan_for_guild(999, items)
    )

    assert snapshot == {
        "mode": naming_identity.NAMING_MODE_SEARCH_SAFE,
        "roles": True,
        "channels": True,
        "categories": False,
    }
    assert normalized[0]["after"] == "general"
    assert normalized[0]["search_safe_naming"] is True
    assert normalized[1]["after"] == items[1]["after"]
    assert search_safe_naming.policy_matches_snapshot(snapshot, dict(snapshot))


def test_design_policy_snapshot_detects_change_before_apply() -> None:
    preview = {
        "mode": naming_identity.NAMING_MODE_SEARCH_SAFE,
        "roles": True,
        "channels": True,
        "categories": False,
    }
    current = {
        **preview,
        "mode": naming_identity.NAMING_MODE_PRESERVE,
    }

    assert search_safe_naming.policy_matches_snapshot(preview, preview)
    assert not search_safe_naming.policy_matches_snapshot(preview, current)
    assert not search_safe_naming.policy_matches_snapshot(None, preview)


def test_undo_targets_obey_current_search_safe_policy() -> None:
    policy = {
        "mode": naming_identity.NAMING_MODE_SEARCH_SAFE,
        "roles": True,
        "channels": True,
        "categories": False,
    }
    styled_channel = _styled("general")
    styled_category = _styled("staff")
    rows = search_safe_naming.normalize_undo_snapshot_items(
        [
            {
                "kind": "text",
                "old_name": styled_channel,
                "new_name": "general",
            },
            {
                "kind": "category",
                "old_name": styled_category,
                "new_name": "staff",
            },
        ],
        policy,
    )

    assert rows[0]["old_name"] == "general"
    assert rows[0]["search_safe_original_old_name"] == styled_channel
    assert rows[0]["search_safe_undo_adjusted"] is True
    assert rows[1]["old_name"] == styled_category
    assert "search_safe_undo_adjusted" not in rows[1]


def test_preserve_policy_leaves_direct_name_adjustment_unchanged() -> None:
    styled = _styled("general")
    policy = {
        "mode": naming_identity.NAMING_MODE_PRESERVE,
        "roles": True,
        "channels": True,
        "categories": False,
    }

    assert (
        search_safe_naming.policy_adjusted_name_for_policy(
            policy,
            kind="channel",
            name=styled,
        )
        == styled
    )


def test_role_editor_adjusts_user_supplied_role_names_before_discord_mutation() -> None:
    source = (
        ROOT / "stoney_verify/commands_ext/public_role_center.py"
    ).read_text(encoding="utf-8")

    assert "from stoney_verify.services import search_safe_naming" in source
    assert source.count("await search_safe_naming.policy_adjusted_name(") >= 3
    duplicate = source.index('kind="role"', source.index("async def duplicate("))
    create = source.index('kind="role"', source.index("class CreateRoleModal"))
    edit = source.index('kind="role"', source.index("class EditRoleAppearanceModal"))
    assert duplicate < create < edit


def test_channel_builder_loads_one_policy_and_uses_effective_names() -> None:
    source = (
        ROOT / "stoney_verify/services/channel_builder_execution.py"
    ).read_text(encoding="utf-8")

    assert "naming_policy = await naming_identity.get_naming_policy(gid)" in source
    assert "policy_adjusted_name_for_policy(" in source
    assert 'policy_kind = "category" if kind == "category" else "channel"' in source
    assert '"search_safe_adjusted": policy_adjusted' in source
    assert source.index("policy_adjusted_name_for_policy(") < source.index("if action in")


def test_setup_assistant_custom_names_use_one_search_safe_policy_snapshot() -> None:
    source = (
        ROOT / "stoney_verify/commands_ext/public_setup_assistant.py"
    ).read_text(encoding="utf-8")

    assert "naming_policy = await naming_identity.get_naming_policy(int(guild.id))" in source
    assert source.count("policy_adjusted_name_for_policy(") >= 4
    assert 'kind="role"' in source
    assert 'kind="channel"' in source
    assert 'kind="category"' in source
    assert source.count("naming_policy=naming_policy") >= 6


def test_server_stats_design_names_are_search_safe_before_create_or_refresh() -> None:
    source = (ROOT / "stoney_verify/security_stats.py").read_text(encoding="utf-8")

    assert "naming_policy = await naming_identity.get_naming_policy(gid)" in source
    assert source.count("policy_adjusted_name_for_policy(") >= 4
    assert "_find_owned_category(guild, cfg, naming_policy=naming_policy)" in source
    assert source.count("naming_policy=naming_policy") >= 8
    assert "category_name = search_safe_naming.policy_adjusted_name_for_policy(" in source


def test_dank_design_and_search_safe_share_one_guild_naming_lock(monkeypatch) -> None:
    from stoney_verify.commands_ext import public_design_studio as legacy

    naming_mutation_locks.GUILD_NAMING_LOCKS.clear()
    guild = _FakeGuild()
    channel = _FakeChannel(guild, 1, f"🎥・{_styled('videos')}")
    guild.channels = [channel]
    monkeypatch.setattr(search_safe_naming, "_channel_blocker", lambda _channel: "")

    async def search_safe_policy(_guild_id: int):
        return {
            "mode": naming_identity.NAMING_MODE_SEARCH_SAFE,
            "roles": True,
            "channels": True,
            "categories": False,
        }

    monkeypatch.setattr(naming_identity, "get_naming_policy", search_safe_policy)

    async def scenario() -> None:
        shared = naming_mutation_locks.guild_naming_lock(guild.id)
        assert legacy._lock_for(guild.id) is shared  # noqa: SLF001

        await shared.acquire()
        task = asyncio.create_task(search_safe_naming.enforce_channel_name(channel))
        await asyncio.sleep(0)

        assert task.done() is False
        assert channel.edits == []

        shared.release()
        assert await task is True

    asyncio.run(scenario())
    assert channel.edits == ["🎥・videos"]


def test_resource_lock_registry_does_not_split_queued_waiters() -> None:
    search_safe_naming._RESOURCE_LOCKS.clear()  # noqa: SLF001

    async def scenario() -> None:
        first = search_safe_naming._resource_lock("channel", 999, 123)  # noqa: SLF001
        await first.acquire()

        queued = search_safe_naming._resource_lock("channel", 999, 123)  # noqa: SLF001
        assert queued is first

        async def waiter() -> None:
            await queued.acquire()
            queued.release()

        waiting_task = asyncio.create_task(waiter())
        await asyncio.sleep(0)

        # Releasing the first holder must not evict the registry entry while a
        # queued waiter still owns a strong reference to the same lock.
        first.release()
        third = search_safe_naming._resource_lock("channel", 999, 123)  # noqa: SLF001
        assert third is first
        assert third is queued

        await waiting_task

    asyncio.run(scenario())
    gc.collect()

    # Weak ownership prevents one lock object per historical Discord resource
    # from becoming a permanent process-wide memory registry.
    assert len(search_safe_naming._RESOURCE_LOCKS) == 0  # noqa: SLF001


def test_resource_lock_registry_has_no_manual_release_pop_race() -> None:
    source = (
        ROOT / "stoney_verify/services/search_safe_naming.py"
    ).read_text(encoding="utf-8")

    assert "WeakValueDictionary" in source
    assert "def _release_resource_lock" not in source
    assert "_release_resource_lock(" not in source


def test_search_safe_mutations_use_shared_rest_budget_and_retry_owner() -> None:
    source = (
        ROOT / "stoney_verify/services/search_safe_naming.py"
    ).read_text(encoding="utf-8")

    assert "reserve_bulk_discord_rest_requests(" in source
    assert "return await with_retry(" in source
    assert source.count("await _safe_name_edit(") >= 3

    direct_edits = [
        line.strip()
        for line in source.splitlines()
        if ".edit(" in line and "resource.edit(" not in line
    ]
    assert direct_edits == []


def test_live_naming_mutation_paths_force_fresh_policy_reads() -> None:
    service = (
        ROOT / "stoney_verify/services/search_safe_naming.py"
    ).read_text(encoding="utf-8")
    design = (
        ROOT / "stoney_verify/commands_ext/public_design_studio_v2.py"
    ).read_text(encoding="utf-8")
    ui = (
        ROOT / "stoney_verify/commands_ext/public_search_safe_naming.py"
    ).read_text(encoding="utf-8")

    assert service.count("get_naming_policy(") >= 4
    assert service.count("refresh=True") >= 4
    assert "get_naming_policy(int(guild.id), refresh=True)" in design
    assert "get_naming_policy(guild.id, refresh=True)" in ui


def test_internal_batch_mutations_load_fresh_policy_once_per_operation() -> None:
    builder = (
        ROOT / "stoney_verify/services/channel_builder_execution.py"
    ).read_text(encoding="utf-8")
    setup = (
        ROOT / "stoney_verify/commands_ext/public_setup_assistant.py"
    ).read_text(encoding="utf-8")
    stats = (ROOT / "stoney_verify/security_stats.py").read_text(encoding="utf-8")

    assert "get_naming_policy(gid, refresh=True)" in builder
    assert "get_naming_policy(int(guild.id), refresh=True)" in setup
    assert stats.count("get_naming_policy(gid, refresh=True)") >= 3


def test_search_safe_scan_warns_when_effective_name_collides(monkeypatch) -> None:
    guild = _FakeGuild()
    styled = _FakeChannel(guild, 1, f"🎥・{_styled('videos')}")
    plain = _FakeChannel(guild, 2, "🎥・videos")
    guild.channels = [styled, plain]
    monkeypatch.setattr(search_safe_naming, "_channel_blocker", lambda _channel: "")

    rows = search_safe_naming.scan_search_safe_targets(
        guild,
        actor=SimpleNamespace(id=123),
    )

    target = next(row for row in rows if row["id"] == styled.id)
    assert target["after"] == "🎥・videos"
    assert target["collision_count"] == 1
    assert "same searchable name" in target["collision_warning"].lower()


def test_search_safe_ui_explains_collision_and_mention_scope() -> None:
    ui = (
        ROOT / "stoney_verify/commands_ext/public_search_safe_naming.py"
    ).read_text(encoding="utf-8")

    assert "Potential same-name collisions" in ui
    assert "Same searchable name warning" in ui
    assert "Search-Safe does not change Discord's role-mention permissions" in ui
