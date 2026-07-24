from __future__ import annotations

from pathlib import Path

from stoney_verify.commands_ext import public_welcome_card_studio
from stoney_verify.commands_ext import public_welcome_group
from stoney_verify.commands_ext.public_setup_group import dank_group


ROOT = Path(__file__).resolve().parents[1]
MAIN = (ROOT / "main.py").read_text(encoding="utf-8")
COMMANDS = (ROOT / "stoney_verify/commands.py").read_text(encoding="utf-8")
STUDIO = (
    ROOT / "stoney_verify/commands_ext/public_welcome_card_studio.py"
).read_text(encoding="utf-8")

EXPECTED_STYLE_COMMANDS = {
    "card-font",
    "card-font-upload",
    "card-font-clear",
    "card-colors",
    "card-style",
}


def _command_names(group) -> set[str]:
    return {
        str(getattr(command, "name", ""))
        for command in getattr(group, "commands", [])
        if getattr(command, "name", "")
    }


def _register() -> None:
    public_welcome_group.register_public_welcome_group_commands(None, None)
    public_welcome_card_studio.register_public_welcome_card_studio_commands(None, None)


def test_production_entrypoint_has_no_welcome_startup_guard() -> None:
    assert "welcome_message_command_guard" not in MAIN
    assert "register_public_welcome_card_studio_commands" in COMMANDS
    assert COMMANDS.index("register_all_commands(bot, bot.tree)") < COMMANDS.index(
        "register_public_welcome_card_studio_commands(bot, bot.tree)"
    )


def test_studio_never_replaces_or_removes_commands() -> None:
    assert "remove_command(" not in STUDIO
    assert "_replace_existing_command" not in STUDIO
    assert "monkey" in STUDIO.lower()


def test_live_welcome_group_contains_all_style_commands() -> None:
    _register()
    assert EXPECTED_STYLE_COMMANDS <= _command_names(public_welcome_group.welcome_group)


def test_dank_tree_uses_the_same_populated_welcome_group() -> None:
    _register()
    attached = dank_group.get_command("welcome")
    assert attached is public_welcome_group.welcome_group
    assert EXPECTED_STYLE_COMMANDS <= _command_names(attached)


def test_welcome_command_names_are_unique() -> None:
    _register()
    names = [
        str(getattr(command, "name", ""))
        for command in getattr(public_welcome_group.welcome_group, "commands", [])
        if getattr(command, "name", "")
    ]
    assert len(names) == len(set(names))


if __name__ == "__main__":
    for test in (
        test_production_entrypoint_has_no_welcome_startup_guard,
        test_studio_never_replaces_or_removes_commands,
        test_live_welcome_group_contains_all_style_commands,
        test_dank_tree_uses_the_same_populated_welcome_group,
        test_welcome_command_names_are_unique,
    ):
        test()
        print(f"PASS {test.__name__}")
