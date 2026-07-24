from pathlib import Path


MAIN = Path("main.py").read_text(encoding="utf-8")
GUARD = Path("stoney_verify/startup_guards/welcome_message_command_guard.py").read_text(
    encoding="utf-8"
)
GROUP = Path("stoney_verify/commands_ext/public_welcome_group.py").read_text(
    encoding="utf-8"
)
COMMANDS_EXT = Path("stoney_verify/commands_ext/__init__.py").read_text(
    encoding="utf-8"
)


def test_entrypoint_loads_welcome_registration_before_app_import():
    guard_import = MAIN.index("welcome_message_command_guard")
    app_import = MAIN.index("from stoney_verify.app import run")

    assert guard_import < app_import


def test_welcome_guard_attaches_group_before_command_sync():
    assert 'allowed.add("welcome")' in GUARD
    assert "register_public_welcome_group_commands" in GUARD
    assert "register(None, None)" in GUARD


def test_public_surface_keeps_welcome_group():
    allowed_start = COMMANDS_EXT.index("_ALLOWED_DANK_CHILDREN")
    allowed_end = COMMANDS_EXT.index("_COMPACT_SUPPRESS_PREFIXES", allowed_start)
    allowed = COMMANDS_EXT[allowed_start:allowed_end]

    assert '"welcome"' in allowed


def test_all_welcome_card_commands_exist_on_the_registered_group():
    for command_name in (
        "card-preview",
        "card-theme",
        "card-upload",
        "card-clear-custom",
        "card-enabled",
    ):
        assert f'@welcome_group.command(name="{command_name}"' in GROUP
