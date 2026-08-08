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


EXPECTED_COMPACT_WELCOME_COMMANDS = {
    "open",
    "card-studio",
    "card-preview",
    "card-upload",
    "card-font-upload",
    "card-font-clear",
    "exit-card-studio",
    "exit-card-preview",
    "exit-card-upload",
}


def _child_names(group: Any) -> set[str]:
    return {
        str(getattr(command, "name", ""))
        for command in getattr(group, "commands", [])
        if getattr(command, "name", "")
    }


def _final_imported_tree() -> int:
    # commands.py performs canonical registration, compaction, then the guarded
    # Exit Studio extension during import. Re-running the Exit registrar proves
    # that the final production surface is idempotent and still under the same
    # payload safety ceiling.
    return register_compact_exit_card_commands(
        commands_module.bot,
        commands_module.bot.tree,
    )


def test_final_compacted_tree_keeps_both_lifecycle_studios() -> None:
    size = _final_imported_tree()

    attached = dank_group.get_command("welcome")
    assert isinstance(attached, app_commands.Group)
    assert _child_names(attached) == EXPECTED_COMPACT_WELCOME_COMMANDS
    assert size == dank_payload_size(commands_module.bot.tree)
    assert size <= DANK_PAYLOAD_SAFETY_LIMIT


def test_compacted_lifecycle_commands_use_canonical_callbacks() -> None:
    _final_imported_tree()
    attached = dank_group.get_command("welcome")
    assert isinstance(attached, app_commands.Group)

    callbacks = {
        command.name: getattr(command.callback, "__module__", "")
        for command in attached.commands
    }
    assert callbacks["open"] == "stoney_verify.welcome_setup_ui"
    assert callbacks["card-studio"] == "stoney_verify.welcome_card_studio_ui"
    assert callbacks["card-preview"] == "stoney_verify.welcome_card_studio_ui"
    assert callbacks["card-upload"] == "stoney_verify.commands_ext.public_welcome_group"
    assert callbacks["card-font-upload"] == "stoney_verify.commands_ext.public_welcome_card_studio"
    assert callbacks["card-font-clear"] == "stoney_verify.commands_ext.public_welcome_card_studio"
    assert callbacks["exit-card-studio"] == "stoney_verify.exit_card_studio_ui"
    assert callbacks["exit-card-preview"] == "stoney_verify.exit_card_studio_ui"
    assert callbacks["exit-card-upload"] == "stoney_verify.commands_ext.public_exit_card_studio"
