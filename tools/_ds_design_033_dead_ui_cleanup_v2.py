from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEGACY_PATH = ROOT / "stoney_verify/commands_ext/public_design_studio.py"
V2_PATH = ROOT / "stoney_verify/commands_ext/public_design_studio_v2.py"
CONSISTENCY_PATH = ROOT / "tests/test_dank_design_consistency_030.py"
CONSOLIDATION_PATH = ROOT / "tests/test_design_studio_consolidation_032.py"
AUDIT_PATH = ROOT / "tools/audit_dank_design_redundancy_033.py"

DEAD_LEGACY_MARKERS = (
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
)


def remove_between(text: str, start_marker: str, end_marker: str, *, label: str) -> str:
    count_start = text.count(start_marker)
    count_end = text.count(end_marker)
    if count_start != 1 or count_end != 1:
        raise RuntimeError(f"{label}: markers changed start={count_start} end={count_end}")
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    if end <= start:
        raise RuntimeError(f"{label}: invalid marker order")
    return text[:start] + text[end:]


def replace_function(text: str, function_name: str, replacement: str) -> str:
    pattern = re.compile(
        rf"^def {re.escape(function_name)}\([^\n]*\) -> None:\n(?:^(?:    .*|\s*)\n)+?(?=^def |^class |\Z)",
        re.MULTILINE,
    )
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise RuntimeError(f"{function_name}: expected one function, found {len(matches)}")
    match = matches[0]
    return text[: match.start()] + replacement.rstrip() + "\n\n\n" + text[match.end():]


# ---------------------------------------------------------------------------
# Legacy backend: physically remove public-looking dead submenus/helpers.
# ---------------------------------------------------------------------------
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
    label="legacy help/editor-lock/advanced submenus",
)
legacy = legacy.replace(
    '"""Public /dank design command for the Server Design Studio.\n\n',
    '"""Compatibility/backend primitives for the consolidated Dank Design Studio.\n\n',
    1,
)
legacy = legacy.replace(
    '    "Nothing was changed unless the success message says it was. Reopen `/dank design`, "\n'
    '    "then check `/dank diagnostics` with the Error ID if it keeps happening."\n',
    '    "Nothing was changed unless the success message says it was. Reopen `/dank home`, "\n'
    '    "choose **Server Design**, then check `/dank diagnostics` with the Error ID if it keeps happening."\n',
    1,
)
for marker in DEAD_LEGACY_MARKERS:
    if marker in legacy:
        raise RuntimeError(f"dead legacy UI survived cleanup: {marker}")
if "Reopen `/dank design`" in legacy:
    raise RuntimeError("legacy recovery guidance still points at retired public route")
LEGACY_PATH.write_text(legacy, encoding="utf-8")


# ---------------------------------------------------------------------------
# V2: remove compatibility help that existed solely for deleted legacy menus.
# ---------------------------------------------------------------------------
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
for marker in ("def _compat_help_embed", "legacy._start_here_embed =", "legacy._design_help_embed ="):
    if marker in v2:
        raise RuntimeError(f"obsolete V2 help bridge survived cleanup: {marker}")
V2_PATH.write_text(v2, encoding="utf-8")


