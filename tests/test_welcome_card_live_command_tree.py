from __future__ import annotations

from typing import Any

from discord import app_commands

from stoney_verify import commands as commands_module
from stoney_verify.commands_ext.public_command_hub import (
    DANK_PAYLOAD_SAFETY_LIMIT,
    dank_payload_size,
)
from stoney_verify.commands_ext.public_exit_compact_surface import (
    register_compact_exit_card_commands,
)
from stoney_verify.commands_ext.public_setup_group import dank_group


EXPECTED_DANK_CHILDREN = {"home", "purge", "upload"}
EXPECTED_GLOBAL_ROOTS = {"dank", "mod", "ticket", "tickets", "verify"}


def _child_names(group: Any) -> set[str]:
    return {
        str(getattr(command, "name", ""))
        for command in getattr(group, "commands", [])
        if getattr(command, "name", "")
    }


def _final_imported_tree() -> int:
    # commands.py performs canonical registration, the legacy compact pass, Exit
    # compatibility registration, and finally DS-COMMAND-UX-024 compaction.
    # Re-running the Exit registrar proves the final surface is idempotent and
    # cannot resurrect /dank welcome after it has been intentionally retired.
    return register_compact_exit_card_commands(
        commands_module.bot,
        commands_module.bot.tree,
    )


def test_final_compacted_tree_is_small_and_idempotent() -> None:
    size = _final_imported_tree()

    assert dank_group.get_command("welcome") is None
    assert _child_names(dank_group) == EXPECTED_DANK_CHILDREN
    assert size == dank_payload_size(commands_module.bot.tree)
    assert size <= DANK_PAYLOAD_SAFETY_LIMIT

    roots = {
        str(getattr(command, "name", ""))
        for command in commands_module.bot.tree.get_commands(guild=None)
        if getattr(command, "name", "") != "View Dank Profile"
    }
    assert roots == EXPECTED_GLOBAL_ROOTS


def test_final_fast_doorways_are_commands_not_subcommand_groups() -> None:
    _final_imported_tree()
    for name in ("mod", "ticket", "tickets", "verify"):
        command = commands_module.bot.tree.get_command(name, guild=None)
        assert isinstance(command, app_commands.Command)
        assert not isinstance(command, app_commands.Group)

    assert commands_module.bot.tree.get_command("ticket-intake", guild=None) is None
    assert commands_module.bot.tree.get_command("ticket-category", guild=None) is None
    assert commands_module.bot.tree.get_command("ticket-panel", guild=None) is None


def test_dank_upload_is_the_only_attachment_command_doorway() -> None:
    _final_imported_tree()
    upload = dank_group.get_command("upload")
    assert isinstance(upload, app_commands.Command)
    assert getattr(upload.callback, "__module__", "") == (
        "stoney_verify.commands_ext.public_command_surface_v2"
    )
    params = getattr(upload, "_params", {})
    assert set(params) == {"asset", "file"}


def test_lifecycle_studios_remain_reachable_from_home_not_subcommands() -> None:
    _final_imported_tree()
    home = dank_group.get_command("home")
    assert isinstance(home, app_commands.Command)
    assert getattr(home.callback, "__module__", "") == (
        "stoney_verify.commands_ext.public_command_surface_v2"
    )
    assert dank_group.get_command("welcome") is None
