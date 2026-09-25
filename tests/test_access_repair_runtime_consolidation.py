from __future__ import annotations

import asyncio
from types import SimpleNamespace

import discord

from stoney_verify import permission_repair_core as core
from stoney_verify import setup_permission_repair_services as setup_repair
from stoney_verify import startup_diagnostics


class FakeTarget:
    def __init__(
        self,
        *,
        channel_id: int = 10,
        manage_roles: bool = False,
        missing_access: bool = True,
    ) -> None:
        self.id = channel_id
        self.name = "verification"
        self.mention = f"<#{channel_id}>"
        self.manage_roles = manage_roles
        self.missing_access = missing_access

    def permissions_for(self, _member):
        return SimpleNamespace(
            administrator=False,
            manage_roles=self.manage_roles,
            view_channel=not self.missing_access,
            send_messages=not self.missing_access,
            embed_links=not self.missing_access,
            attach_files=not self.missing_access,
            read_message_history=not self.missing_access,
            manage_channels=False,
            manage_messages=False,
            manage_threads=False,
            send_messages_in_threads=False,
            move_members=False,
        )

    def overwrites_for(self, _member):
        return discord.PermissionOverwrite()


def _member(*, guild_manage_roles: bool, administrator: bool = False):
    return SimpleNamespace(
        id=42,
        guild_permissions=SimpleNamespace(
            administrator=administrator,
            manage_roles=guild_manage_roles,
            manage_channels=False,
            view_channel=True,
            view_audit_log=True,
        ),
        top_role=SimpleNamespace(managed=True),
    )


class _ParentCategory:
    def __init__(self, member, overwrite: discord.PermissionOverwrite) -> None:
        self.id = 700
        self.name = "the-lounge"
        self.mention = "<#700>"
        self.member = member
        self.overwrite = overwrite

    def overwrites_for(self, principal):
        if principal is self.member:
            return discord.PermissionOverwrite.from_pair(*self.overwrite.pair())
        return discord.PermissionOverwrite()


class _RepairableChild:
    def __init__(self, member, parent: _ParentCategory, *, manage_roles: bool) -> None:
        self.id = 701
        self.name = "general"
        self.mention = "<#701>"
        self.member = member
        self.category = parent
        self.permissions_synced = False
        self.manage_roles = manage_roles
        self.current = discord.PermissionOverwrite()
        self.permission_calls: list[discord.PermissionOverwrite] = []

    def permissions_for(self, principal):
        assert principal is self.member
        return SimpleNamespace(
            administrator=False,
            manage_roles=self.manage_roles,
            view_channel=self.current.view_channel is True,
            send_messages=self.current.send_messages is True,
            embed_links=self.current.embed_links is True,
            attach_files=self.current.attach_files is True,
            read_message_history=self.current.read_message_history is True,
            manage_channels=False,
            manage_messages=self.current.manage_messages is True,
            manage_threads=self.current.manage_threads is True,
            send_messages_in_threads=self.current.send_messages_in_threads is True,
            move_members=False,
        )

    def overwrites_for(self, principal):
        if principal is self.member:
            return discord.PermissionOverwrite.from_pair(*self.current.pair())
        return discord.PermissionOverwrite()

    async def set_permissions(self, principal, *, overwrite=None, reason=None):
        assert principal is self.member
        assert overwrite is not None
        saved = discord.PermissionOverwrite.from_pair(*overwrite.pair())
        self.permission_calls.append(saved)
        self.current = saved


def test_fix_access_owner_authority_does_not_require_member_cache_shape() -> None:
    interaction = SimpleNamespace(
        guild=SimpleNamespace(id=77, owner_id=88),
        user=SimpleNamespace(id=88),
        permissions=SimpleNamespace(
            administrator=False,
            manage_guild=False,
            manage_channels=False,
        ),
    )

    assert core._actor_can_manage(interaction) is True


def test_server_manage_roles_missing_is_the_overwrite_hard_blocker(monkeypatch) -> None:
    guild = SimpleNamespace(id=77)
    target = FakeTarget(manage_roles=False)
    monkeypatch.setattr(core, "_bot_member", lambda _guild: _member(guild_manage_roles=False))

    blocker = core.permission_overwrite_edit_blocker(guild, target)

    assert "server role is missing **Manage Roles**" in blocker
    assert "Manage Channels" not in blocker


def test_channel_manage_permissions_self_lockout_is_explained_before_write(monkeypatch) -> None:
    guild = SimpleNamespace(id=77, default_role=object())
    target = FakeTarget(manage_roles=False)
    monkeypatch.setattr(core, "_bot_member", lambda _guild: _member(guild_manage_roles=True))
    monkeypatch.setattr(core, "_target_supported", lambda _target: True)

    blocker = core.permission_overwrite_edit_blocker(guild, target)
    report = core.audit_target(guild, target, feature="general", mode="minimum")

    assert "self-lockout" in blocker
    assert "Manage Permissions is denied" in blocker
    assert report.blockers == [blocker]
    assert report.can_apply is False