# ---------------------------------------------------------------------------
# Consolidation tests: remove expectations for intentionally deleted UI.
# ---------------------------------------------------------------------------
consolidation = CONSOLIDATION_PATH.read_text(encoding="utf-8")
consolidation = consolidation.replace('    assert "One exact Rename" in V2\n', "")
consolidation = replace_function(
    consolidation,
    "test_legacy_bridge_is_small_explicit_navigation_help_and_apply_boundary",
    '''def test_legacy_bridge_is_small_explicit_navigation_and_apply_boundary() -> None:
    assert legacy._home_embed is studio_v2._home_embed
    assert not hasattr(legacy, "_start_here_embed")
    assert not hasattr(legacy, "_design_help_embed")
    assert legacy.DesignHomeView is studio_v2.DesignHomeView
    assert legacy.DesignPreviewView is studio_v2.ReviewedPreviewView
    assert legacy.StyleChangePreviewView is studio_v2.LegacyStyleChangePreviewView

    bridge_start = V2.index("def _install_legacy_compatibility_bridge")
    bridge_end = V2.index("\\n\\n_install_legacy_compatibility_bridge()", bridge_start)
    bridge = V2[bridge_start:bridge_end]
    for required in (
        "legacy._home_embed = _home_embed",
        "legacy.DesignHomeView = DesignHomeView",
        "legacy.DesignPreviewView = ReviewedPreviewView",
        "legacy.StyleChangePreviewView = LegacyStyleChangePreviewView",
    ):
        assert required in bridge
    for forbidden in (
        "legacy._start_here_embed =",
        "legacy._design_help_embed =",
        "legacy.build_design_plan =",
        "legacy.DesignDoctorView =",
        "legacy._load_design_options =",
        "legacy.register_public_design_studio_command =",
    ):
        assert forbidden not in bridge''',
)
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
        raise RuntimeError(f"stale consolidation expectation survived: {stale}")
CONSOLIDATION_PATH.write_text(consolidation, encoding="utf-8")


# ---------------------------------------------------------------------------
# Consistency tests: replace tests for deleted submenus with absence contract.
# ---------------------------------------------------------------------------
consistency = CONSISTENCY_PATH.read_text(encoding="utf-8")
consistency = replace_function(
    consistency,
    "test_rules_ui_has_no_duplicate_exact_name_counter_or_joined_lines",
    '''def test_rules_surface_has_one_counter_and_retired_submenus_are_absent() -> None:
    start = PUBLIC.index("def _format_locks_embed")
    end = PUBLIC.index("async def build_design_plan", start)
    block = PUBLIC[start:end]
    assert block.count("Exact manual names:") == 1
    for marker in (
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
        assert marker not in PUBLIC
    assert "def _compat_help_embed" not in V2''',
)
consistency = replace_function(
    consistency,
    "test_doctor_does_not_treat_optional_category_locks_as_required",
    '''def test_legacy_recovery_guidance_uses_canonical_public_route() -> None:
    assert "Reopen `/dank home`" in PUBLIC
    assert "choose **Server Design**" in PUBLIC
    assert "Reopen `/dank design`" not in PUBLIC''',
)
for stale in (
    "def _editors_locks_embed",
    "class EditorsLocksButton",
    "def _doctor_embed",
    "class DesignDoctorButton",
):
    if stale in consistency and stale not in '"' + stale + '"':
        pass
CONSISTENCY_PATH.write_text(consistency, encoding="utf-8")


# ---------------------------------------------------------------------------
# Permanent redundancy audit: dead submenus/help bridge must never return.
# ---------------------------------------------------------------------------
audit = AUDIT_PATH.read_text(encoding="utf-8")
needle = '''    if 'custom_id="dank_design:apply"' in LEGACY:
        failures.append("retired independent legacy Apply owner still exists")
'''
if audit.count(needle) != 1:
    raise RuntimeError("redundancy audit insertion point changed")
addition = needle + '''
    for marker in (
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
        if marker in LEGACY:
            failures.append(f"retired legacy submenu/helper remains: {marker}")
    for marker in ("def _compat_help_embed", "legacy._start_here_embed =", "legacy._design_help_embed ="):
        if marker in V2:
            failures.append(f"obsolete legacy help bridge remains: {marker}")
'''
audit = audit.replace(needle, addition, 1)
audit = audit.replace(
    '        "runtime_magic=0 native_plan=yes consolidated_apply=yes compatibility_boundary=ui_only"\n',
    '        "runtime_magic=0 dead_submenus=0 native_plan=yes consolidated_apply=yes compatibility_boundary=ui_only"\n',
    1,
)
if "dead_submenus=0" not in audit:
    raise RuntimeError("redundancy audit success contract did not update")
AUDIT_PATH.write_text(audit, encoding="utf-8")

print("DS-DESIGN-033 robust dead legacy UI cleanup staged")
