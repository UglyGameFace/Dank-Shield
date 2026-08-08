from __future__ import annotations

import discord
from discord import app_commands

from stoney_verify import commands as commands_module
from stoney_verify.commands_ext.public_exit_compact_surface import (
    register_compact_exit_card_commands,
)
from stoney_verify.commands_ext.public_setup_group import dank_group


async def _placeholder(interaction: discord.Interaction) -> None:
    _ = interaction


def _root_names() -> set[str]:
    return {
        str(getattr(command, "name", ""))
        for command in commands_module.bot.tree.get_commands(guild=None)
    }


def _dank_children() -> set[str]:
    return {
        str(getattr(command, "name", ""))
        for command in dank_group.commands
    }


def test_final_surface_reasserts_after_additive_registrar_drift() -> None:
    # Simulate a later/duplicate registration pass rebuilding shortcuts that the
    # final product intentionally hides. The final registrar must heal the tree
    # every time; a one-time installed flag is not enough.
    dank_group.add_command(
        app_commands.Command(
            name="status",
            description="temporary stale shortcut",
            callback=_placeholder,
        )
    )
    commands_module.bot.tree.add_command(
        app_commands.Command(
            name="ticket-panel",
            description="temporary stale root",
            callback=_placeholder,
        )
    )
    assert "status" in _dank_children()
    assert "ticket-panel" in _root_names()

    register_compact_exit_card_commands(commands_module.bot, commands_module.bot.tree)

    assert _dank_children() == {"home", "upload"}
    assert "ticket-panel" not in _root_names()
    assert {
        name for name in _root_names() if name != "View Dank Profile"
    } == {"dank", "mod", "ticket", "tickets", "verify"}
