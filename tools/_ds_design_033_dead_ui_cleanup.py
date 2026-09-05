from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEGACY_PATH = ROOT / "stoney_verify/commands_ext/public_design_studio.py"
V2_PATH = ROOT / "stoney_verify/commands_ext/public_design_studio_v2.py"
TEST_PATH = ROOT / "tests/test_dank_design_consistency_030.py"
CONSOLIDATION_TEST_PATH = ROOT / "tests/test_design_studio_consolidation_032.py"
AUDIT_PATH = ROOT / "tools/audit_dank_design_redundancy_033.py"


def remove_between(text: str, start_marker: str, end_marker: str, *, label: str) -> str:
    if text.count(start_marker) != 1:
        raise RuntimeError(f"{label}: expected one start marker, found {text.count(start_marker)}")
    if text.count(end_marker) != 1:
        raise RuntimeError(f"{label}: expected one end marker, found {text.count(end_marker)}")
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    if end <= start:
        raise RuntimeError(f"{label}: invalid marker order")
    return text[:start] + text[end:]


legacy = LEGACY_PATH.read_text(encoding="utf-8")
legacy = remove_between(
    legacy,
    "# ---------------------------------------------------------------------------\n# Design Doctor\n# ---------------------------------------------------------------------------\n",
    "# ---------------------------------------------------------------------------\n# Saved Rule Removal / Reset\n# ---------------------------------------------------------------------------\n",
    label="legacy Design Doctor",
)
legacy = remove_between(
    legacy,
    "def _start_here_embed() -> discord.Embed:\n",
    "STYLE_CHANGE_SEPARATOR_IDS: tuple[str, ...] = (\n",
    label="legacy dead help/rules submenus",
)
for forbidden in (
    "class DesignDoctorButton",
    "class DesignDoctorView",
    "class StartHereButton",
    "class StartHereView",
    "class EditorsLocksButton",
    "class EditorsLocksView",
    "class AdvancedToolsView",
    "def _doctor_embed",
    "def _start_here_embed",
    "def _editors_locks_embed",
    "def _design_help_embed",
    "def _advanced_tools_embed",
):
    if forbidden in legacy:
        raise RuntimeError(f"legacy dead UI marker survived cleanup: {forbidden}")
LEGACY_PATH.write_text(legacy, encoding="utf-8")

v2 = V2_PATH.read_text(encoding="utf-8")
v2 = remove_between(
    v2,
    "def _compat_help_embed() -> discord.Embed:\n",
    "async def _go_home(interaction: discord.Interaction) -> None:\n",
    label="V2 obsolete compatibility help",
)
v2 = v2.replace("    legacy._start_here_embed = _compat_help_embed  # type: ignore[attr-defined]\n", "")
v2 = v2.replace("    legacy._design_help_embed = _compat_help_embed  # type: ignore[attr-defined]\n", "")
v2 = v2.replace(
    "    This bridge changes only navigation/help/apply UI globals. It does not replace\n",
    "    This bridge changes only navigation/apply UI globals. It does not replace\n",
)
v2 = v2.replace('    "_compat_help_embed",\n', "")
for forbidden in (
    "def _compat_help_embed",
    "legacy._start_here_embed =",
    "legacy._design_help_embed =",
):
    if forbidden in v2:
        raise RuntimeError(f"V2 obsolete help bridge marker survived cleanup: {forbidden}")
V2_PATH.write_text(v2, encoding="utf-8")

test = TEST_PATH.read_text(encoding="utf-8")
test = remove_between(
    test,
    "def test_rules_ui_has_no_duplicate_exact_name_counter_or_joined_lines() -> None:\n",
    "def test_doctor_does_not_treat_optional_category_locks_as_required() -> None:\n",
    label="stale rules submenu test",
)
test = test.replace(
    "def test_doctor_does_not_treat_optional_category_locks_as_required() -> None:\n"
    "    start = PUBLIC.index(\"def _doctor_embed\")\n"
    "    end = PUBLIC.index(\"class DesignDoctorButton\", start)\n"
    "    block = PUBLIC[start:end]\n"
    "    assert \"missing_locks\" not in block\n"
    "    assert \"lock missing categories\" not in block\n\n\n",
    "def test_rules_surface_has_one_counter_and_retired_submenus_are_absent() -> None:\n"
    "    start = PUBLIC.index(\"def _format_locks_embed\")\n"
    "    end = PUBLIC.index(\"async def build_design_plan\", start)\n"
    "    block = PUBLIC[start:end]\n"
    "    assert block.count(\"Exact manual names:\") == 1\n"
    "    for marker in (\n"
    "        \"class DesignDoctorButton\",\n"
    "        \"class DesignDoctorView\",\n"
    "        \"class StartHereButton\",\n"
    "        \"class StartHereView\",\n"
    "        \"class EditorsLocksButton\",\n"
    "        \"class EditorsLocksView\",\n"
    "        \"class AdvancedToolsView\",\n"
    "        \"def _doctor_embed\",\n"
    "        \"def _start_here_embed\",\n"
    "        \"def _editors_locks_embed\",\n"
    "        \"def _design_help_embed\",\n"
    "        \"def _advanced_tools_embed\",\n"
    "    ):\n"
    "        assert marker not in PUBLIC\n"
    "    assert \"def _compat_help_embed\" not in V2\n\n\n",
)
if "test_doctor_does_not_treat_optional_category_locks_as_required" in test:
    raise RuntimeError("stale doctor test survived cleanup")
if "def test_rules_surface_has_one_counter_and_retired_submenus_are_absent" not in test:
    raise RuntimeError("replacement dead-submenu regression was not installed")
TEST_PATH.write_text(test, encoding="utf-8")

