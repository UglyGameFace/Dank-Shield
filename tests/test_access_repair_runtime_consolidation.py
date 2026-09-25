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


class _HashablePrincipal:
    def __init__(self, principal_id: int, *, name: str = "principal") -> None:
        self.id = principal_id
        self.name = name


class _BootstrapMember(_HashablePrincipal):
    def __init__(self) -> None:
        super().__init__(42, name="Dank Shield")
        self.guild_permissions = SimpleNamespace(
            administrator=False,
            manage_roles=True,
            manage_channels=True,
            view_channel=True,
            view_audit_log=True,
        )
        self.top_role = SimpleNamespace(managed=True)


class _BootstrapParent:
    def __init__(self, member: _BootstrapMember) -> None:
        self.member = member

    def overwrites_for(self, principal):
        if principal is self.member:
            return discord.PermissionOverwrite(
                manage_roles=True,
                manage_messages=True,
                view_channel=True,
            )
        return discord.PermissionOverwrite()


class _BootstrapTarget:
    def __init__(self, member: _BootstrapMember) -> None:
        self.id = 810
        self.name = "general"
        self.mention = "<#810>"
        self.member = member
        self.category = _BootstrapParent(member)
        self.other = _HashablePrincipal(99, name="Moderator")
        self.other_overwrite = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=False,
        )
        self._bot_overwrite = discord.PermissionOverwrite()
        self.overwrites = {self.other: self.other_overwrite}
        self.edit_calls: list[dict] = []
        self.set_permission_calls: list[tuple] = []

    def permissions_for(self, principal):
        assert principal is self.member
        ow = self._bot_overwrite
        return SimpleNamespace(
            administrator=False,
            manage_roles=ow.manage_roles is True,
            manage_channels=True,
            view_channel=ow.view_channel is True,
            send_messages=ow.send_messages is True,
            embed_links=ow.embed_links is True,
            attach_files=ow.attach_files is True,
            read_message_history=ow.read_message_history is True,
            manage_messages=ow.manage_messages is True,
            manage_threads=ow.manage_threads is True,
            send_messages_in_threads=ow.send_messages_in_threads is True,
            move_members=ow.move_members is True,
        )

    def overwrites_for(self, principal):
        if principal is self.member:
            return discord.PermissionOverwrite.from_pair(*self._bot_overwrite.pair())
        if principal is self.other:
            return discord.PermissionOverwrite.from_pair(*self.other_overwrite.pair())
        return discord.PermissionOverwrite()

    async def edit(self, *, overwrites, reason=None):
        self.edit_calls.append(
            {"overwrites": dict(overwrites), "reason": str(reason or "")}
        )
        self.overwrites = dict(overwrites)
        self._bot_overwrite = discord.PermissionOverwrite.from_pair(
            *self.overwrites[self.member].pair()
        )
        return self

    async def set_permissions(self, *args, **kwargs):
        self.set_permission_calls.append((args, kwargs))
        raise AssertionError("self-lockout recovery should use the bot-only bulk edit path")


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


def test_self_lockout_bootstrap_uses_parent_bot_allows_and_preserves_other_overwrites(monkeypatch) -> None:
    guild = SimpleNamespace(id=77, default_role=object())
    member = _BootstrapMember()
    target = _BootstrapTarget(member)
    monkeypatch.setattr(core, "_bot_member", lambda _guild: member)

    plan = core.build_bot_overwrite_bootstrap_plan(guild, target)

    assert plan is not None
    assert plan.overwrite.manage_roles is True
    assert plan.overwrite.manage_messages is True
    assert "manage_messages" in plan.inherited_permissions
    assert plan.overwrites[target.other].pair() == target.other_overwrite.pair()
    assert set(plan.overwrites) == {target.other, member}


def test_specific_channel_audit_treats_safe_self_unlock_as_repairable(monkeypatch) -> None:
    guild = SimpleNamespace(id=77, default_role=object())
    member = _BootstrapMember()
    target = _BootstrapTarget(member)
    monkeypatch.setattr(core, "_bot_member", lambda _guild: member)
    monkeypatch.setattr(core, "_target_supported", lambda _target: True)

    report = core.audit_target(guild, target, feature="general", mode="minimum")

    assert report.missing
    assert report.blockers == []
    assert report.can_apply is True
    assert any("without syncing unrelated" in warning for warning in report.warnings)


def test_specific_channel_apply_self_unlocks_without_set_permissions(monkeypatch) -> None:
    guild = SimpleNamespace(id=77, default_role=object())
    member = _BootstrapMember()
    target = _BootstrapTarget(member)
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
    assert result.changed_targets
    assert len(target.edit_calls) == 1
    assert target.set_permission_calls == []
    assert target._bot_overwrite.manage_roles is True
    assert target._bot_overwrite.view_channel is True
    assert target._bot_overwrite.send_messages is True
    assert target.overwrites[target.other].pair() == target.other_overwrite.pair()


def test_setup_repair_self_unlocks_same_channel_without_full_category_sync(monkeypatch) -> None:
    guild = SimpleNamespace(id=77, default_role=object())
    member = _BootstrapMember()
    target = _BootstrapTarget(member)
    desired = discord.PermissionOverwrite(
        view_channel=True,
        read_message_history=True,
        manage_threads=True,
    )

    monkeypatch.setattr(core, "_bot_member", lambda _guild: member)

    async def targets(_guild, *, include_activity_coverage=False):
        assert include_activity_coverage is True
        return [
            SimpleNamespace(
                channel=target,
                overwrites={member: desired},
            )
        ], [], [], []

    monkeypatch.setattr(setup_repair, "_build_expanded_targets", targets)

    preview = asyncio.run(
        setup_repair.preview_or_apply(
            guild,
            apply=False,
            include_activity_coverage=True,
        )
    )
    assert preview["manual_actions"] == []
    assert preview["changed"]
    assert any("Self-lockout recovery" in note for note in preview["notes"])
    assert target.edit_calls == []

    applied = asyncio.run(
        setup_repair.preview_or_apply(
            guild,
            apply=True,
            include_activity_coverage=True,
        )
    )
    assert applied["manual_actions"] == []
    assert applied["failed"] == []
    assert applied["changed"]
    assert len(target.edit_calls) == 1
    assert target._bot_overwrite.manage_roles is True
    assert target._bot_overwrite.manage_messages is True
    assert target.overwrites[target.other].pair() == target.other_overwrite.pair()


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
