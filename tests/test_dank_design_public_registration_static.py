from pathlib import Path


INIT = Path("stoney_verify/commands_ext/__init__.py").read_text(encoding="utf-8")
GROUP = Path("stoney_verify/commands_ext/public_design_group.py").read_text(encoding="utf-8")
STUDIO = Path("stoney_verify/commands_ext/public_design_studio_v2.py").read_text(encoding="utf-8")


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


def test_design_group_is_the_only_native_public_registration_owner():
    assert "register_public_design_group_commands" in GROUP
    assert "public_design_studio_v2 as design" in GROUP
    assert GROUP.count('@dank_group.command(name="design"') == 1
    assert "server_design_studio_command_guard" not in GROUP
    assert "activate_public_design_enhancements" not in GROUP
    assert "register_public_design_studio_command" not in STUDIO


def test_design_studio_home_is_four_owner_tasks_not_a_feature_directory():
    assert "🎨 Server Appearance" in STUDIO
    assert "class DesignHomeView" in STUDIO
    home = STUDIO[STUDIO.index("class DesignHomeView"):STUDIO.index("def _snapshot_matches")]
    for label in ("Style All Names", "Edit One Item", "Check & Fix", "Advanced"):
        assert f'label="{label}"' in home
    assert home.count("@discord.ui.button") == 4
    assert "DesignServerThemeSelect" not in home
    assert "DesignServerStrengthSelect" not in home
    advanced = STUDIO[STUDIO.index("class DesignAdvancedView"):STUDIO.index("class DesignHomeView")]
    for label in ("Saved Style Exceptions", "Searchable Names", "History & Reset"):
        assert f'label="{label}"' in advanced