consolidation = CONSOLIDATION_TEST_PATH.read_text(encoding="utf-8")
consolidation = consolidation.replace('    assert "One exact Rename" in V2\n', "")
old_bridge = '''def test_legacy_bridge_is_small_explicit_navigation_help_and_apply_boundary() -> None:\n    assert legacy._home_embed is studio_v2._home_embed\n    assert legacy._start_here_embed is studio_v2._compat_help_embed\n    assert legacy._design_help_embed is studio_v2._compat_help_embed\n    assert legacy.DesignHomeView is studio_v2.DesignHomeView\n    assert legacy.DesignPreviewView is studio_v2.ReviewedPreviewView\n    assert legacy.StyleChangePreviewView is studio_v2.LegacyStyleChangePreviewView\n\n    bridge_start = V2.index("def _install_legacy_compatibility_bridge")\n    bridge_end = V2.index("\\n\\n_install_legacy_compatibility_bridge()", bridge_start)\n    bridge = V2[bridge_start:bridge_end]\n    for required in (\n        "legacy._home_embed = _home_embed",\n        "legacy._start_here_embed = _compat_help_embed",\n        "legacy.DesignHomeView = DesignHomeView",\n        "legacy.DesignPreviewView = ReviewedPreviewView",\n        "legacy.StyleChangePreviewView = LegacyStyleChangePreviewView",\n    ):\n        assert required in bridge\n    for forbidden in (\n        "legacy.build_design_plan =",\n        "legacy.DesignDoctorView =",\n        "legacy._load_design_options =",\n        "legacy.register_public_design_studio_command =",\n    ):\n        assert forbidden not in bridge\n\n\n'''
new_bridge = '''def test_legacy_bridge_is_small_explicit_navigation_and_apply_boundary() -> None:\n    assert legacy._home_embed is studio_v2._home_embed\n    assert not hasattr(legacy, "_start_here_embed")\n    assert not hasattr(legacy, "_design_help_embed")\n    assert legacy.DesignHomeView is studio_v2.DesignHomeView\n    assert legacy.DesignPreviewView is studio_v2.ReviewedPreviewView\n    assert legacy.StyleChangePreviewView is studio_v2.LegacyStyleChangePreviewView\n\n    bridge_start = V2.index("def _install_legacy_compatibility_bridge")\n    bridge_end = V2.index("\\n\\n_install_legacy_compatibility_bridge()", bridge_start)\n    bridge = V2[bridge_start:bridge_end]\n    for required in (\n        "legacy._home_embed = _home_embed",\n        "legacy.DesignHomeView = DesignHomeView",\n        "legacy.DesignPreviewView = ReviewedPreviewView",\n        "legacy.StyleChangePreviewView = LegacyStyleChangePreviewView",\n    ):\n        assert required in bridge\n    for forbidden in (\n        "legacy._start_here_embed =",\n        "legacy._design_help_embed =",\n        "legacy.build_design_plan =",\n        "legacy.DesignDoctorView =",\n        "legacy._load_design_options =",\n        "legacy.register_public_design_studio_command =",\n    ):\n        assert forbidden not in bridge\n\n\n'''
if consolidation.count(old_bridge) != 1:
    raise RuntimeError("consolidation bridge test changed unexpectedly")
consolidation = consolidation.replace(old_bridge, new_bridge)
consolidation = consolidation.replace(
    '    assert LEGACY.count("view=DesignHomeView(options)") >= 8\n',
    '    assert LEGACY.count("view=DesignHomeView(options)") >= 7\n',
)
for stale in (
    'assert "One exact Rename" in V2',
    "legacy._start_here_embed is studio_v2._compat_help_embed",
    "legacy._design_help_embed is studio_v2._compat_help_embed",
    'LEGACY.count("view=DesignHomeView(options)") >= 8',
):
    if stale in consolidation:
        raise RuntimeError(f"stale consolidation expectation survived cleanup: {stale}")
CONSOLIDATION_TEST_PATH.write_text(consolidation, encoding="utf-8")

audit = AUDIT_PATH.read_text(encoding="utf-8")
needle = "    if 'custom_id=\"dank_design:apply\"' in LEGACY:\n        failures.append(\"retired independent legacy Apply owner still exists\")\n"
if audit.count(needle) != 1:
    raise RuntimeError("redundancy audit insertion point changed")
addition = needle + "\n    for marker in (\n        \"class DesignDoctorButton\",\n        \"class DesignDoctorView\",\n        \"class StartHereButton\",\n        \"class StartHereView\",\n        \"class EditorsLocksButton\",\n        \"class EditorsLocksView\",\n        \"class AdvancedToolsView\",\n        \"def _doctor_embed\",\n        \"def _start_here_embed\",\n        \"def _editors_locks_embed\",\n        \"def _design_help_embed\",\n        \"def _advanced_tools_embed\",\n    ):\n        if marker in LEGACY:\n            failures.append(f\"retired legacy submenu/helper remains: {marker}\")\n    for marker in (\"def _compat_help_embed\", \"legacy._start_here_embed =\", \"legacy._design_help_embed =\"):\n        if marker in V2:\n            failures.append(f\"obsolete legacy help bridge remains: {marker}\")\n"
audit = audit.replace(needle, addition)
audit = audit.replace(
    '        "runtime_magic=0 native_plan=yes consolidated_apply=yes compatibility_boundary=ui_only"\n',
    '        "runtime_magic=0 dead_submenus=0 native_plan=yes consolidated_apply=yes compatibility_boundary=ui_only"\n',
)
AUDIT_PATH.write_text(audit, encoding="utf-8")

print("DS-DESIGN-033 dead legacy UI cleanup staged")
