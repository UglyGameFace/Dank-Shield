from __future__ import annotations

import discord
from discord import app_commands

from stoney_verify import commands as commands_module
from stoney_verify.command_surface_contract import PUBLIC_DANK_CHILDREN
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
    # Simulate an obsolete alias being reintroduced after public compaction.
    # Real shortcuts such as status must remain discoverable and not collide. The final registrar must heal the tree
    # every time; a one-time installed flag is not enough.
    dank_group.add_command(
        app_commands.Command(
            name="obsolete-shortcut",
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
    assert "obsolete-shortcut" in _dank_children()
    assert "ticket-panel" in _root_names()

    register_compact_exit_card_commands(commands_module.bot, commands_module.bot.tree)

    assert _dank_children() == PUBLIC_DANK_CHILDREN
    assert "obsolete-shortcut" not in _dank_children()
    assert isinstance(dank_group.get_command("status"), app_commands.Command)
    assert isinstance(dank_group.get_command("protection"), app_commands.Command)
    purge = dank_group.get_command("purge")
    assert isinstance(purge, app_commands.Group)
    assert {str(command.name) for command in purge.commands} == {"messages", "members"}
    assert "ticket-panel" not in _root_names()
    assert {
        name for name in _root_names() if name != "View Dank Profile"
    } == {"captions", "dank", "mod", "role", "ticket", "tickets", "toke", "verify"}
