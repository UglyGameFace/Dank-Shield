from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEGACY_PATH = ROOT / "stoney_verify/commands_ext/public_design_studio.py"
AUDIT_PATH = ROOT / "tools/audit_dank_design_redundancy_033.py"

REMOVE_TOP_LEVEL = {
    "ThemeSelect",
    "StrengthSelect",
    "FormatLocksButton",
    "DesignCategoryEditorButton",
    "DesignChannelEditorButton",
    "ProtectionManagerButton",
    "DesignDoneView",
    "RollbackConfirmView",
    "_open_rollback",
    "_saved_style_summary",
    "_consistency_lines",
    "_consistency_embed",
}


def top_level_nodes(source: str) -> dict[str, ast.AST]:
    tree = ast.parse(source)
    out: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = node
    return out


def remove_nodes(source: str, names: set[str]) -> str:
    nodes = top_level_nodes(source)
    missing = sorted(name for name in names if name not in nodes)
    if missing:
        raise RuntimeError(f"expected dead top-level owners missing or moved: {missing}")
    lines = source.splitlines(keepends=True)
    ranges: list[tuple[int, int, str]] = []
    for name in names:
        node = nodes[name]
        start = int(getattr(node, "lineno")) - 1
        end = int(getattr(node, "end_lineno"))
        while end < len(lines) and not lines[end].strip():
            end += 1
        ranges.append((start, end, name))
    for start, end, _name in sorted(ranges, reverse=True):
        del lines[start:end]
    return "".join(lines)


legacy_before = LEGACY_PATH.read_text(encoding="utf-8")

# Prove these are implementation islands before deleting them. They should not
# be imported elsewhere; historical global references are confined to the dead
# island itself or are replaced by the V2 compatibility bridge at public use.
all_python = "\n".join(
    path.read_text(encoding="utf-8", errors="ignore")
    for path in ROOT.rglob("*.py")
    if path != Path(__file__)
)
for name in (
    "ThemeSelect",
    "FormatLocksButton",
    "DesignCategoryEditorButton",
    "DesignChannelEditorButton",
    "ProtectionManagerButton",
    "DesignDoneView",
):
    if all_python.count(name) != legacy_before.count(name):
        raise RuntimeError(f"{name} has a reference outside the legacy backend")

legacy = remove_nodes(legacy_before, REMOVE_TOP_LEVEL)

# The legacy backend still needs the symbol because mature editor Back buttons
# resolve it dynamically. Keep a redirect-only fallback, while V2 replaces this
# global with its real consolidated home function on public import.
nodes = top_level_nodes(legacy)
home = nodes.get("_home_embed")
if home is None:
    raise RuntimeError("legacy _home_embed fallback owner not found")
lines = legacy.splitlines(keepends=True)
start = int(getattr(home, "lineno")) - 1
end = int(getattr(home, "end_lineno"))
fallback = '''def _home_embed(guild: discord.Guild, options: Mapping[str, Any] | None = None) -> discord.Embed:
    """Import-order fallback only; V2 replaces this before public Studio use."""
    _ = guild, options
    embed = discord.Embed(
        title="🎨 Dank Design Studio",
        description="Open `/dank home`, then choose **Server Design** to use the consolidated Studio.",
        color=discord.Color.blurple(),
    )
    embed.set_footer(text="Compatibility fallback only • Public home is owned by V2")
    return _clean_design_embed(embed)

'''
legacy = "".join(lines[:start]) + fallback + "".join(lines[end:])

for name in REMOVE_TOP_LEVEL:
    if name in top_level_nodes(legacy):
        raise RuntimeError(f"dead owner survived cleanup: {name}")
for marker in (
    'class ThemeSelect',
    'class FormatLocksButton',
    'class DesignCategoryEditorButton',
    'class DesignChannelEditorButton',
    'class ProtectionManagerButton',
    'class DesignDoneView',
    'class RollbackConfirmView',
    'async def _open_rollback',
    'def _saved_style_summary',
    'def _consistency_lines',
    'def _consistency_embed',
):
    if marker in legacy:
        raise RuntimeError(f"dead legacy marker survived cleanup: {marker}")
if "Compatibility fallback only" not in legacy:
    raise RuntimeError("legacy home did not become redirect-only fallback")

ast.parse(legacy)
LEGACY_PATH.write_text(legacy, encoding="utf-8")

# Make the permanent audit reject resurrection of these historical owners.
audit = AUDIT_PATH.read_text(encoding="utf-8")
needle = '    for marker in (\n        "class DesignDoctorButton",\n'
if needle not in audit:
    raise RuntimeError("redundancy audit dead-submenu block changed")
extra = '''    for marker in (
        "class ThemeSelect",
        "class FormatLocksButton",
        "class DesignCategoryEditorButton",
        "class DesignChannelEditorButton",
        "class ProtectionManagerButton",
        "class DesignDoneView",
        "class RollbackConfirmView",
        "async def _open_rollback",
        "def _saved_style_summary",
        "def _consistency_lines",
        "def _consistency_embed",
    ):
        if marker in LEGACY:
            failures.append(f"retired legacy owner/helper remains: {marker}")
    if "Compatibility fallback only" not in LEGACY:
        failures.append("legacy home fallback grew back into a competing home implementation")

'''
audit = audit.replace(needle, extra + needle, 1)
audit = audit.replace(
    'runtime_magic=0 dead_submenus=0 native_plan=yes',
    'runtime_magic=0 dead_submenus=0 dead_owners=0 native_plan=yes',
    1,
)
if "dead_owners=0" not in audit:
    raise RuntimeError("redundancy audit success line did not gain dead-owner contract")
AUDIT_PATH.write_text(audit, encoding="utf-8")

print("DS-DESIGN-033 final dead owner cleanup staged")
