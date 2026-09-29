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


def test_role_callback_exposes_only_member_and_role_shortcuts() -> None:
    signature = inspect.signature(open_role_command)
    assert list(signature.parameters) == ["interaction", "member", "role"]
    assert signature.parameters["member"].default is None
    assert signature.parameters["role"].default is None


def test_member_shortcut_reuses_existing_guarded_member_center() -> None:
    source = (ROOT / "stoney_verify/commands_ext/member_command_center.py").read_text(encoding="utf-8")
    start = source.index("async def open_member_target")
    end = source.index("async def open_member_command_center", start)
    block = source[start:end]

    assert "require_review(interaction)" in block
    assert "MemberActionView(" in block
    assert "member_detail_embed(member, None)" in block
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

    assert "STONER_ROLE_KEY" in block
    assert "SESH_PING_ROLE_KEY" in block
    assert "PROFILE_COSMETIC_ROLE_IDS_KEY" in block
    assert "PROFILE_CATEGORIES" in block
    assert "_profile_cosmetic_role_blocker" in block
    assert "not configured as a member self-service role" in block


def test_direct_self_role_card_is_small_and_routes_back_to_canonical_center() -> None:
    labels = _labels(SelfServiceRoleView(1, 123))
    assert labels == {"Add / Remove Role", "Roles & Profiles"}


def test_self_role_toggle_rechecks_live_mapping_under_member_lock() -> None:
    source = (ROOT / "stoney_verify/commands_ext/public_role_center.py").read_text(encoding="utf-8")
    start = source.index("class SelfServiceRoleView")
    end = source.index("async def _open_direct_role", start)
    block = source[start:end]

    assert "_self_service_role_lock(guild.id, member.id)" in block
    assert "_self_service_role_kind(guild, role)" in block
    assert "get_guild_config(int(guild.id), refresh=True)" in block
    assert "Select the configured Stoner role before enabling Sesh Pings." in block
    assert "remove_roles(sesh_role" in block
