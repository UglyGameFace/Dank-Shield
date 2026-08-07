from __future__ import annotations

from typing import Any

from discord import app_commands

from stoney_verify import commands as commands_module
from stoney_verify.commands_ext.public_command_hub import (
    DANK_PAYLOAD_SAFETY_LIMIT,
    compact_public_dank_surface,
    dank_payload_size,
)
from stoney_verify.commands_ext.public_setup_group import dank_group


EXPECTED_COMPACT_WELCOME_COMMANDS = {
    "open",
    "card-studio",
    "card-preview",
    "card-upload",
    "card-font-upload",
    "card-font-clear",
}


def _child_names(group: Any) -> set[str]:
    return {
        str(getattr(command, "name", ""))
        for command in getattr(group, "commands", [])
        if getattr(command, "name", "")
    }


def _compact_imported_tree() -> int:
    # commands.py performs canonical registration and compaction during import.
    # Re-running the idempotent compactor validates the actual production tree
    # rather than calling a nonexistent legacy registration helper.
    return compact_public_dank_surface(
        commands_module.bot,
        commands_module.bot.tree,
    )


def test_final_compacted_tree_keeps_the_complete_studio_entry_surface() -> None:
    size = _compact_imported_tree()

    attached = dank_group.get_command("welcome")
    assert isinstance(attached, app_commands.Group)
    assert _child_names(attached) == EXPECTED_COMPACT_WELCOME_COMMANDS
    assert size == dank_payload_size(commands_module.bot.tree)
    assert size <= DANK_PAYLOAD_SAFETY_LIMIT


def test_compacted_studio_commands_use_canonical_callbacks() -> None:
    _compact_imported_tree()
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
