from __future__ import annotations

"""Make Dank Design repair safe for already-styled servers.

The problem this fixes:
- Review Design Repairs / Fix Mismatched Names can show a Live Majority preview
  even when saved format locks already exist.
- Live Majority is a guess across the visible server. It must not be able to
  apply over saved rules or low/review confidence.
- Saved rules/locks are the owner-approved source of truth when present.

Run from repo root:
    python tools/apply_dank_design_safe_repair_cleanup.py
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "stoney_verify/commands_ext/public_design_studio.py"
MAJORITY_GUARD = ROOT / "stoney_verify/startup_guards/server_design_majority_layout_guard.py"

PUBLIC_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    (
        "repair_options = majority.apply_majority_to_options(studio, options, analysis, respect_locks=False)",
        "repair_options = majority.apply_majority_to_options(studio, options, analysis, respect_locks=True)",
    ),
    (
        "🧭 **Fix Mismatched Names** — copies the live server style and fixes only names that do not match.",
        "🧭 **Fix Mismatched Names** — reviews saved rules first; Live Majority is preview-only when saved rules exist.",
    ),
    (
        "Fix Mismatched Names ignores saved rules unless you choose saved layout.",
        "Fix Mismatched Names protects saved rules; use Live Majority only as a manual preview.",
    ),
    (
        "Names only • Fix Mismatched Names follows live style • Preview Saved Design follows saved rules",
        "Names only • Saved rules win • Live Majority is preview-only when locks exist",
    ),
)

GUARD_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    (
        '@discord.ui.button(label="Use Live Majority", emoji="✅", style=discord.ButtonStyle.success, custom_id="dank_design:majority_use_live", row=0)',
        '@discord.ui.button(label="Preview Live Majority", emoji="👁️", style=discord.ButtonStyle.secondary, custom_id="dank_design:majority_use_live", row=0)',
    ),
    (
        '"Saved rules found: **{_saved_rule_count(options)}**\\nUse saved rules only when those are intentionally correct."',
        '"Saved rules found: **{_saved_rule_count(options)}**\\nSaved rules/locks are owner-approved. Use them unless you are only previewing Live Majority."',
    ),
    (
        '"Use **Live Majority** only when the preview keeps the server\'s look. "\n                "If it would flatten styled names, choose **Manual Editor** or **Saved Layout**."',
        '"Use **Saved Layout** when saved rules exist. "\n                "Live Majority is only a preview unless there are no saved locks and confidence is high."',
    ),
    (
        'embed.add_field(name="Saved rules found", value=f"{found} saved rule(s) exist. This preview uses **Live Majority** because you chose it.", inline=False)',
        'embed.add_field(name="Saved rules found", value=f"{found} saved rule(s) exist. **Apply is disabled for Live Majority** so saved rules cannot be bypassed here. Use **Saved Layout** or Manual Editor.", inline=False)',
    ),
)

OLD_CAN_APPLY_BLOCK = '''            counts = _counts(command_guard, items)
            command_guard._PENDING[command_guard._key(int(guild.id), int(interaction.user.id))] = {
                "created_at": command_guard.time.time(),
                "items": items,
                "options": dict(requested),
                "mode": "consistency_live_majority",
            }
            await interaction.edit_original_response(
                embed=command_guard._consistency_embed(guild, items, requested),
                view=command_guard.DesignPreviewView(
                    can_apply=(
                        not counts.get("failed")
                        and bool(counts.get("needs_fix"))
                        and not _majority_apply_blocked(items)
                    )
                ),
            )
'''

NEW_CAN_APPLY_BLOCK = '''            counts = _counts(command_guard, items)
            saved_rules = _saved_rule_count(options)
            live_apply_allowed = (
                bool(confidence.get("apply_allowed"))
                and not counts.get("failed")
                and bool(counts.get("needs_fix"))
                and not _majority_apply_blocked(items)
                and saved_rules == 0
            )
            if saved_rules:
                requested["__live_majority_apply_disabled_by_saved_rules"] = saved_rules
            command_guard._PENDING[command_guard._key(int(guild.id), int(interaction.user.id))] = {
                "created_at": command_guard.time.time(),
                "items": items,
                "options": dict(requested),
                "mode": "consistency_live_majority_preview_only" if saved_rules else "consistency_live_majority",
            }
            await interaction.edit_original_response(
                embed=command_guard._consistency_embed(guild, items, requested),
                view=command_guard.DesignPreviewView(can_apply=live_apply_allowed),
            )
'''

OLD_GUARD_PLAN_BLOCK = '''            records = _records_for_guild(command_guard, guild)
            analysis = majority.infer_live_majority_layout(studio, records)
            inferred = majority.apply_majority_to_options(studio, options, analysis, respect_locks=False)
            items = await original(guild, inferred)
            return majority.annotate_plan_items(items, analysis, inferred, studio=studio)
'''

NEW_GUARD_PLAN_BLOCK = '''            records = _records_for_guild(command_guard, guild)
            analysis = majority.infer_live_majority_layout(studio, records)
            respect_saved_locks = bool(_saved_rule_count(options))
            inferred = majority.apply_majority_to_options(studio, options, analysis, respect_locks=respect_saved_locks)
            if respect_saved_locks:
                inferred["__live_majority_apply_disabled_by_saved_rules"] = _saved_rule_count(options)
            items = await original(guild, inferred)
            return majority.annotate_plan_items(items, analysis, inferred, studio=studio)
'''


def replace_all(path: Path, replacements: tuple[tuple[str, str], ...]) -> bool:
    text = path.read_text(encoding="utf-8")
    original = text
    for old, new in replacements:
        text = text.replace(old, new)
    if text != original:
        path.write_text(text, encoding="utf-8")
        return True
    return False


def replace_required(path: Path, old: str, new: str, label: str) -> bool:
    text = path.read_text(encoding="utf-8")
    if old in text:
        path.write_text(text.replace(old, new), encoding="utf-8")
        print(f"✅ patched {label}: {path.relative_to(ROOT)}")
        return True
    if new in text:
        print(f"✅ already patched {label}: {path.relative_to(ROOT)}")
        return False
    raise SystemExit(f"Could not find target block for {label} in {path.relative_to(ROOT)}")


def main() -> None:
    touched: list[str] = []
    if replace_all(PUBLIC, PUBLIC_REPLACEMENTS):
        touched.append(str(PUBLIC.relative_to(ROOT)))
    if replace_all(MAJORITY_GUARD, GUARD_REPLACEMENTS):
        touched.append(str(MAJORITY_GUARD.relative_to(ROOT)))
    if replace_required(MAJORITY_GUARD, OLD_CAN_APPLY_BLOCK, NEW_CAN_APPLY_BLOCK, "Live Majority apply gate"):
        touched.append(str(MAJORITY_GUARD.relative_to(ROOT)))
    if replace_required(MAJORITY_GUARD, OLD_GUARD_PLAN_BLOCK, NEW_GUARD_PLAN_BLOCK, "saved-lock-aware majority plan"):
        touched.append(str(MAJORITY_GUARD.relative_to(ROOT)))

    public_text = PUBLIC.read_text(encoding="utf-8")
    guard_text = MAJORITY_GUARD.read_text(encoding="utf-8")

    required = (
        "respect_locks=True",
        "Saved rules win",
        "Preview Live Majority",
        "live_apply_allowed",
        "saved_rules == 0",
        "__live_majority_apply_disabled_by_saved_rules",
        "respect_saved_locks = bool(_saved_rule_count(options))",
    )
    missing = [token for token in required if token not in public_text + guard_text]
    if missing:
        raise SystemExit("Dank Design safe repair cleanup missing expected tokens: " + ", ".join(missing))

    print("✅ Dank Design safe repair cleanup complete")
    for item in sorted(set(touched)):
        print(f" - {item}")


if __name__ == "__main__":
    main()
