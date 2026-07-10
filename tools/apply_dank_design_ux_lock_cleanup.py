from __future__ import annotations

"""Deterministic Dank Design UX / lock cleanup.

This avoids indentation damage by replacing whole helper functions instead of
inserting random embed fields into unknown positions.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "stoney_verify/commands_ext/public_design_studio.py"
SAFE_TEST = ROOT / "tools/test_dank_design_safe_repair_cleanup_static.py"
UX_TEST = ROOT / "tools/test_dank_design_ux_lock_cleanup_static.py"


def replace_function(text: str, name: str, replacement: str) -> str:
    start = text.find(f"def {name}")
    if start < 0:
        raise SystemExit(f"Could not find function {name}")

    candidates = []
    for marker in ("\nclass ", "\ndef ", "\nasync def "):
        pos = text.find(marker, start + 1)
        if pos > start:
            candidates.append(pos)
    end = min(candidates) if candidates else len(text)

    return text[:start] + replacement.rstrip() + "\n\n\n" + text[end:].lstrip("\n")


HOME_EMBED = r'''
def _home_embed(guild: discord.Guild, options: Mapping[str, Any] | None = None) -> discord.Embed:
    options = options or {}
    counts = _lock_count(options)
    _live_analysis, _live_options, live_summary = _infer_live_majority_context(guild, options)
    saved = _saved_style_summary(options)

    embed = discord.Embed(
        title="🎨 Dank Design Studio",
        description=" ".join((
            "Design channel/category names without touching permissions, roles, topics, order, tickets, or verification.",
            "Safe workflow: review first → preview exact names → apply only when you approve.",
        )),
        color=discord.Color.blurple(),
    )

    embed.add_field(
        name="Recommended workflow",
        value="\n".join((
            "👁️ **Preview Saved Design** — follows your saved global/category/channel rules and shows exact names before anything changes.",
            "🧭 **Review Name Drift** — compares names against saved category/channel rules; live detection is preview-only when saved rules exist.",
            "⚡ **Change Channel Separator Only** — changes channel separators only. It does not change icons, font, category frames, permissions, or order.",
        )),
        inline=False,
    )

    embed.add_field(
        name="Edit one thing",
        value="\n".join((
            "🗂️ **Category Editor** — preview, rename, style, lock, or unlock one category.",
            "#️⃣ **Channel Editor** — preview, rename, style, lock, or unlock one channel.",
        )),
        inline=False,
    )

    embed.add_field(
        name="Detected live style",
        value="\n".join((
            f"Separator: **{_safe_str(live_summary.get('separator'), 'mixed/unknown')}**",
            f"Categories: **{_safe_str(live_summary.get('category_frame'), 'mixed/unknown')}**",
            f"Font/style: **{_safe_str(live_summary.get('font'), 'mixed/unknown')}**",
            f"Leading emoji: **{_safe_str(live_summary.get('leading_emoji'), 'mixed/unknown')}**",
            f"Confidence: **{_majority_confidence_line(live_summary)}**",
        ))[:1024],
        inline=False,
    )

    embed.add_field(
        name="Saved design rule",
        value="\n".join((
            f"Theme: **{saved['theme']}**",
            f"Font: **{saved['font']}**",
            f"Strength: **{saved['strength']}**",
            "Used by Preview Saved Design and manual saved rules.",
        )),
        inline=True,
    )

    embed.add_field(
        name="Saved rules / locks",
        value="\n".join((
            f"Global preset: **{'On' if counts['global'] else 'Off'}**",
            f"Locked category rules: **{counts['categories']}**",
            f"Locked channel overrides: **{counts['channels']}**",
            "Open **Rules & Unlocks** to see the exact preset each category/channel follows or unlock it.",
        )),
        inline=True,
    )

    embed.set_footer(text="Names only • Saved rules win • Live detection is preview-only when saved rules exist")
    return _clean_design_embed(embed)
'''


FORMAT_LOCKS_EMBED = r'''
def _format_locks_embed(guild: discord.Guild, options: Mapping[str, Any]) -> discord.Embed:
    counts = _lock_count(options)
    theme = _theme_from_options(options)
    current_lock = _current_format_lock(options)

    font = _safe_str(current_lock.get("font"), "normal").replace("_", " ").title()
    sep = _safe_str(current_lock.get("separator_id"), "bar_full").replace("_", " ").title()
    frame = _safe_str(current_lock.get("category_frame_id"), "line").replace("_", " ").title()
    strength = _safe_int(current_lock.get("strength"), 4)

    embed = discord.Embed(
        title="🔐 Lock / Unlock Saved Rules",
        description=(
            "Review exactly which preset is locked for global, categories, and channels. "
            "Unlock individual rules or clean stale/deleted targets. Nothing is permanent."
        ),
        color=discord.Color.blurple(),
    )

    embed.add_field(
        name="Current global preset",
        value="\n".join((
            f"Theme: **{getattr(theme, 'label', 'Gothic Clean')}**",
            f"Font: **{font}**",
            f"Separator: **{sep}**",
            f"Category frame: **{frame}**",
            f"Strength: **{strength}/5**",
        )),
        inline=False,
    )

    embed.add_field(
        name="Saved rules / locks",
        value="\n".join((
            f"Global preset: **{'On' if counts['global'] else 'Off'}**",
            f"Locked category rules: **{counts['categories']}**",
            f"Locked channel overrides: **{counts['channels']}**",
        )),
        inline=True,
    )

    embed.add_field(
        name="What each rule means",
        value="\n".join((
            "Category rule preset shows: Font `{font}` • Separator `{sep}` • Frame `{frame}` • Strength `{strength}/5`.",
            "Channel override preset shows: Font `{font}` • Separator `{sep}` • Strength `{strength}/5`.",
            "Priority: Protection policy → Channel override → Category rule → Global preset → Detected live style preview.",
            "Protected Names / Unlock controls protected ticket/log/system names separately.",
        )),
        inline=False,
    )

    embed.set_footer(text="Use numbered buttons to unlock one saved rule. Nothing is permanent.")
    return _clean_design_embed(embed)
'''


REPLACEMENTS = {
    "Fix Mismatched Names": "Review Name Drift",
    "Fix Inconsistencies": "Review Name Drift",
    "Review Repairs": "Review Name Drift",
    "Change One Style": "Change Channel Separator Only",
    "Change Separator Only": "Change Channel Separator Only",
    "Editors & Locks": "Rules & Unlocks",
    "More Tools": "Rules & Unlocks",
    "Manage Saved Rules": "Unlock Saved Rules",
    "Manage Locks": "Unlock Saved Rules",
    "Format Lock Manager": "Lock / Unlock Saved Rules",
    "Remove {display_index}.": "Unlock {display_index}.",
    "Save Category Layout": "Lock Category Rule",
    "Save Channel Layout": "Lock Channel Rule",
    "Category Rule Saved": "Category Rule Locked",
    "Channel Rule Saved": "Channel Rule Locked",
    "Preview/Change One Style/Custom Format": "Preview/Change Separator/Custom Format",
    "✅ Change One Style Applied": "✅ Separator Change Applied",
    "Rename Protection": "Protected Names / Unlock",
    "Protected item → Channel lock → Category lock → Global lock → Auto theme": (
        "Protection policy → Channel override → Category rule → Global preset → Detected live style preview"
    ),
    "Protected item → Channel override → Category lock → Global lock → Auto theme": (
        "Protection policy → Channel override → Category rule → Global preset → Detected live style preview"
    ),
}


def patch_public() -> None:
    text = PUBLIC.read_text(encoding="utf-8")

    text = replace_function(text, "_home_embed", HOME_EMBED)
    text = replace_function(text, "_format_locks_embed", FORMAT_LOCKS_EMBED)

    for old, new in REPLACEMENTS.items():
        text = text.replace(old, new)

    # Normalize duplicate wording from older appliers.
    for _ in range(4):
        text = text.replace("Change Channel Separator Only Only", "Change Channel Separator Only")
        text = text.replace("⚡ Change Channel Separator Only Only", "⚡ Change Channel Separator Only")

    # Separator tool copy: clear and exact.
    text = text.replace(
        "Change **one visual rule** while keeping the rest of the server style the same.\\n\\n"
        "\"\n            \"**Current tool:** Channel Separator\\n"
        "\"\n            \"Choosing a separator only updates this draft. Use **Preview This Change** next, then **Apply Reviewed Changes**.",
        "This tool changes only the **separator between an existing icon and channel name**.\\n\\n"
        "\"\n            \"It keeps current emoji/icons, font, category frames, permissions, tickets, verification, and channel order unchanged.\\n"
        "\"\n            \"Use **Preview This Change** next, then **Apply Reviewed Changes**.",
    )

    text = text.replace(
        '"How to fix",\n            value="\\n".join(_style_change_issue_lines(items))[:1024],',
        '"How to fix next",\n            value="\\n".join(_style_change_issue_lines(items))[:1024],',
    )

    required = (
        "Recommended workflow",
        "Review Name Drift",
        "Change Channel Separator Only",
        "Rules & Unlocks",
        "Lock / Unlock Saved Rules",
        "Unlock Saved Rules",
        "Saved rules / locks",
        "Locked category rules",
        "Locked channel overrides",
        "Nothing is permanent",
        "Frame `{frame}`",
        "Separator `{sep}`",
        "Strength `{strength}/5`",
        "Protected Names / Unlock",
        "Protection policy → Channel override → Category rule → Global preset",
    )
    missing = [token for token in required if token not in text]
    if missing:
        raise SystemExit("Missing required UX tokens: " + ", ".join(missing))

    forbidden = (
        "Fix Mismatched Names",
        "Change One Style",
        "Editors & Locks",
        "Format Lock Manager",
        "Review Repairs",
        "Rename Protection",
    )
    remaining = [token for token in forbidden if token in text]
    if remaining:
        raise SystemExit("Old confusing wording still remains in public source: " + ", ".join(remaining))

    PUBLIC.write_text(text, encoding="utf-8")
    print("✅ public_design_studio.py rewritten safely")


def patch_tests() -> None:
    for path in (SAFE_TEST, UX_TEST):
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")

        for old, new in REPLACEMENTS.items():
            text = text.replace(old, new)

        for _ in range(4):
            text = text.replace("Change Channel Separator Only Only", "Change Channel Separator Only")

        text = text.replace("Saved rules win", "Saved rules / locks")
        text = text.replace("Live Majority is preview-only when locks exist", "live detection is preview-only when saved rules exist")
        text = text.replace("reviews saved rules first", "compares names against saved category/channel rules")
        text = text.replace("How to fix", "How to fix next")

        forbidden = (
            "Fix Mismatched Names",
            "Change One Style",
            "Editors & Locks",
            "Format Lock Manager",
            "Review Repairs",
            "Rename Protection",
        )
        remaining = [token for token in forbidden if token in text]
        if remaining:
            raise SystemExit(f"Old wording remains in {path}: " + ", ".join(remaining))

        path.write_text(text, encoding="utf-8")
        print(f"✅ updated {path.relative_to(ROOT)}")


def main() -> None:
    patch_public()
    patch_tests()
    print("✅ Dank Design UX and lock/unlock cleanup verified")


if __name__ == "__main__":
    main()
