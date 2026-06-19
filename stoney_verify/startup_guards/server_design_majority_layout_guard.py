from __future__ import annotations

"""Live-majority repair bridge for Dank Design.

The parser and detector live in stoney_verify.services.server_design_majority_layout.
This bridge keeps the existing /dank design command flow and only changes the
Find & Fix Inconsistencies naming plan.
"""

from collections.abc import Mapping
import inspect
from typing import Any

_PATCHED = False


def _text(value: Any, default: str = "") -> str:
    try:
        if value is None:
            return default
        text = str(value).strip()
        return text if text else default
    except Exception:
        return default


def _is_consistency_repair(options: Mapping[str, Any]) -> bool:
    if bool(options.get("__use_live_majority_layout")):
        return True
    try:
        for frame in inspect.stack(context=0)[1:10]:
            if frame.function == "consistency" and "server_design_studio_command_guard" in frame.filename:
                return True
    except Exception:
        pass
    return False


def _records_for_guild(command_guard: Any, guild: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for channel in list(command_guard._editable_channels(guild) or []):
        kind = command_guard._kind(channel)
        parent = getattr(channel, "category", None)
        records.append(
            {
                "id": str(getattr(channel, "id", "")),
                "category_id": str(getattr(parent, "id", "")),
                "kind": kind,
                "name": _text(getattr(channel, "name", "")),
            }
        )
    return records


def _change_lines(command_guard: Any, items: list[dict[str, Any]], *, limit: int = 12) -> list[str]:
    try:
        return command_guard._consistency_lines(items, limit=limit)
    except Exception:
        rows: list[str] = []
        for item in items:
            if item.get("status") != "changed":
                continue
            before = _text(item.get("before"))
            after = _text(item.get("after"))
            rows.append(f"🧩 `{before}` → `{after}`"[:240])
            if len(rows) >= limit:
                break
        return rows or ["No inconsistent channel names found."]


def _patch_consistency_embed(command_guard: Any, majority: Any, discord: Any) -> None:
    if getattr(command_guard, "_DANK_MAJORITY_LAYOUT_EMBED_ACTIVE", False):
        return

    def _majority_consistency_embed(guild: Any, items: list[dict[str, Any]], options: Mapping[str, Any]) -> Any:
        try:
            summary = command_guard._consistency_summary(items)
        except Exception:
            summary = {"matches": 0, "needs_fix": 0, "protected": 0, "failed": 0, "notes": 0}

        detected = majority.majority_summary_from_items(items) or {
            "separator": "mixed/unknown",
            "category_frame": "mixed/unknown",
            "font": "mixed/unknown",
            "leading_emoji": "mixed/unknown",
        }
        majority_first_count, saved_first_count = majority.lock_notice_from_items(items)
        has_failures = bool(summary.get("failed"))
        embed = discord.Embed(
            title="🧭 Server Design Consistency Check",
            description=(
                "Dank Shield compared the current live server layout and will repair only names that drift from the majority.\n\n"
                "Nothing has been changed yet. Review the before/after list, then press Apply if it looks right."
            ),
            color=discord.Color.orange() if has_failures else discord.Color.green(),
        )
        embed.add_field(
            name="Live majority detected",
            value=(
                f"Majority separator detected: **{detected.get('separator', 'mixed/unknown')}**\n"
                f"Majority category frame detected: **{detected.get('category_frame', 'mixed/unknown')}**\n"
                f"Majority font/style detected: **{detected.get('font', 'mixed/unknown')}**\n"
                f"Leading emoji usage: **{detected.get('leading_emoji', 'mixed/unknown')}**"
            )[:1024],
            inline=False,
        )
        embed.add_field(
            name="Results",
            value=(
                f"Channels matching: **{summary.get('matches', 0)}**\n"
                f"Channels needing repair: **{summary.get('needs_fix', 0)}**\n"
                f"Protected/skipped: **{summary.get('protected', 0)}**\n"
                f"Cannot fix yet: **{summary.get('failed', 0)}**\n"
                f"Notes: **{summary.get('notes', 0)}**"
            ),
            inline=True,
        )
        embed.add_field(name="What will be fixed", value="\n".join(_change_lines(command_guard, items, limit=12))[:1024], inline=False)

        skipped = majority.skipped_lines(items, limit=6)
        if skipped:
            embed.add_field(name="Protected / skipped", value="\n".join(skipped)[:1024], inline=False)

        if majority_first_count:
            embed.add_field(
                name="Saved layout note",
                value=(
                    f"**{majority_first_count}** saved layout rule(s) were present. For this repair preview, the live majority layout is taking precedence so hand-built servers can be normalized. "
                    "Use **Manage Locks** when a saved layout should intentionally win."
                )[:1024],
                inline=False,
            )
        elif saved_first_count:
            embed.add_field(
                name="Saved layout rule active",
                value=(
                    f"**{saved_first_count}** saved layout rule(s) are taking precedence over live majority detection. "
                    "Clear that saved rule when you want the majority layout copied instead."
                )[:1024],
                inline=False,
            )

        try:
            failed_lines = command_guard.studio.preview_lines(items, filter_mode="failed", limit=5)
        except Exception:
            failed_lines = []
        if failed_lines and failed_lines != ["No matching preview rows."]:
            embed.add_field(name="Cannot fix yet", value="\n".join(failed_lines)[:1024], inline=False)

        embed.set_footer(text="Fix uses preview first, names only, and the existing rollback snapshot before apply.")
        return command_guard._clean_design_embed(embed)

    command_guard._consistency_embed = _majority_consistency_embed
    command_guard._DANK_MAJORITY_LAYOUT_EMBED_ACTIVE = True


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        import sys

        from stoney_verify.services import server_design_majority_layout as majority
        from stoney_verify.services import server_design_studio as studio

        command_guard = sys.modules.get("stoney_verify.startup_guards.server_design_studio_command_guard")
        if command_guard is None:
            return False
        if getattr(command_guard, "_DANK_MAJORITY_LAYOUT_PLAN_ACTIVE", False):
            _patch_consistency_embed(command_guard, majority, command_guard.discord)
            _PATCHED = True
            return True

        original = getattr(command_guard, "build_design_plan", None)
        if not callable(original):
            return False

        async def _build_design_plan_with_majority(guild: Any, options: Mapping[str, Any]) -> list[dict[str, Any]]:
            if not _is_consistency_repair(options):
                return await original(guild, options)

            records = _records_for_guild(command_guard, guild)
            analysis = majority.infer_live_majority_layout(studio, records)
            inferred = majority.apply_majority_to_options(studio, options, analysis, respect_locks=False)
            items = await original(guild, inferred)
            return majority.annotate_plan_items(items, analysis, inferred)

        command_guard.build_design_plan = _build_design_plan_with_majority
        command_guard._DANK_MAJORITY_LAYOUT_PLAN_ACTIVE = True
        _patch_consistency_embed(command_guard, majority, command_guard.discord)
        _PATCHED = True
        print("✅ server_design_majority_layout_guard active; Fix Mismatches copies the live majority layout")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ server_design_majority_layout_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
