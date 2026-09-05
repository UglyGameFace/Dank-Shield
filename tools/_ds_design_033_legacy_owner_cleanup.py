from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEGACY_PATH = ROOT / "stoney_verify/commands_ext/public_design_studio.py"
AUDIT_PATH = ROOT / "tools/audit_dank_design_redundancy_033.py"

legacy = LEGACY_PATH.read_text(encoding="utf-8")

old_doc = '''The runtime guard keeps the command in the existing /dank group and uses the
pure service engine for preview/apply/rollback. It only edits channel/category
names and never mutates permissions, overwrites, topics, order, slowmode, NSFW,
archive settings, or category placement.'''
new_doc = '''This module is the compatibility/backend layer for mature exact-item, saved-rule,
separator, and rollback primitives used by the consolidated V2 Studio. It does
not register a public command or own the public home/apply workflow. Design
operations only edit channel/category names and never mutate permissions,
overwrites, topics, order, slowmode, NSFW, archive settings, or placement.'''
if legacy.count(old_doc) != 1:
    raise RuntimeError(f"stale legacy docstring block count={legacy.count(old_doc)}")
legacy = legacy.replace(old_doc, new_doc, 1)

old_live = '    is_live = bool(options.get("__majority_layout_inferred") or options.get("__use_live_majority_layout"))\n'
if legacy.count(old_live) != 1:
    raise RuntimeError(f"legacy consistency runtime-magic marker count={legacy.count(old_live)}")
legacy = legacy.replace(old_live, '    is_live = bool(options.get("__majority_layout_inferred"))\n', 1)

pattern = re.compile(
    r'\nclass DesignHomeView\(discord\.ui\.View\):.*?'
    r'\nclass DesignPreviewView\(discord\.ui\.View\):.*?'
    r'(?=\n\ndef _style_change_missing_emoji_items)',
    re.S,
)
replacement = '''
class DesignHomeView(discord.ui.View):
    """Import-time compatibility symbol; V2 replaces it before public use."""

    def __init__(self, options: Mapping[str, Any] | None = None) -> None:
        super().__init__(timeout=900)


class DesignPreviewView(discord.ui.View):
    """Import-time base only; V2 owns every active reviewed Apply surface."""

    def __init__(self, *, can_apply: bool, pending_created_at: float | None = None) -> None:
        super().__init__(timeout=900)
        self.pending_created_at = pending_created_at
'''
legacy, replaced = pattern.subn(replacement, legacy, count=1)
if replaced != 1:
    raise RuntimeError(f"legacy competing home/apply owner region count={replaced}")

if "__use_live_majority_layout" in legacy:
    raise RuntimeError("retired __use_live_majority_layout marker remains in legacy source")
if '@discord.ui.button(label="Review Name Drift"' in legacy:
    raise RuntimeError("retired mashed legacy home button remains")
if 'custom_id="dank_design:apply"' in legacy:
    raise RuntimeError("retired legacy independent Apply button remains")

LEGACY_PATH.write_text(legacy, encoding="utf-8")

audit = AUDIT_PATH.read_text(encoding="utf-8")
old_check = '''    if "__use_live_majority_layout" in PLAN:
        failures.append("retired live-majority runtime magic flag remains in the native planner")
'''
new_check = '''    if "__use_live_majority_layout" in PLAN:
        failures.append("retired live-majority runtime magic flag remains in the native planner")
    if "__use_live_majority_layout" in LEGACY:
        failures.append("retired live-majority runtime magic flag remains in the legacy backend")
    if '@discord.ui.button(label="Review Name Drift"' in LEGACY:
        failures.append("retired mashed legacy public home still exists")
    if 'custom_id="dank_design:apply"' in LEGACY:
        failures.append("retired independent legacy Apply owner still exists")
'''
if audit.count(old_check) != 1:
    raise RuntimeError(f"redundancy audit insertion anchor count={audit.count(old_check)}")
audit = audit.replace(old_check, new_check, 1)
AUDIT_PATH.write_text(audit, encoding="utf-8")

print("DS-DESIGN-033 legacy owner cleanup staged")
