from __future__ import annotations

import inspect
from pathlib import Path

import discord
from discord import app_commands

from stoney_verify.command_surface_contract import (
    PUBLIC_GLOBAL_COMMAND_COUNT,
    PUBLIC_GLOBAL_COMMAND_NAMES,
)
from stoney_verify.commands_ext.public_command_surface_v2 import _standalone
from stoney_verify.commands_ext.public_role_center import (
    RolesProfilesView,
    SelfServiceRoleView,
    open_role_command,
)


ROOT = Path(__file__).resolve().parents[1]


def _labels(view: discord.ui.View) -> set[str]:
    return {
        str(getattr(item, "label", "") or "")
        for item in view.children
        if str(getattr(item, "label", "") or "")
    }


def test_role_is_one_intentional_public_doorway() -> None:
    assert PUBLIC_GLOBAL_COMMAND_COUNT == 9
    assert PUBLIC_GLOBAL_COMMAND_NAMES == (
        "dank",
        "captions",
        "mod",
        "role",
        "ticket",
        "tickets",
        "toke",
        "verify",
        "View Dank Profile",
    )

    command = _standalone("role", "Open Roles & Profiles.", open_role_command)
    assert isinstance(command, app_commands.Command)
    assert set(getattr(command, "_params", {})) == {"member", "role"}
    role_param = command._params["role"]
    assert role_param.type == discord.AppCommandOptionType.string
    assert getattr(role_param, "autocomplete", None) is not None


def test_role_callback_exposes_only_member_and_role_shortcuts() -> None:
    signature = inspect.signature(open_role_command)
    assert list(signature.parameters) == ["interaction", "member", "role"]
    assert signature.parameters["member"].default is None
    assert signature.parameters["role"].default is None

    source = (ROOT / "stoney_verify/commands_ext/public_role_center.py").read_text(encoding="utf-8")
    assert "@app_commands.autocomplete(role=_role_name_autocomplete)" in source
    assert "resolve_role_query(interaction.guild, role_query)" in source
    assert "Optional[str] = None" in source


def test_member_shortcut_is_role_focused_and_reuses_guarded_role_actions() -> None:
    source = (ROOT / "stoney_verify/commands_ext/member_command_center.py").read_text(encoding="utf-8")
    start = source.index("class DirectMemberRoleActionView")
    end = source.index("async def open_member_command_center", start)
    block = source[start:end]

    assert "require_review(interaction)" in block
    assert "DirectMemberRoleView(" in block
    assert "DirectMemberRoleActionView(self, action=action)" in block
    assert "MemberRoleActionView" in source
    assert 'class DirectMemberRoleActionView(MemberRoleActionView):' in block
    assert '@discord.ui.button(' in block
    assert 'label="Back"' in block
    assert 'embed=_member_role_shortcut_embed(target)' in block
    assert 'label="Add Role"' in block
    assert 'label="Remove Role"' in block
    assert 'label="View Profile"' in block
    assert 'label="Full Member Panel"' in block
    assert "add_roles(" not in block
    assert "remove_roles(" not in block


def test_role_shortcut_reuses_existing_role_editor_for_role_managers() -> None:
    source = (ROOT / "stoney_verify/commands_ext/public_role_center.py").read_text(encoding="utf-8")
    start = source.index("async def _open_direct_role")
    end = source.index("@app_commands.describe", start)
    block = source[start:end]

    assert "_actor_can_manage_roles(guild, member)" in block
    assert "_role_mutation_blockers(guild, member, role)" in block
    assert "RoleDetailView(int(member.id), role.id)" in block


def test_normal_members_only_receive_existing_self_service_roles() -> None:
    source = (ROOT / "stoney_verify/commands_ext/public_role_center.py").read_text(encoding="utf-8")
    start = source.index("async def _self_service_role_kind")
    end = source.index("def _self_role_embed", start)
    block = source[start:end]

    assert "parse_community_pings" in block
    assert "community_self_service_kind" in block
    assert "PROFILE_COSMETIC_ROLE_IDS_KEY" in block
    assert "PROFILE_CATEGORIES" in block
    assert "_profile_cosmetic_role_blocker" in block
    assert "not configured as a member self-service role" in block


def test_direct_self_role_card_is_small_and_routes_back_to_canonical_center() -> None:
    labels = _labels(SelfServiceRoleView(1, 123))
    assert labels == {"Add / Remove Role", "Roles & Profiles"}


def test_role_center_separates_member_and_staff_community_pings_surfaces() -> None:
    member_labels = _labels(
        RolesProfilesView(
            1,
            staff=False,
            role_manager=False,
            setup_manager=False,
        )
    )
    assert "My Community & Pings" in member_labels
    assert "Community & Pings Manager" not in member_labels

    staff_labels = _labels(
        RolesProfilesView(
            1,
            staff=True,
            role_manager=True,
            setup_manager=True,
        )
    )
    assert "My Community & Pings" not in staff_labels
    assert "Community & Pings Manager" in staff_labels
    assert "Member Role Manager" in staff_labels
    assert "Server Role Editor" in staff_labels

    limited_staff_labels = _labels(
        RolesProfilesView(
            1,
            staff=True,
            role_manager=False,
            setup_manager=False,
        )
    )
    assert "My Community & Pings" not in limited_staff_labels
    assert "Community & Pings Manager" not in limited_staff_labels


def test_staff_community_pings_manager_reuses_canonical_manager() -> None:
    source = (ROOT / "stoney_verify/commands_ext/public_role_center.py").read_text(encoding="utf-8")
    start = source.index("class RolesProfilesView")
    end = source.index("class RoleEditorHomeView", start)
    block = source[start:end]

    assert 'label="My Community & Pings"' in block
    assert 'label="Community & Pings Manager"' in block
    assert "from .public_community_pings import open_community_ping_setup" in block
    assert "await open_community_ping_setup(interaction)" in block


def test_self_role_toggle_rechecks_live_mapping_under_member_lock() -> None:
    source = (ROOT / "stoney_verify/commands_ext/public_role_center.py").read_text(encoding="utf-8")
    start = source.index("class SelfServiceRoleView")
    end = source.index("async def _open_direct_role", start)
    block = source[start:end]

    assert "community_member_lock(guild.id, member.id)" in block
    assert "get_guild_config(int(guild.id), refresh=True)" in block
    assert "_self_service_role_kind(guild, role, config=config)" in block
    assert "parse_community_pings(config)" in block
    assert "option_for_role(community_model, int(role.id))" in block
    assert "validate_member_selection(" in block
    assert "selection_error" in block


def test_role_autocomplete_filters_choices_before_discord_returns_them() -> None:
    source = (ROOT / "stoney_verify/commands_ext/public_role_center.py").read_text(encoding="utf-8")
    start = source.index("async def _role_name_autocomplete")
    end = source.index("@app_commands.describe", start)
    block = source[start:end]

    assert "_actor_can_manage_roles(guild, member)" in block
    assert "_role_mutation_blockers(guild, member, role)" in block
    assert "get_guild_config(int(guild.id), refresh=True)" in block
    assert "_self_service_role_kind(" in block
    assert "config=config" in block
    assert "return allowed[:25]" in block
