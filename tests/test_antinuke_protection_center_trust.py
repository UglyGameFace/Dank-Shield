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


def test_antinuke_trust_modal_exposes_preapproved_bot_field() -> None:
    source = inspect.getsource(protection.AntiNukeTrustedIdsModal)

    assert 'label="Trusted inviter user IDs"' in source
    assert 'label="Trusted inviter role IDs"' in source
    assert 'label="Pre-approved bot IDs"' in source
    assert 'settings["antinuke_trusted_bot_ids"]' in source
    assert "self.add_item(self.trusted_bots)" in source
