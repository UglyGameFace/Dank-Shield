from __future__ import annotations

import asyncio
from types import SimpleNamespace

from stoney_verify.commands_ext import common
from stoney_verify.commands_ext import public_access_control
from stoney_verify.commands_ext import public_command_hub
from stoney_verify.commands_ext import public_diagnostics_group
from stoney_verify.commands_ext import public_design_studio
from stoney_verify.commands_ext import public_embed_group
from stoney_verify.commands_ext import public_owner_authority
from stoney_verify.commands_ext import public_setup_group
from stoney_verify.commands_ext import public_setup_overview
from stoney_verify.commands_ext import public_staff_scope


def _interaction(*, user_id: int, owner_id: int) -> SimpleNamespace:
    guild = SimpleNamespace(id=9001, owner_id=owner_id)
    user = SimpleNamespace(id=user_id, guild=guild)
    return SimpleNamespace(guild=guild, user=user)


def test_owner_identity_is_authoritative_without_member_cache_state() -> None:
    interaction = _interaction(user_id=111, owner_id=111)

    assert public_owner_authority.interaction_is_actual_guild_owner(interaction)
    assert public_owner_authority.interaction_has_manage_guild_authority(interaction)
    assert public_owner_authority.interaction_has_channel_management_authority(interaction)
    assert public_owner_authority.is_actual_guild_owner(
        interaction.user,
        interaction.guild,
    )


def test_owner_identity_falls_back_to_guild_owner_object() -> None:
    guild = SimpleNamespace(id=9001, owner_id=0, owner=SimpleNamespace(id=111))
    interaction = SimpleNamespace(
        guild=guild,
        user=SimpleNamespace(id=111),
        permissions=SimpleNamespace(administrator=False, manage_guild=False),
    )

    assert public_owner_authority.interaction_is_actual_guild_owner(interaction)
    assert public_owner_authority.interaction_has_administrator_authority(interaction)
    assert public_owner_authority.interaction_has_manage_guild_authority(interaction)


def test_resolved_interaction_administrator_does_not_require_member_cache() -> None:
    guild = SimpleNamespace(id=9001, owner_id=0, owner=None)
    interaction = SimpleNamespace(
        guild=guild,
        user=SimpleNamespace(id=111),
        permissions=SimpleNamespace(administrator=True, manage_guild=True),
    )

    assert public_owner_authority.interaction_has_administrator_authority(interaction)
    assert public_owner_authority.interaction_has_manage_guild_authority(interaction)
    assert public_staff_scope.scoped_interaction_is_staff(interaction)
    assert public_access_control.scoped_interaction_is_server_control(interaction)
    assert common._staff_check(interaction)
    assert public_command_hub._admin_or_manage(interaction)


def test_resolved_manage_channels_is_valid_channel_repair_authority_without_member_cache() -> None:
    guild = SimpleNamespace(id=9001, owner_id=0, owner=None)
    interaction = SimpleNamespace(
        guild=guild,
        user=SimpleNamespace(id=111),
        permissions=SimpleNamespace(
            administrator=False,
            manage_guild=False,
            manage_channels=True,
        ),
    )

    assert not public_owner_authority.interaction_has_manage_guild_authority(interaction)
    assert public_owner_authority.interaction_has_channel_management_authority(interaction)


def test_role_builder_doorways_accept_resolved_admin_without_member_cache(monkeypatch) -> None:
    async def scenario() -> None:
        guild = SimpleNamespace(id=9001, owner_id=0, owner=None)
        interaction = SimpleNamespace(
            guild=guild,
            user=SimpleNamespace(id=111),
            permissions=SimpleNamespace(administrator=True, manage_guild=True),
        )

        async def forbidden_reply(*args, **kwargs) -> None:
            raise AssertionError("resolved Administrator must reach Role Builder")

        monkeypatch.setattr(public_setup_group, "reply_once", forbidden_reply)

        # /dank home -> Roles & Profiles
        assert public_command_hub._admin_or_manage(interaction)

        # /dank profile builder and /dank roles ... direct entrypoints
        assert public_setup_group._admin_or_manage_guild(interaction)
        assert await public_setup_group._require_setup_permission(interaction) is True

    asyncio.run(scenario())


