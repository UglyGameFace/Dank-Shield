from __future__ import annotations

from pathlib import Path

from stoney_verify.commands_ext import public_welcome_group
from stoney_verify.commands_ext import welcome_card_style_commands  # noqa: F401
from stoney_verify.commands_ext.public_setup_group import dank_group


ROOT = Path(__file__).resolve().parents[1]
MAIN = (ROOT / "main.py").read_text(encoding="utf-8")
GUARD = (
    ROOT / "stoney_verify/startup_guards/welcome_message_command_guard.py"
).read_text(encoding="utf-8")

EXPECTED_STYLE_COMMANDS = {
    "card-font",
    "card-colors",
    "card-style",
}


def _command_names(group) -> set[str]:
    return {
        str(getattr(command, "name", ""))
        for command in getattr(group, "commands", [])
        if getattr(command, "name", "")
    }


def test_production_entrypoint_loads_welcome_registration_before_app() -> None:
    guard_import = MAIN.index("welcome_message_command_guard")
    app_import = MAIN.index("from stoney_verify.app import run")
    assert guard_import < app_import
    assert "welcome_card_style_commands" in GUARD


def test_live_welcome_group_contains_all_style_commands() -> None:
    names = _command_names(public_welcome_group.welcome_group)
    assert EXPECTED_STYLE_COMMANDS <= names


def test_dank_tree_uses_the_same_populated_welcome_group() -> None:
    attached = dank_group.get_command("welcome")
    assert attached is public_welcome_group.welcome_group
    assert EXPECTED_STYLE_COMMANDS <= _command_names(attached)


def test_welcome_command_names_are_unique() -> None:
    names = [
        str(getattr(command, "name", ""))
        for command in getattr(public_welcome_group.welcome_group, "commands", [])
        if getattr(command, "name", "")
    ]
    assert len(names) == len(set(names))


if __name__ == "__main__":
    for test in (
        test_production_entrypoint_loads_welcome_registration_before_app,
        test_live_welcome_group_contains_all_style_commands,
        test_dank_tree_uses_the_same_populated_welcome_group,
        test_welcome_command_names_are_unique,
    ):
        test()
        print(f"PASS {test.__name__}")