def test_unsynced_child_with_good_parent_reports_exact_manual_handoff(monkeypatch) -> None:
    member = _member(guild_manage_roles=True)
    parent = _ParentCategory(
        member,
        discord.PermissionOverwrite(
            view_channel=True,
            manage_roles=True,
            manage_messages=True,
        ),
    )
    target = _RepairableChild(member, parent, manage_roles=False)
    guild = SimpleNamespace(id=77, default_role=object())

    monkeypatch.setattr(core, "_bot_member", lambda _guild: member)
    monkeypatch.setattr(core, "_target_supported", lambda _target: True)

    blocker = core.permission_overwrite_edit_blocker(guild, target)
    report = core.audit_target(guild, target, feature="general", mode="minimum")

    assert "not synced" in blocker
    assert "parent category" in blocker
    assert "already allows Manage Permissions" in blocker
    assert "Sync Now" in blocker
    assert "entire permission set" in blocker
    assert report.blockers == [blocker]
    assert report.can_apply is False


def test_empty_child_bot_overwrite_can_use_parent_as_bot_only_template(monkeypatch) -> None:
    member = _member(guild_manage_roles=True)
    parent = _ParentCategory(
        member,
        discord.PermissionOverwrite(
            view_channel=True,
            manage_roles=True,
            manage_messages=True,
        ),
    )
    target = _RepairableChild(member, parent, manage_roles=True)
    guild = SimpleNamespace(id=77)

    monkeypatch.setattr(core, "_bot_member", lambda _guild: member)

    seeded, copied = core.seed_bot_overwrite_from_parent(
        guild,
        target,
        discord.PermissionOverwrite(),
    )

    assert seeded.view_channel is True
    assert seeded.manage_roles is True
    assert seeded.manage_messages is True
    assert "manage_roles" in copied
    assert "manage_messages" in copied


def test_existing_child_bot_overwrite_is_not_replaced_by_parent_template(monkeypatch) -> None:
    member = _member(guild_manage_roles=True)
    parent = _ParentCategory(
        member,
        discord.PermissionOverwrite(
            view_channel=True,
            manage_roles=True,
            manage_messages=True,
        ),
    )
    target = _RepairableChild(member, parent, manage_roles=True)
    guild = SimpleNamespace(id=77)
    current = discord.PermissionOverwrite(view_channel=False)

    monkeypatch.setattr(core, "_bot_member", lambda _guild: member)

    seeded, copied = core.seed_bot_overwrite_from_parent(guild, target, current)

    assert seeded.view_channel is False
    assert seeded.manage_messages is None
    assert copied == []


def test_specific_channel_repair_seeds_parent_bot_template_when_discord_allows_write(
    monkeypatch,
) -> None:
    member = _member(guild_manage_roles=True)
    parent = _ParentCategory(
        member,
        discord.PermissionOverwrite(
            view_channel=True,
            manage_roles=True,
            manage_messages=True,
        ),
    )
    target = _RepairableChild(member, parent, manage_roles=True)
    guild = SimpleNamespace(id=77, default_role=object())

    monkeypatch.setattr(core, "_bot_member", lambda _guild: member)
    monkeypatch.setattr(core, "_target_supported", lambda _target: True)

    async def record(**_kwargs):
        return True

    monkeypatch.setattr(core, "_record_repair_event", record)

    result = asyncio.run(
        core.apply_target_repair(
            guild,
            target,
            actor_id=99,
            feature="general",
            mode="minimum",
            include_children=False,
        )
    )

    assert result.ok is True
    assert result.failed_targets == []
    assert len(target.permission_calls) == 1
    written = target.permission_calls[0]
    assert written.manage_roles is True
    assert written.manage_messages is True
    assert written.view_channel is True
    assert written.send_messages is True
    assert written.read_message_history is True


def test_setup_repair_does_not_attempt_overwrite_write_after_channel_self_lockout(
    monkeypatch,
) -> None:
    member = _member(guild_manage_roles=True)
    parent = _ParentCategory(
        member,
        discord.PermissionOverwrite(
            view_channel=True,
            manage_roles=True,
        ),
    )
    target = _RepairableChild(member, parent, manage_roles=False)
    guild = SimpleNamespace(id=77)

    monkeypatch.setattr(core, "_bot_member", lambda _guild: member)

    async def targets(_guild, *, include_activity_coverage=False):
        assert include_activity_coverage is True
        return [
            SimpleNamespace(
                channel=target,
                overwrites={
                    member: discord.PermissionOverwrite(
                        view_channel=True,
                        read_message_history=True,
                        manage_threads=True,
                        manage_roles=True,
                    )
                },
            )
        ], [], [], []

    monkeypatch.setattr(setup_repair, "_build_expanded_targets", targets)

    result = asyncio.run(
        setup_repair.preview_or_apply(
            guild,
            apply=True,
            include_activity_coverage=True,
        )
    )

    assert result["changed"] == []
    assert target.permission_calls == []
    assert len(result["manual_actions"]) == 1
    assert "not synced" in result["manual_actions"][0]
    assert "Sync Now" in result["manual_actions"][0]


