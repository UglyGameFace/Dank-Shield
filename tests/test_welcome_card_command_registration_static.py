from pathlib import Path


MAIN = Path("main.py").read_text(encoding="utf-8")
COMMANDS = Path("stoney_verify/commands.py").read_text(encoding="utf-8")
GROUP = Path("stoney_verify/commands_ext/public_welcome_group.py").read_text(
    encoding="utf-8"
)
STUDIO = Path(
    "stoney_verify/commands_ext/public_welcome_card_studio.py"
).read_text(encoding="utf-8")
COMMANDS_EXT = Path("stoney_verify/commands_ext/__init__.py").read_text(
    encoding="utf-8"
)


def test_entrypoint_has_no_welcome_product_guard() -> None:
    assert "welcome_message_command_guard" not in MAIN
    assert "register_public_welcome_card_studio_commands" in COMMANDS


def test_canonical_registration_never_replaces_existing_commands() -> None:
    assert "remove_command(" not in STUDIO
    assert "_replace_existing_command" not in STUDIO
    assert "duplicate /dank welcome command" in STUDIO


def test_public_surface_keeps_welcome_group() -> None:
    allowed_start = COMMANDS_EXT.index("_ALLOWED_DANK_CHILDREN")
    allowed_end = COMMANDS_EXT.index("_COMPACT_SUPPRESS_PREFIXES", allowed_start)
    allowed = COMMANDS_EXT[allowed_start:allowed_end]
    assert '"welcome"' in allowed


def test_all_base_welcome_card_commands_exist_on_the_registered_group() -> None:
    for command_name in (
        "card-preview",
        "card-theme",
        "card-upload",
        "card-clear-custom",
        "card-enabled",
    ):
        assert f'@welcome_group.command(name="{command_name}"' in GROUP


def test_all_studio_commands_are_owned_by_one_module() -> None:
    for command_name in (
        "card-font",
        "card-font-upload",
        "card-font-clear",
        "card-colors",
        "card-style",
    ):
        assert f'"{command_name}"' in STUDIO
