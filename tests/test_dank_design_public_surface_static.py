from pathlib import Path


INIT = Path("stoney_verify/commands_ext/__init__.py").read_text(encoding="utf-8")
DESIGN = Path("stoney_verify/commands_ext/public_design_group.py").read_text(encoding="utf-8")


def test_dank_design_is_public_core_module():
    assert '("public_design_group", "register_public_design_group_commands"' in INIT
    public_core = INIT[INIT.index("_PUBLIC_CORE_MODULES"):INIT.index("_PUBLIC_ADMIN_EXTRA_MODULES")]
    assert '"public_design_group",' in public_core


def test_dank_design_is_allowed_child():
    allowed = INIT[INIT.index("_ALLOWED_DANK_CHILDREN"):INIT.index("_COMPACT_SUPPRESS_PREFIXES")]
    assert '"design",' in allowed


def test_dank_design_registers_native_command():
    assert 'dank_group.command(name="design"' in DESIGN
    assert "open_design_studio" in DESIGN
