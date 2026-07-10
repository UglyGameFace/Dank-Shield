from __future__ import annotations

"""Clean up Dank Design UX wording and lock/unlock surfaces.

Goals:
- Stop mixing Live Majority, saved rules, and separator-only tools in one vague UI.
- Rename vague buttons like "Change One Style" to the actual action: separator-only.
- Make saved category/channel rules visibly lockable/unlockable.
- Show per-rule presets in the lock manager, including separator, font, frame, and strength.
- Update old static test copy so older expectations do not pull the confusing wording back.

Run from repo root:
    python tools/apply_dank_design_ux_lock_cleanup.py
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "stoney_verify/commands_ext/public_design_studio.py"
SAFE_TEST = ROOT / "tools/test_dank_design_safe_repair_cleanup_static.py"


def replace_required(text: str, old: str, new: str, label: str) -> str:
    if old in text:
        print(f"✅ patched {label}")
        return text.replace(old, new, 1)
    if new in text:
        print(f"✅ already patched {label}")
        return text
    raise SystemExit(f"Could not find {label}")


def patch_public() -> None:
    text = PUBLIC.read_text(encoding="utf-8")

    old_home_recommended = '''    embed.add_field(
        name="Recommended",
        value="\n".join((
            "🧭 **Fix Mismatched Names** — reviews saved rules first; Live Majority is preview-only when saved rules exist.",
            "⚡ **Change One Style** — add/change one thing, like a separator, while keeping everything else.",
            "👁️ **Preview Saved Design** — shows what saved rules would rename before anything changes.",
        )),
        inline=False,
    )
'''
    new_home_recommended = '''    embed.add_field(
        name="Recommended workflow",
        value="\n".join((
            "👁️ **Preview Saved Design** — follows your saved global/category/channel rules and shows exact names before anything changes.",
            "🧭 **Review Name Drift** — compares names against saved category/channel rules; live detection is preview-only when saved rules exist.",
            "⚡ **Change Separator Only** — changes channel separators only. It does not change icons, font, category frames, permissions, or order.",
        )),
        inline=False,
    )
'''
    text = replace_required(text, old_home_recommended, new_home_recommended, "home recommended workflow")

    old_saved_rules = '''    embed.add_field(
        name="Saved rules",
        value="\n".join((
            f"Global: **{'On' if counts['global'] else 'Off'}**",
            f"Categories: **{counts['categories']}**",
            f"Channels: **{counts['channels']}**",
            "Fix Mismatched Names protects saved rules; use Live Majority only as a manual preview.",
        )),
        inline=True,
    )
'''
    new_saved_rules = '''    embed.add_field(
        name="Saved rules / locks",
        value="\n".join((
            f"Global preset: **{'On' if counts['global'] else 'Off'}**",
            f"Locked category rules: **{counts['categories']}**",
            f"Locked channel overrides: **{counts['channels']}**",
            "Open **Rules & Unlocks** to see the exact preset each category/channel follows or unlock it.",
        )),
        inline=True,
    )
'''
    text = replace_required(text, old_saved_rules, new_saved_rules, "home saved rules summary")

    replacements = {
        "Fix Mismatched Names": "Review Name Drift",
        "Fix Inconsistencies": "Review Name Drift",
        "Change One Style": "Change Separator Only",
        "Editors & Locks": "Rules & Unlocks",
        "Manage Saved Rules": "Unlock Saved Rules",
        "Manage Locks": "Unlock Saved Rules",
        "Format Lock Manager": "Lock / Unlock Saved Rules",
        "Review saved global/category/channel locks, remove individual overrides, or clean stale locks.": "Review exactly which preset is locked for global, categories, and channels. Unlock individual rules or clean stale/deleted targets.",
        "No format locks saved yet. Use **Category Editor** or **Channel Editor** to create locks.": "No saved category/channel rules yet. Use **Category Editor** or **Channel Editor** → **Custom Format** → **Save Rule & Preview** to create one.",
        "Use the numbered buttons to remove one lock, or clean stale locks only.": "Use numbered buttons to unlock one saved rule. Nothing is permanent; stale/deleted targets can be cleaned safely.",
        "Protected item → Channel override → Category lock → Global lock → Auto theme": "Protection policy → Channel override → Category rule → Global preset → Detected live style preview",
        "Remove {display_index}.": "Unlock {display_index}.",
        "Save Category Layout": "Lock Category Rule",
        "Save Channel Layout": "Lock Channel Rule",
        "Category Rule Saved": "Category Rule Locked",
        "Channel Rule Saved": "Channel Rule Locked",
        "Protected Names / Unlock": "Protection: Lock / Unlock",
        "Use More Tools for problem checks, saved rules, rename protection, rollback, and help.": "Use Rules & Unlocks to see exact presets, unlock category/channel rules, manage protection, rollback, and help.",
        "Preview/Change One Style/Custom Format": "Preview/Change Separator/Custom Format",
        "✅ Change One Style Applied": "✅ Separator Change Applied",
        "Review Repairs": "Review Name Drift",
    }
    for old, new in replacements.items():
        if old in text:
            text = text.replace(old, new)

    old_style_embed = '''    embed = discord.Embed(
        title="⚡ Change Separator Only",
        description=(
            "Change **one visual rule** while keeping the rest of the server style the same.\n\n"
            "**Current tool:** Channel Separator\n"
            "Choosing a separator only updates this draft. Use **Preview This Change** next, then **Apply Reviewed Changes**."
        ),
        color=discord.Color.blurple(),
    )
'''
    new_style_embed = '''    embed = discord.Embed(
        title="⚡ Change Channel Separator Only",
        description=(
            "This tool changes only the **separator between an existing icon and channel name**.\n\n"
            "It keeps current emoji/icons, font, category frames, permissions, tickets, verification, and channel order unchanged.\n"
            "Use **Preview This Change** next, then **Apply Reviewed Changes**."
        ),
        color=discord.Color.blurple(),
    )
'''
    text = replace_required(text, old_style_embed, new_style_embed, "separator-only style embed")

    # Add category frame data to lock rows so users can see category/channel presets clearly.
    text = text.replace(
        '"separator_id": _safe_str(global_lock.get("separator_id"), ""),\n            "strength": _safe_int(global_lock.get("strength"), 4),',
        '"separator_id": _safe_str(global_lock.get("separator_id"), ""),\n            "category_frame_id": _safe_str(global_lock.get("category_frame_id"), "plain"),\n            "strength": _safe_int(global_lock.get("strength"), 4),'
    )
    text = text.replace(
        '"separator_id": _safe_str(lock_map.get("separator_id"), ""),\n            "strength": _safe_int(lock_map.get("strength"), 4),',
        '"separator_id": _safe_str(lock_map.get("separator_id"), ""),\n            "category_frame_id": _safe_str(lock_map.get("category_frame_id"), "plain"),\n            "strength": _safe_int(lock_map.get("strength"), 4),'
    )

    old_lock_line = '''            font = _safe_str(row.get("font"), "normal").replace("_", " ").title()
            sep = _safe_str(row.get("separator_id"), "none").replace("_", " ").title()
            strength = _safe_int(row.get("strength"), 4)
            lines.append(f"**{index}.** {exists} **{scope}** `{label}` · Font: `{font}` · Sep: `{sep}` · Strength: `{strength}`")
'''
    new_lock_line = '''            font = _safe_str(row.get("font"), "normal").replace("_", " ").title()
            sep = _safe_str(row.get("separator_id"), "none").replace("_", " ").title() or "None"
            frame = _safe_str(row.get("category_frame_id"), "plain").replace("_", " ").title()
            strength = _safe_int(row.get("strength"), 4)
            if row.get("scope") == "channel":
                preset = f"Font `{font}` • Separator `{sep}` • Strength `{strength}/5`"
            else:
                preset = f"Font `{font}` • Separator `{sep}` • Frame `{frame}` • Strength `{strength}/5`"
            lines.append(f"**{index}.** {exists} **{scope}** `{label}` → {preset}")
'''
    text = replace_required(text, old_lock_line, new_lock_line, "lock manager preset row")

    # Make preview help less contradictory for separator-only missing-icon cases.
    text = text.replace(
        '• **{other_count} other issue(s)** — open Channel Editor and fix individually.',
        '• **{other_count} other issue(s)** — if they are not missing icons, open Channel Editor and fix only those items.'
    )
    text = text.replace(
        '"How to fix",\n            value="\\n".join(_style_change_issue_lines(items))[:1024],',
        '"How to fix next",\n            value="\\n".join(_style_change_issue_lines(items))[:1024],'
    )

    required = (
        "Recommended workflow",
        "Review Name Drift",
        "Change Channel Separator Only",
        "Rules & Unlocks",
        "Lock / Unlock Saved Rules",
        "Unlock Saved Rules",
        "Locked category rules",
        "Locked channel overrides",
        "Nothing is permanent",
        "Frame `{frame}`",
        "Protection policy → Channel override → Category rule → Global preset",
    )
    missing = [token for token in required if token not in text]
    if missing:
        raise SystemExit("Missing Dank Design UX tokens: " + ", ".join(missing))

    forbidden = (
        "Change One Style",
        "Fix Mismatched Names",
        "Editors & Locks",
        "Format Lock Manager",
    )
    remaining = [token for token in forbidden if token in text]
    if remaining:
        raise SystemExit("Old confusing Dank Design wording still remains: " + ", ".join(remaining))

    PUBLIC.write_text(text, encoding="utf-8")
    print("✅ Dank Design UX and lock/unlock wording cleaned")


def patch_safe_test() -> None:
    if not SAFE_TEST.exists():
        return
    text = SAFE_TEST.read_text(encoding="utf-8")
    text = text.replace('assert "Saved rules win" in PUBLIC', 'assert "Saved rules / locks" in PUBLIC')
    text = text.replace('assert "Live Majority is preview-only when locks exist" in PUBLIC', 'assert "live detection is preview-only when saved rules exist" in PUBLIC')
    text = text.replace('assert "reviews saved rules first" in PUBLIC', 'assert "compares names against saved category/channel rules" in PUBLIC')
    SAFE_TEST.write_text(text, encoding="utf-8")
    print("✅ updated safe repair static expectations")


def main() -> None:
    patch_public()
    patch_safe_test()


if __name__ == "__main__":
    main()
