from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import discord
import pytest

from stoney_verify.commands_ext.public_role_center import (
    RoleEditorHomeView,
    RolesProfilesView,
    _actor_can_manage_roles,
    _config_dependency_labels,
    _parse_bool,
    _parse_colour,
    _permission_groups,
    _role_dependencies,
)
from stoney_verify.ui.picker import DankRoleSelect


ROOT = Path(__file__).resolve().parents[1]
ROLE_CENTER = ROOT / "stoney_verify/commands_ext/public_role_center.py"
COMPACT_HOME = ROOT / "stoney_verify/commands_ext/public_command_surface_v2.py"
COMPAT_HOME = ROOT / "stoney_verify/commands_ext/public_command_hub.py"
ANTINUKE_PROVENANCE = ROOT / "stoney_verify/anti_nuke_self_action_runtime.py"
PROFILE_ROLES = ROOT / "stoney_verify/commands_ext/public_self_roles_group.py"


def _labels(view: discord.ui.View) -> set[str]:
    return {
        str(getattr(item, "label", "") or "")
        for item in view.children
        if str(getattr(item, "label", "") or "")
    }


def test_normal_members_never_receive_server_role_admin_controls() -> None:
    labels = _labels(
        RolesProfilesView(
            1,
            staff=False,
            role_manager=False,
            setup_manager=False,
        )
    )
    assert {"My Profile", "Profile Tags & Cosmetics", "Dank Shield Home"} <= labels
    assert "Member Role Manager" not in labels
    assert "Profile Builder" not in labels
    assert "Server Role Editor" not in labels
    assert "Create Role" not in labels
    assert "Role Health" not in labels


def test_staff_without_manage_roles_still_cannot_see_role_definition_mutations() -> None:
    labels = _labels(
        RolesProfilesView(
            1,
            staff=True,
            role_manager=False,
            setup_manager=True,
        )
    )
    assert {"Member Role Manager", "Profile Builder"} <= labels
    assert "Server Role Editor" not in labels
    assert "Create Role" not in labels
    assert "Role Health" not in labels


def test_manage_roles_surface_exposes_server_role_editor() -> None:
    labels = _labels(
        RolesProfilesView(
            1,
            staff=True,
            role_manager=True,
            setup_manager=True,
        )
    )
    assert {
        "Server Role Editor",
        "Create Role",
        "Role Health",
        "Member Role Manager",
        "Profile Builder",
    } <= labels

    editor = RoleEditorHomeView(1)
    assert any(isinstance(item, DankRoleSelect) for item in editor.children)
    assert {"Create Role", "Role Health", "Roles & Profiles"} <= _labels(editor)


def test_owner_role_authority_does_not_depend_on_member_cache_shape() -> None:
    guild = SimpleNamespace(owner_id=123, owner=None)
    assert _actor_can_manage_roles(guild, SimpleNamespace(id=123))
    assert not _actor_can_manage_roles(guild, SimpleNamespace(id=456))


def test_dependency_scan_finds_role_ids_without_confusing_unrelated_config() -> None:
    role_id = 123456789012345678
    config = {
        "staff_role_id": str(role_id),
        "profile_cosmetic_role_ids": ["7", str(role_id)],
        "ticket_category_id": str(role_id),
        "nested_role_settings": {"protected": [str(role_id)]},
        "verification_settings": {"verified_role_id": str(role_id)},
        "unrelated": str(role_id),
    }
    labels = _config_dependency_labels(config, role_id)
    assert "Staff Role" in labels
    assert "Profile Cosmetic Roles" in labels
    assert "Nested Role Settings" in labels
    assert "Verified Role" in labels
    assert all("Ticket Category" not in label for label in labels)
    assert all(label != "Unrelated" for label in labels)



def test_role_dependencies_include_spam_guard_role_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from stoney_verify import guild_config, spam_guard

    role_id = 123456789012345678

    async def fake_get_guild_config(guild_id: int, *, refresh: bool = False):
        assert guild_id == 55
        assert refresh is True
        return {"verified_role_id": "999999999999999999"}

    async def fake_get_spam_settings(guild_id: int):
        assert guild_id == 55
        return {
            "quarantine_role_id": str(role_id),
            "exempt_role_ids": ["111111111111111111", str(role_id)],
        }

    monkeypatch.setattr(guild_config, "get_guild_config", fake_get_guild_config)
    monkeypatch.setattr(spam_guard, "get_spam_settings", fake_get_spam_settings)
    monkeypatch.setattr(spam_guard, "_SETTINGS_LAST_DIAG_BY_GUILD", {55: {"status": "ok"}})

    dependencies = asyncio.run(
        _role_dependencies(
            SimpleNamespace(id=55),
            SimpleNamespace(id=role_id),
        )
    )
    assert "Spam Guard · Quarantine Role" in dependencies
    assert "Spam Guard · Exempt Roles" in dependencies


