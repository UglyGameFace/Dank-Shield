from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEGACY_PATH = ROOT / "stoney_verify/commands_ext/public_design_studio.py"
V2_PATH = ROOT / "stoney_verify/commands_ext/public_design_studio_v2.py"


def once(text: str, old: str, new: str, name: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{name}: expected 1 match, found {count}")
    return text.replace(old, new, 1)


legacy = LEGACY_PATH.read_text(encoding="utf-8")
legacy = once(
    legacy,
    '    protection_items = _protection_item_rules(options) if "_protection_item_rules" in globals() else _mapping_dict(options.get("protection_item_rules"))\n    return {\n',
    '    protection_items = _protection_item_rules(options) if "_protection_item_rules" in globals() else _mapping_dict(options.get("protection_item_rules"))\n    protection_names = _protection_rules(options) if "_protection_rules" in globals() else _mapping_dict(options.get("protection_rules"))\n    return {\n',
    "protection name count source",
)
legacy = once(
    legacy,
    '        "protection_items": len(protection_items),\n    }\n',
    '        "protection_items": len(protection_items),\n        "protection_names": len(protection_names),\n    }\n',
    "protection name count result",
)
count_copy = '            f"Exact protection overrides: **{counts[\'protection_items\']}**",\n'
if legacy.count(count_copy) != 2:
    raise RuntimeError(f"protection count copy: expected 2 matches, found {legacy.count(count_copy)}")
legacy = legacy.replace(
    count_copy,
    count_copy + '            f"Name protection overrides: **{counts[\'protection_names\']}**",\n',
)

replacements = {
    "Saved manual name is empty. Unlock it and rename again.": "Saved manual name is empty. Remove or reset that exact-name rule, then rename again.",
    "Use Unlock Saved Rules to remove one rule without disturbing the others.": "Use Remove One Saved Rule to remove one listed rule, or Reset This Item to clear that item's same-item overrides.",
    "Open **Rules & Unlocks** to inspect or remove any saved rule without changing the others.": "Open **Rules & Resets** to inspect saved authority, remove one listed rule, or reset an item's overrides.",
    "Global/category styles cannot replace it unless you use Custom Format, Lock Rule, or Unlock Saved Rules.": "Global/category styles cannot replace it unless you replace or reset this item's saved rule through Custom Format, Lock Rule, or Reset This Item.",
    "# Lock / Unlock Saved Rules": "# Saved Rule Removal / Reset",
    "title=\"🔐 Lock / Unlock Saved Rules\"": "title=\"🔐 Saved Rules — Remove One\"",
    "description=\"Review exact names and style locks, remove individual overrides, or clean stale locks.\"": "description=\"Each numbered button removes exactly one listed rule. A broader or different same-item rule can still remain active. Use Reset This Category/Channel in the item editor when you want every same-item override removed. Name-level protection is managed under Protection Rules.\"",
    "Use the numbered buttons to unlock one rule, or clean stale rules only.": "Numbered buttons remove one listed rule only • Reset This Item clears same-item overrides • Clean Stale removes deleted-item rows only.",
    "Unlock Saved Rules": "Remove One Saved Rule",
    "Exact item overrides are visible in Remove One Saved Rule and can be removed independently.": "Exact item overrides are visible in Remove One Saved Rule and can be removed independently; normalized-name protection remains under Protection Rules.",
    "Protected Names / Unlock": "Protection Rules",
    "Rules & Unlocks": "Rules & Resets",
}

for old, new in replacements.items():
    if old not in legacy:
        raise RuntimeError(f"legacy copy marker missing: {old}")
    legacy = legacy.replace(old, new)

LEGACY_PATH.write_text(legacy, encoding="utf-8")

v2 = V2_PATH.read_text(encoding="utf-8")
v2 = v2.replace("Saving or unlocking a rule", "Saving or removing a rule")
v2 = v2.replace("Saving, unlocking, or changing a rule", "Saving, removing, or changing a rule")
v2 = once(
    v2,
    '            f"Protection overrides: **{counts.get(\'protection_items\', 0)}**"\n',
    '            f"Exact protection: **{counts.get(\'protection_items\', 0)}**\\n"\n            f"Name protection: **{counts.get(\'protection_names\', 0)}**"\n',
    "home protection counts",
)
v2 = once(
    v2,
    '            f"Exact protection: **{counts.get(\'protection_items\', 0)}**"\n',
    '            f"Exact protection: **{counts.get(\'protection_items\', 0)}**\\n"\n            f"Name protection: **{counts.get(\'protection_names\', 0)}**"\n',
    "saved rules protection counts",
)
v2 = once(
    v2,
    '            "**Layout Rules** = global/category/channel visual rules.\\n"\n            "**Unlock / Clean** = remove one saved rule or stale rule.\\n"\n            "**Protection** = decide which exact/default names automated styling may touch."\n',
    '            "**Layout Rules** = global/category/channel visual rules, plus **Reset All Design Overrides**.\\n"\n            "**Remove One Rule** = remove exactly one listed saved rule or clean deleted-item rows.\\n"\n            "**Protection** = manage exact-item and normalized-name protection. For every same-item override, use **Reset This Category/Channel** in the item editor."\n',
    "saved rules tool explanation",
)
v2 = once(
    v2,
    '@discord.ui.button(label="Unlock / Clean", emoji="🧹", style=discord.ButtonStyle.secondary, custom_id="dank_design_v2:unlock", row=0)',
    '@discord.ui.button(label="Remove One Rule", emoji="🧹", style=discord.ButtonStyle.secondary, custom_id="dank_design_v2:unlock", row=0)',
    "saved rules remove button",
)

V2_PATH.write_text(v2, encoding="utf-8")
print("DS-DESIGN-033 UX patch applied")