def test_role_builder_routes_share_central_authority_contract() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    home = (root / "stoney_verify" / "commands_ext" / "public_command_surface_v2.py").read_text(encoding="utf-8")
    roles = (root / "stoney_verify" / "commands_ext" / "public_self_roles_group.py").read_text(encoding="utf-8")

    home_start = home.index('label="Roles & Profiles"')
    home_end = home.index('label="Logs & Activity"', home_start)
    home_route = home[home_start:home_end]
    assert "_admin_or_manage(interaction)" in home_route
    assert "_post_profile_builder(interaction" in home_route

    builder_start = roles.index('@profile_group.command(name="builder"')
    builder_end = roles.index('@profile_group.command(name="view"', builder_start)
    builder_route = roles[builder_start:builder_end]
    assert "await _require_setup_permission(interaction)" in builder_route
    assert "await _post_profile_builder(interaction" in builder_route


def test_partial_non_owner_without_resolved_permissions_still_fails_closed() -> None:
    guild = SimpleNamespace(id=9001, owner_id=111, owner=SimpleNamespace(id=111))
    interaction = SimpleNamespace(
        guild=guild,
        user=SimpleNamespace(id=222),
        permissions=SimpleNamespace(administrator=False, manage_guild=False),
    )

    assert not public_owner_authority.interaction_has_administrator_authority(interaction)
    assert not public_owner_authority.interaction_has_manage_guild_authority(interaction)
    assert not public_owner_authority.interaction_has_channel_management_authority(interaction)
    assert not public_staff_scope.scoped_interaction_is_staff(interaction)
    assert not public_access_control.scoped_interaction_is_server_control(interaction)


def test_non_owner_partial_interaction_fails_closed() -> None:
    interaction = _interaction(user_id=222, owner_id=111)

    assert not public_owner_authority.interaction_is_actual_guild_owner(interaction)
    assert not public_owner_authority.interaction_has_manage_guild_authority(interaction)
    assert not public_access_control.scoped_interaction_is_server_control(interaction)
    assert not public_staff_scope.scoped_interaction_is_staff(interaction)


def test_interaction_owner_authority_does_not_require_user_guild_attachment() -> None:
    guild = SimpleNamespace(id=9001, owner_id=111)
    interaction = SimpleNamespace(
        guild=guild,
        user=SimpleNamespace(id=111),
    )

    assert public_owner_authority.interaction_is_actual_guild_owner(interaction)
    assert public_access_control.scoped_interaction_is_server_control(interaction)


def test_server_control_owner_fast_path_precedes_member_type_requirement() -> None:
    interaction = _interaction(user_id=111, owner_id=111)

    assert public_access_control.scoped_is_server_control(interaction.user)
    assert public_access_control.scoped_is_ticket_staff(interaction.user)
    assert public_access_control.scoped_interaction_is_server_control(interaction)


def test_require_server_control_never_denies_actual_owner(monkeypatch) -> None:
    async def scenario() -> None:
        interaction = _interaction(user_id=111, owner_id=111)

        async def forbidden_reply(*args, **kwargs) -> None:
            raise AssertionError("actual guild owner must not receive a denial")

        monkeypatch.setattr(public_access_control, "reply_once", forbidden_reply)

        assert await public_access_control.require_server_control(interaction) is True

    asyncio.run(scenario())


def test_server_design_accepts_actual_owner_before_member_state() -> None:
    interaction = _interaction(user_id=111, owner_id=111)

    assert public_design_studio._can_user_design(interaction)


def test_shared_public_management_gates_all_accept_partial_owner_state() -> None:
    interaction = _interaction(user_id=111, owner_id=111)

    assert common._staff_check(interaction)
    assert public_staff_scope.scoped_interaction_is_staff(interaction)
    assert public_command_hub._admin_or_manage(interaction)
    assert public_setup_group._admin_or_manage_guild(interaction)
    assert public_setup_overview._admin_or_manage_guild(interaction)
    assert public_diagnostics_group._admin_or_manage_guild(interaction)
    assert public_embed_group._admin_or_manage_guild(interaction)


def test_shared_public_management_gates_reject_partial_non_owner_state() -> None:
    interaction = _interaction(user_id=222, owner_id=111)

    assert not common._staff_check(interaction)
    assert not public_command_hub._admin_or_manage(interaction)
    assert not public_setup_group._admin_or_manage_guild(interaction)
    assert not public_setup_overview._admin_or_manage_guild(interaction)
    assert not public_diagnostics_group._admin_or_manage_guild(interaction)
    assert not public_embed_group._admin_or_manage_guild(interaction)