def test_role_dependencies_fail_closed_when_spam_guard_cannot_be_verified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from stoney_verify import guild_config, spam_guard

    async def fake_get_guild_config(guild_id: int, *, refresh: bool = False):
        return {}

    async def fake_get_spam_settings(guild_id: int):
        return {}

    monkeypatch.setattr(guild_config, "get_guild_config", fake_get_guild_config)
    monkeypatch.setattr(spam_guard, "get_spam_settings", fake_get_spam_settings)
    monkeypatch.setattr(
        spam_guard,
        "_SETTINGS_LAST_DIAG_BY_GUILD",
        {55: {"status": "unavailable", "source": "defaults"}},
    )

    dependencies = asyncio.run(
        _role_dependencies(
            SimpleNamespace(id=55),
            SimpleNamespace(id=123456789012345678),
        )
    )
    assert "Spam Guard role settings could not be verified" in dependencies

def test_boolean_and_colour_inputs_fail_closed() -> None:
    assert _parse_bool("yes", field="Mentionable") is True
    assert _parse_bool("OFF", field="Mentionable") is False
    with pytest.raises(ValueError):
        _parse_bool("sometimes", field="Mentionable")

    current = discord.Colour(0x112233)
    assert _parse_colour("", current=current).value == 0x112233
    assert _parse_colour("#5865F2", current=current).value == 0x5865F2
    assert _parse_colour("default", current=current).value == 0
    with pytest.raises(ValueError):
        _parse_colour("#nothex", current=current)


def test_permission_groups_cover_each_current_discord_permission_once() -> None:
    fake_role = SimpleNamespace(permissions=discord.Permissions.all())
    groups = _permission_groups(fake_role)
    flattened = [name for _key, _label, names in groups for name in names]
    current = [name for name, _enabled in fake_role.permissions]
    assert len(flattened) == len(set(flattened))
    assert set(flattened) == set(current)
    assert all(1 <= len(names) <= 25 for _key, _label, names in groups)


def test_role_mutations_recheck_live_authority_and_destructive_dependencies() -> None:
    source = ROLE_CENTER.read_text(encoding="utf-8")
    assert source.count("await _require_role_manager(interaction)") >= 10
    assert 'async with _role_action_lock(guild.id, 0, "create")' in source
    for action in ("appearance", "permissions", "position", "duplicate", "delete"):
        assert f'"{action}")' in source
    assert "fresh_dependencies = await _role_dependencies(guild, fresh)" in source
    assert "await fresh.delete(" in source
    assert source.index("fresh_dependencies = await _role_dependencies(guild, fresh)") < source.index(
        "await fresh.delete("
    )
    assert "Confirm Administrator Permission" in source
    assert 'label="Type the exact role name to confirm"' in source
    assert 'title="Confirm Role Deletion"' in source


def test_both_roles_and_profiles_doorways_use_one_authoritative_center() -> None:
    compact = COMPACT_HOME.read_text(encoding="utf-8")
    compact_start = compact.index('if key == "roles_profiles":')
    compact_end = compact.index('if key == "profile_builder":', compact_start)
    compact_route = compact[compact_start:compact_end]
    assert "open_roles_profiles_center(interaction)" in compact_route
    assert "_post_profile_builder(interaction" not in compact_route

    compat = COMPAT_HOME.read_text(encoding="utf-8")
    compat_start = compat.index('label="Roles & Profiles"')
    compat_end = compat.index('label="Logs & Activity"', compat_start)
    compat_route = compat[compat_start:compat_end]
    assert "open_roles_profiles_center(interaction)" in compat_route
    assert "_post_profile_builder(interaction" not in compat_route



def test_empty_cosmetic_guidance_uses_the_public_roles_profiles_path() -> None:
    source = PROFILE_ROLES.read_text(encoding="utf-8")
    assert '/dank home` → **Roles & Profiles** → **Profile Builder**' in source
    assert 'Staff can add them in `/dank profile builder`' not in source

def test_role_editor_mutations_remain_inside_antinuke_self_action_provenance() -> None:
    source = ANTINUKE_PROVENANCE.read_text(encoding="utf-8")
    assert 'return _spec(("role_create",), gid)' in source
    assert 'return _spec(("role_update",), gid)' in source
    assert '"role_update" if method == "PATCH" else "role_delete"' in source
    editor = ROLE_CENTER.read_text(encoding="utf-8")
    assert "await guild.create_role(" in editor
    assert "await fresh.edit(" in editor
    assert "await fresh.delete(" in editor
    assert "anti_nuke" not in editor.casefold()
