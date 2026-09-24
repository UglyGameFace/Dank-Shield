from __future__ import annotations

import asyncio
import inspect

import pytest

from stoney_verify.commands_ext import public_protection_center as protection


def test_antinuke_trust_lists_save_bot_targets_separately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_save(guild_id: int, patch):
        captured["guild_id"] = int(guild_id)
        captured["patch"] = dict(patch)
        return dict(patch)

    monkeypatch.setattr(protection, "save_antinuke_settings", fake_save)

    users, roles, bots = asyncio.run(
        protection._save_antinuke_trust_lists(  # noqa: SLF001
            123,
            trusted_users="11111, 22222, 11111",
            trusted_roles="33333",
            trusted_bots="44444, 55555, 44444",
        )
    )

    assert users == [11111, 22222]
    assert roles == [33333]
    assert bots == [44444, 55555]
    assert captured == {
        "guild_id": 123,
        "patch": {
            "antinuke_trusted_user_ids": [11111, 22222],
            "antinuke_trusted_role_ids": [33333],
            "antinuke_trusted_bot_ids": [44444, 55555],
        },
    }


def test_antinuke_trust_modal_keeps_role_state_out_of_manual_role_ids() -> None:
    source = inspect.getsource(protection.AntiNukeTrustedIdsModal)

    assert 'label="Trusted inviter user IDs"' in source
    assert 'label="Trusted inviter role IDs"' not in source
    assert 'label="Pre-approved bot IDs"' in source
    assert 'current["antinuke_trusted_role_ids"]' in source
    assert "self.add_item(self.trusted_bots)" in source


def test_native_trusted_role_selectors_are_add_remove_controls() -> None:
    add_source = inspect.getsource(
        protection.AntiNukeTrustedRoleAddSelect
    )
    remove_source = inspect.getsource(
        protection.AntiNukeTrustedRoleRemoveSelect
    )
    view_source = inspect.getsource(protection.AntiNukeTrustManagerView)

    assert issubclass(
        protection.AntiNukeTrustedRoleAddSelect,
        protection.discord.ui.RoleSelect,
    )
    assert issubclass(
        protection.AntiNukeTrustedRoleRemoveSelect,
        protection.discord.ui.RoleSelect,
    )
    assert "antinuke_trusted_roles_add" in add_source
    assert "remove=False" in add_source
    assert "antinuke_trusted_roles_remove" in remove_source
    assert "remove=True" in remove_source
    assert "Edit User/Bot IDs" in view_source


def test_native_trusted_role_update_merges_roles_and_blocks_everyone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_get(_guild_id: int):
        return {
            "antinuke_trusted_role_ids": [33333, 99999],
            "antinuke_trusted_user_ids": [],
            "antinuke_trusted_bot_ids": [],
        }

    async def fake_save(guild_id: int, patch):
        captured["guild_id"] = guild_id
        captured["patch"] = dict(patch)
        return {
            "antinuke_trusted_role_ids": list(
                patch["antinuke_trusted_role_ids"]
            )
        }

    monkeypatch.setattr(
        protection,
        "get_antinuke_settings",
        fake_get,
    )
    monkeypatch.setattr(
        protection,
        "save_antinuke_settings",
        fake_save,
    )

    roles = asyncio.run(
        protection._update_antinuke_trusted_roles(  # noqa: SLF001
            123,
            [44444, 99999],
            blocked_role_ids={99999},
        )
    )

    assert roles == [33333, 44444]
    assert captured == {
        "guild_id": 123,
        "patch": {"antinuke_trusted_role_ids": [33333, 44444]},
    }
