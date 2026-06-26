from pathlib import Path


INIT = Path("stoney_verify/commands_ext/__init__.py").read_text(encoding="utf-8")
GROUP = Path("stoney_verify/commands_ext/public_design_group.py").read_text(encoding="utf-8")
STUDIO = Path("stoney_verify/commands_ext/public_design_studio.py").read_text(encoding="utf-8")


def test_public_design_group_is_loaded_by_commands_ext():
    assert '("public_design_group", "register_public_design_group_commands"' in INIT


def test_public_design_group_is_in_public_core_profile():
    core_start = INIT.index("_PUBLIC_CORE_MODULES")
    core_end = INIT.index("_PUBLIC_ADMIN_EXTRA_MODULES", core_start)
    core = INIT[core_start:core_end]

    assert '"public_design_group"' in core


def test_design_child_is_allowed_to_survive_public_prune():
    allowed_start = INIT.index("_ALLOWED_DANK_CHILDREN")
    allowed_end = INIT.index("_COMPACT_SUPPRESS_PREFIXES", allowed_start)
    allowed = INIT[allowed_start:allowed_end]

    assert '"design"' in allowed


def test_design_group_uses_native_registration_not_startup_guard():
    assert "register_public_design_group_commands" in GROUP
    assert "public_design_studio as design" in GROUP
    assert "server_design_studio_command_guard as design" not in GROUP
    assert "register_public_design_studio_command" in STUDIO


def test_design_studio_has_real_home_ui():
    assert "🎨 Dank Design Studio" in STUDIO
    assert "DesignHomeView" in STUDIO
