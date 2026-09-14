from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import discord
import pytest

from stoney_verify.command_runtime import (
    DankAutoShardedBot,
    DankBot,
    DankCommandTree,
    command_surface_hash,
    create_discord_bot,
    normalize_command_runtime_env,
    remember_global_sync,
    should_skip_unchanged_global_sync,
    validate_public_command_surface,
)
from stoney_verify.command_surface_contract import (
    PUBLIC_DANK_CHILDREN,
    PUBLIC_GLOBAL_COMMAND_NAMES,
)
from stoney_verify.startup_diagnostics import EXPECTED_STARTUP_OWNER_MODULES


ROOT = Path(__file__).resolve().parents[1]


class _FakeCommand:
    def __init__(self, name: str, *, children: tuple[str, ...] = ()) -> None:
        self.name = name
        self.description = f"{name} description"
        self.commands = [_FakeCommand(child) for child in children]

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
        }
        if self.commands:
            payload["options"] = [child.to_dict() for child in self.commands]
        return payload


class _FakeTree:
    def __init__(self, names: tuple[str, ...] = PUBLIC_GLOBAL_COMMAND_NAMES) -> None:
        self._commands: list[_FakeCommand] = []
        for name in names:
            if name == "dank":
                self._commands.append(
                    _FakeCommand("dank", children=tuple(sorted(PUBLIC_DANK_CHILDREN)))
                )
            else:
                self._commands.append(_FakeCommand(name))

    def get_commands(self, *, guild=None):  # type: ignore[no-untyped-def]
        _ = guild
        return list(self._commands)

    def get_command(self, name: str, *, guild=None):  # type: ignore[no-untyped-def]
        _ = guild
        return next((command for command in self._commands if command.name == name), None)


def test_legacy_command_guard_imports_do_not_patch_discord_classes() -> None:
    code = r'''
from discord import app_commands
from discord.ext import commands

bot_cls = commands.Bot
add_command = app_commands.CommandTree.add_command
sync = app_commands.CommandTree.sync

import stoney_verify.startup_guards.command_safety
import stoney_verify.startup_guards.global_command_sync
import stoney_verify.startup_guards.auto_shard
import stoney_verify.startup_guards.command_scope_dedupe

assert commands.Bot is bot_cls
assert app_commands.CommandTree.add_command is add_command
assert app_commands.CommandTree.sync is sync
'''
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def test_native_command_runtime_replaces_retired_startup_owner_expectations() -> None:
    assert "stoney_verify.command_runtime" in EXPECTED_STARTUP_OWNER_MODULES
    assert "stoney_verify.startup_guards.command_safety" not in EXPECTED_STARTUP_OWNER_MODULES
    assert "stoney_verify.startup_guards.auto_shard" not in EXPECTED_STARTUP_OWNER_MODULES
    assert "stoney_verify.startup_guards.global_command_sync" not in EXPECTED_STARTUP_OWNER_MODULES
    assert "stoney_verify.startup_guards.command_scope_dedupe" not in EXPECTED_STARTUP_OWNER_MODULES


def test_native_bot_constructor_owns_tree_and_shard_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_AUTO_SHARD", "false")
    monkeypatch.delenv("DISCORD_SHARD_COUNT", raising=False)
    bot = create_discord_bot(
        command_prefix="!",
        intents=discord.Intents.none(),
        help_command=None,
    )
    assert isinstance(bot, DankBot)
    assert type(bot.tree) is DankCommandTree
    asyncio.run(bot.close())

    monkeypatch.setenv("DISCORD_AUTO_SHARD", "true")
    monkeypatch.setenv("DISCORD_SHARD_COUNT", "2")
    sharded = create_discord_bot(
        command_prefix="!",
        intents=discord.Intents.none(),
        help_command=None,
    )
    assert isinstance(sharded, DankAutoShardedBot)
    assert type(sharded.tree) is DankCommandTree
    assert int(sharded.shard_count or 0) == 2
    # AutoShardedBot.close() assumes the internal shard queue was created by
    # startup. This constructor test never starts/connects the client, so calling
    # close() here would test an invalid discord.py lifecycle rather than Dank
    # Shield ownership.


def test_public_surface_validation_is_menu_first_and_fail_closed() -> None:
    tree = _FakeTree()
    snapshot = validate_public_command_surface(tree)
    assert snapshot["global_count"] == len(PUBLIC_GLOBAL_COMMAND_NAMES)
    assert set(snapshot["global_names"]) == set(PUBLIC_GLOBAL_COMMAND_NAMES)

    missing_verify = _FakeTree(tuple(name for name in PUBLIC_GLOBAL_COMMAND_NAMES if name != "verify"))
    with pytest.raises(RuntimeError, match="public command surface drifted"):
        validate_public_command_surface(missing_verify)

    wrong_children = _FakeTree()
    dank = wrong_children.get_command("dank")
    assert dank is not None
    dank.commands = [_FakeCommand("home"), _FakeCommand("setup")]
    with pytest.raises(RuntimeError, match="menu-first surface drifted"):
        validate_public_command_surface(wrong_children)


def test_unchanged_global_sync_state_is_real_behavior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_file = tmp_path / "command-sync.json"
    monkeypatch.setenv("DANK_COMMAND_SYNC_STATE_FILE", str(state_file))
    monkeypatch.setenv("DANK_SKIP_UNCHANGED_GLOBAL_SYNC", "true")
    monkeypatch.delenv("DANK_FORCE_COMMAND_SYNC_ON_BOOT", raising=False)

    tree = _FakeTree()
    skip, surface_hash = should_skip_unchanged_global_sync(tree, public_scope=True)
    assert skip is False
    assert surface_hash == command_surface_hash(tree)

    remember_global_sync(surface_hash, public_scope=True)
    skip_after, same_hash = should_skip_unchanged_global_sync(tree, public_scope=True)
    assert skip_after is True
    assert same_hash == surface_hash

    changed = _FakeTree(PUBLIC_GLOBAL_COMMAND_NAMES + ("unexpected",))
    changed_skip, changed_hash = should_skip_unchanged_global_sync(changed, public_scope=True)
    assert changed_skip is False
    assert changed_hash != surface_hash


def test_beta_guild_sync_default_is_normalized_without_overriding_explicit_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DANK_SYNC_BETA_GUILD_COMMANDS", raising=False)
    normalize_command_runtime_env()
    assert os.environ["DANK_SYNC_BETA_GUILD_COMMANDS"] == "false"

    monkeypatch.setenv("DANK_SYNC_BETA_GUILD_COMMANDS", "true")
    normalize_command_runtime_env()
    assert os.environ["DANK_SYNC_BETA_GUILD_COMMANDS"] == "true"
