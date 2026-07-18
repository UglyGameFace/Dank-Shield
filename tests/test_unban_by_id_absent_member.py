from __future__ import annotations

import asyncio
import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from stoney_verify.commands_ext import common
from stoney_verify.commands_ext import moderation
from stoney_verify.commands_ext import (
    public_ban_unban_patch,
)
from stoney_verify.commands_ext import (
    public_mod_ban_toggle_patch,
)


ROOT = Path(__file__).resolve().parents[1]

ACTIVE_COMMAND_PATH = (
    ROOT
    / "stoney_verify"
    / "commands_ext"
    / "public_ban_unban_patch.py"
)

MOD_GROUP_PATH = (
    ROOT
    / "stoney_verify"
    / "commands_ext"
    / "public_mod_group.py"
)

USER_ID = 123456789012345678


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


class BannedUserGuild:
    def __init__(self) -> None:
        self.fetch_ban_ids: list[int] = []
        self.member_lookups: list[str] = []

        self.banned_user = SimpleNamespace(
            id=USER_ID,
            name="ReturningUser",
            discriminator="0",
        )

        self.ban_entry = SimpleNamespace(
            user=self.banned_user,
            reason="Previous moderation action",
        )

    async def fetch_ban(
        self,
        target: Any,
    ) -> Any:
        self.fetch_ban_ids.append(
            int(target.id)
        )
        return self.ban_entry

    def get_member(
        self,
        user_id: int,
    ) -> None:
        self.member_lookups.append(
            f"get:{user_id}"
        )
        raise AssertionError(
            "Banned users must not require get_member"
        )

    async def fetch_member(
        self,
        user_id: int,
    ) -> None:
        self.member_lookups.append(
            f"fetch:{user_id}"
        )
        raise AssertionError(
            "Banned users must not require fetch_member"
        )


class CurrentMemberGuild:
    def __init__(self) -> None:
        self.member = SimpleNamespace(
            id=USER_ID,
            name="CurrentMember",
        )
        self.ban_checks = 0
        self.member_checks = 0

    async def fetch_ban(
        self,
        target: Any,
    ) -> None:
        self.ban_checks += 1
        raise RuntimeError("not banned")

    def get_member(
        self,
        user_id: int,
    ) -> Any:
        self.member_checks += 1
        assert int(user_id) == USER_ID
        return self.member

    async def fetch_member(
        self,
        user_id: int,
    ) -> Any:
        raise AssertionError(
            "fetch_member should not run when cached"
        )


def test_parser_accepts_raw_mention_and_log_text() -> None:
    values = (
        str(USER_ID),
        f"<@{USER_ID}>",
        f"<@!{USER_ID}>",
        f"User ID: {USER_ID}",
        f"ReturningUser — {USER_ID}",
        f"Target (`{USER_ID}`)",
    )

    for value in values:
        assert (
            common.parse_member_id_from_target(value)
            == USER_ID
        )


def test_parser_rejects_name_without_id() -> None:
    assert (
        common.parse_member_id_from_target(
            "ReturningUser"
        )
        == 0
    )


def test_canonical_resolver_is_ban_first() -> None:
    guild = BannedUserGuild()

    user_id, member, entry = run(
        moderation._resolve_ban_toggle_target(
            guild,
            f"<@!{USER_ID}>",
        )
    )

    assert user_id == USER_ID
    assert member is None
    assert entry is guild.ban_entry
    assert guild.fetch_ban_ids == [USER_ID]
    assert guild.member_lookups == []


def test_public_resolver_is_ban_first() -> None:
    guild = BannedUserGuild()

    user_id, member, entry = run(
        public_mod_ban_toggle_patch._resolve_ban_target(
            guild,
            f"User ID: {USER_ID}",
        )
    )

    assert user_id == USER_ID
    assert member is None
    assert entry is guild.ban_entry
    assert guild.fetch_ban_ids == [USER_ID]
    assert guild.member_lookups == []


def test_active_ban_unban_uses_public_resolver() -> None:
    assert (
        public_ban_unban_patch._resolve_ban_target
        is public_mod_ban_toggle_patch._resolve_ban_target
    )

    guild = BannedUserGuild()

    user_id, member, entry = run(
        public_ban_unban_patch._resolve_ban_target(
            guild,
            str(USER_ID),
        )
    )

    assert user_id == USER_ID
    assert member is None
    assert entry is guild.ban_entry
    assert guild.member_lookups == []


def test_current_member_still_resolves_for_ban() -> None:
    guild = CurrentMemberGuild()

    user_id, member, entry = run(
        public_mod_ban_toggle_patch._resolve_ban_target(
            guild,
            str(USER_ID),
        )
    )

    assert user_id == USER_ID
    assert member is guild.member
    assert entry is None
    assert guild.ban_checks >= 1
    assert guild.member_checks == 1


def test_active_command_unbans_ban_entry_user() -> None:
    source = ACTIVE_COMMAND_PATH.read_text(
        encoding="utf-8"
    )
    tree = ast.parse(
        source,
        filename=str(ACTIVE_COMMAND_PATH),
    )

    command = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_ban_unban_command"
    )

    body = (
        ast.get_source_segment(
            source,
            command,
        )
        or ""
    )

    assert (
        "_resolve_ban_target(guild, member)"
        in body
    )
    assert 'selected == "unban"' in body
    assert (
        'getattr(ban_entry, "user", None)'
        in body
    )
    assert "await guild.unban(" in body


def test_grouped_command_uses_canonical_resolver() -> None:
    source = MOD_GROUP_PATH.read_text(
        encoding="utf-8"
    )

    assert (
        "_resolve_ban_toggle_target(guild, member)"
        in source
    )
    assert "await guild.unban(" in source