def test_effective_manage_roles_allows_overwrite_repair_even_without_manage_channels(monkeypatch) -> None:
    guild = SimpleNamespace(id=77, default_role=object())
    target = FakeTarget(manage_roles=True)
    monkeypatch.setattr(core, "_bot_member", lambda _guild: _member(guild_manage_roles=True))
    monkeypatch.setattr(core, "_target_supported", lambda _target: True)

    assert core.permission_overwrite_edit_blocker(guild, target) == ""
    report = core.audit_target(guild, target, feature="general", mode="minimum")
    assert report.missing
    assert report.blockers == []
    assert report.can_apply is True


def test_setup_repair_does_not_hard_block_only_because_manage_channels_is_missing(
    monkeypatch,
) -> None:
    guild = SimpleNamespace(id=77)
    member = _member(guild_manage_roles=True)

    monkeypatch.setattr(core, "_bot_member", lambda _guild: member)

    async def no_targets(_guild, *, include_activity_coverage=False):
        assert include_activity_coverage is False
        return [], [], [], []

    monkeypatch.setattr(setup_repair, "_build_expanded_targets", no_targets)

    result = asyncio.run(
        setup_repair.preview_or_apply(
            guild,
            apply=False,
            include_activity_coverage=False,
        )
    )

    assert result["target_count"] == 0
    assert "Permission repair is blocked" not in str(result.get("error") or "")
    assert any("Manage Channels" in note for note in result["notes"])
    assert not any("Manage Channels" in item for item in result["manual_actions"])


def test_startup_diagnostics_only_tracks_real_native_boot_owners() -> None:
    owners = set(startup_diagnostics.EXPECTED_STARTUP_OWNER_MODULES)

    retired_or_lazy = {
        "stoney_verify.startup_guards.basic_verification_mode_guard",
        "stoney_verify.startup_guards.id_verify_allowlist_guard",
        "stoney_verify.startup_guards.unverified_ticket_panel_flow",
    }
    required_native = {
        "stoney_verify.startup_guards.process_health",
        "stoney_verify.startup_guards.discord_api_safety",
        "stoney_verify.command_runtime",
        "stoney_verify.startup_guards.public_server_env_id_guard",
        "stoney_verify.interaction_guard",
        "stoney_verify.verification_new.basic_verify",
        "stoney_verify.ticket_panel_runtime",
        "stoney_verify.interaction_handlers",
        "stoney_verify.commands_ext.public_self_roles_group",
        "stoney_verify.members_new.activity_tracker",
    }

    assert owners.isdisjoint(retired_or_lazy)
    assert required_native <= owners


def _component(view: discord.ui.View, custom_id: str):
    matches = [
        child
        for child in view.children
        if str(getattr(child, "custom_id", "") or "") == custom_id
    ]
    assert len(matches) == 1
    return matches[0]


def test_undo_is_disabled_until_a_real_repair_created_a_token() -> None:
    guild = SimpleNamespace(me=None)
    empty_state = core.PermissionRepairState(guild=guild, actor_id=1)
    empty_view = core.TargetPermissionRepairView(empty_state)
    assert _component(empty_view, "dank_permission_repair:undo").disabled is True

    changed_state = core.PermissionRepairState(
        guild=guild,
        actor_id=1,
        last_token="abc123",
    )
    changed_view = core.TargetPermissionRepairView(changed_state)
    assert _component(changed_view, "dank_permission_repair:undo").disabled is False


def test_failed_noop_repair_does_not_create_meaningless_undo_token(monkeypatch) -> None:
    guild = SimpleNamespace(id=77)
    target = SimpleNamespace(id=10, name="verification", mention="<#10>")
    member = object()
    audit = core.TargetPermissionAudit(
        guild_id=77,
        target_id=10,
        target_name="verification",
        feature="general",
        mode="minimum",
        required=["send_messages"],
        missing=["send_messages"],
        repairable_missing=[],
        blockers=["manual Discord permission fix required"],
    )

    monkeypatch.setattr(core, "_bot_member", lambda _guild: member)
    monkeypatch.setattr(
        core,
        "audit_targets",
        lambda *_args, **_kwargs: [audit],
    )

    recorded: list[tuple[str, dict]] = []

    async def record(**kwargs):
        recorded.append((kwargs["event_type"], dict(kwargs["metadata"])))
        return True

    monkeypatch.setattr(core, "_record_repair_event", record)

    result = asyncio.run(
        core.apply_target_repair(
            guild,
            target,
            actor_id=99,
            feature="general",
            mode="minimum",
            include_children=False,
        )
    )

    assert result.changed_targets == []
    assert result.failed_targets
    assert result.token == ""
    assert result.notes == [
        "No overwrite changed, so no undo snapshot was created."
    ]
    assert recorded and recorded[0][0] == "permission_repair_attempt"
    assert "token" not in recorded[0][1]
