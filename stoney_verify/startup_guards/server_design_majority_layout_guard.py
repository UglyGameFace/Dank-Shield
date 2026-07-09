from __future__ import annotations

"""Guided live-majority repair bridge for Dank Design."""

from collections.abc import Mapping
import inspect
import re
from typing import Any

from stoney_verify.services import server_design_repair_confidence as repair_confidence

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
            if frame.function == "consistency" and (
                "server_design_studio_command_guard" in frame.filename
                or "public_design_studio" in frame.filename
            ):
                return True
    except Exception:
        pass
    return False


def _records_for_guild(command_guard: Any, guild: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for channel in list(command_guard._editable_channels(guild) or []):
        parent = getattr(channel, "category", None)
        rows.append({
            "id": str(getattr(channel, "id", "")),
            "category_id": str(getattr(parent, "id", "")),
            "kind": command_guard._kind(channel),
            "name": _text(getattr(channel, "name", "")),
        })
    return rows


def _analysis_summary(analysis: Mapping[str, Any]) -> dict[str, str]:
    separator = analysis.get("separator") if isinstance(analysis.get("separator"), Mapping) else {}
    frame = analysis.get("category_frame") if isinstance(analysis.get("category_frame"), Mapping) else {}
    font = analysis.get("font") if isinstance(analysis.get("font"), Mapping) else {}
    emoji = analysis.get("leading_emoji") if isinstance(analysis.get("leading_emoji"), Mapping) else {}
    return {
        "separator": _text(separator.get("label"), "mixed/unknown"),
        "category_frame": _text(frame.get("label"), "mixed/unknown"),
        "font": _text(font.get("label"), "mixed/unknown"),
        "leading_emoji": _text(emoji.get("label"), "mixed/unknown"),
    }


def _counts(command_guard: Any, items: list[dict[str, Any]]) -> dict[str, int]:
    try:
        summary = command_guard._consistency_summary(items)
        return {key: int(summary.get(key, 0)) for key in ("matches", "needs_fix", "protected", "failed", "notes")}
    except Exception:
        return {
            "matches": sum(1 for item in items if item.get("status") == "unchanged"),
            "needs_fix": sum(1 for item in items if item.get("status") == "changed"),
            "protected": sum(1 for item in items if item.get("status") == "protected"),
            "failed": sum(1 for item in items if item.get("status") == "failed"),
            "notes": sum(1 for item in items if item.get("warnings")),
        }


def _change_lines(items: list[dict[str, Any]], *, limit: int = 8) -> list[str]:
    out: list[str] = []
    for item in items:
        if item.get("status") != "changed":
            continue
        before = _text(item.get("before"), "unnamed")
        after = _text(item.get("after"), "unnamed")
        out.append(f"• `{before}`\n  → `{after}`"[:240])
        if len(out) >= limit:
            break
    return out or ["No repair rows for this target."]


_DECORATIVE_HEADING_MARKS = set("─━═╭╮╰╯╔╗【】「」✦⋆｡°✩🏁🛠🛡🎫📦")


def _looks_display_heading(value: Any) -> bool:
    text = _text(value)
    if not text:
        return False

    if any(ch in _DECORATIVE_HEADING_MARKS for ch in text):
        return True

    if " " in text or "/" in text:
        return True

    # Styled unicode letters are common in hand-designed category names.
    if any(ord(ch) > 127 and ch.isalpha() for ch in text):
        return True

    letters = [ch for ch in text if ch.isalpha()]
    if len(letters) >= 3:
        upper_ratio = sum(1 for ch in letters if ch.isupper()) / max(1, len(letters))
        if upper_ratio >= 0.55:
            return True

    # Multiple non-word marks usually means this is a visual heading, not a slug.
    marks = re.findall(r"[^\w\s-]", text, flags=re.UNICODE)
    return len(marks) >= 2


def _looks_plain_slug(value: Any) -> bool:
    text = _text(value).strip()
    if not text:
        return False

    # Ignore a leading emoji/icon when deciding if the rest became a slug.
    core = re.sub(r"^[^\w#]+", "", text, flags=re.UNICODE).strip()
    if not core:
        return False

    return (
        " " not in core
        and core == core.lower()
        and ("-" in core or core.islower())
        and not any(ch in _DECORATIVE_HEADING_MARKS for ch in core)
    )


def _visual_downgrade_items(items: list[dict[str, Any]], *, limit: int = 6) -> list[str]:
    """Rows where Live Majority would make a designed section/category plainer."""

    out: list[str] = []
    for item in items:
        if item.get("status") != "changed":
            continue

        kind = _text(item.get("kind"), "text")
        before = _text(item.get("before"))
        after = _text(item.get("after"))

        if not before or not after or before == after:
            continue

        if kind == "category" and _looks_display_heading(before) and _looks_plain_slug(after):
            out.append(f"• `{before}`\n  → `{after}`"[:240])
            if len(out) >= limit:
                break

    return out


def _majority_apply_blocked(items: list[dict[str, Any]]) -> bool:
    """Block Apply when the preview mostly simplifies hand-designed sections."""

    downgrade_count = len(_visual_downgrade_items(items, limit=50))
    changed_count = sum(1 for item in items if item.get("status") == "changed")

    if downgrade_count >= 3:
        return True

    return bool(changed_count and downgrade_count >= max(2, changed_count // 3))


def _saved_rule_count(options: Mapping[str, Any]) -> int:
    total = 0
    global_rule = options.get("format_lock_global")
    if isinstance(global_rule, Mapping) and global_rule.get("enabled"):
        total += 1
    for key in ("category_format_locks", "channel_format_locks"):
        value = options.get(key)
        if isinstance(value, Mapping):
            total += len(value)
    return total



def _repair_mode_recommendation_text() -> str:
    return (
        "Start with **Fix Only Obvious Mistakes** for styled servers. "
        "Use **Live Majority** only when the preview keeps the current server look. "
        "Use **Saved Layout** when this server already has approved Dank Design rules."
    )

def _patch_consistency_embed(command_guard: Any, majority: Any, discord: Any) -> None:
    def _majority_consistency_embed(guild: Any, items: list[dict[str, Any]], options: Mapping[str, Any]) -> Any:
        counts = _counts(command_guard, items)
        confidence = options.get('__repair_confidence_result') if isinstance(options.get('__repair_confidence_result'), dict) else repair_confidence.evaluate_repair_plan(items, context='live_majority')
        confidence_apply_allowed = bool(confidence.get('apply_allowed'))
        detected = majority.majority_summary_from_items(items) or {
            "separator": "mixed/unknown",
            "category_frame": "mixed/unknown",
            "font": "mixed/unknown",
            "leading_emoji": "mixed/unknown",
        }
        downgrade_lines = _visual_downgrade_items(items, limit=6)
        apply_blocked = _majority_apply_blocked(items)

        embed = discord.Embed(
            title="✅ Live Majority Repair Preview" if confidence_apply_allowed else "⚠️ Live Majority Needs Review",
            description=(
                "**Step 2 of 2 — review before apply.**\n"
                "Target: the layout most channels/categories already use here.\n\n"
                + (
                    "Apply is blocked because this preview would simplify styled section names. "
                    "Use **Manual Editor** or **Saved Layout** instead."
                    if apply_blocked
                    else "Apply only renames the safer rows in this preview."
                )
            ),
            color=discord.Color.orange() if apply_blocked or counts.get("failed") else discord.Color.green(),
        )
        embed.add_field(
            name="Detected target layout",
            value=(
                f"Separator: **{detected.get('separator', 'mixed/unknown')}**\n"
                f"Category frame: **{detected.get('category_frame', 'mixed/unknown')}**\n"
                f"Font/style: **{detected.get('font', 'mixed/unknown')}**\n"
                f"Leading emoji: **{detected.get('leading_emoji', 'mixed/unknown')}**"
            )[:1024],
            inline=False,
        )
        embed.add_field(
            name="Summary",
            value=(
                f"Already matching: **{counts.get('matches', 0)}**\n"
                f"Safe repairs: **{counts.get('needs_fix', 0)}**\n"
                f"Skipped: **{counts.get('protected', 0)}**\n"
                f"Cannot repair: **{counts.get('failed', 0)}**\n"
                f"Notes: **{counts.get('notes', 0)}**"
            ),
            inline=True,
        )
        embed.add_field(name="Repair confidence", value=repair_confidence.confidence_summary_text(confidence), inline=True)
        blocked_lines = list(confidence.get("blocked_lines") or [])
        review_lines = list(confidence.get("review_lines") or [])
        if blocked_lines:
            embed.add_field(name="Blocked by design safety", value="\n".join(str(line) for line in blocked_lines)[:1024], inline=False)
        if review_lines:
            embed.add_field(name="Needs review", value="\n".join(str(line) for line in review_lines)[:1024], inline=False)
        embed.add_field(name="Sample safe repairs", value="\n".join(_change_lines(items, limit=8))[:1024], inline=False)
        if apply_blocked and downgrade_lines:
            embed.add_field(
                name="Apply blocked — would simplify this server",
                value=(
                    "These look like designed section/category names, not mistakes:\n"
                    + "\n".join(downgrade_lines)
                )[:1024],
                inline=False,
            )
        skipped = majority.skipped_lines(items, limit=5)
        if skipped:
            embed.add_field(name="Skipped on purpose", value="\n".join(skipped)[:1024], inline=False)
        found, active = majority.lock_notice_from_items(items)
        if found:
            embed.add_field(name="Saved rules found", value=f"{found} saved rule(s) exist. **Apply is disabled for Live Majority** so saved rules cannot be bypassed here. Use **Saved Layout** or Manual Editor.", inline=False)
        elif active:
            embed.add_field(name="Saved rules active", value=f"{active} saved rule(s) are active for this preview.", inline=False)
        embed.set_footer(text="Names only • Preview first • Apply disabled when confidence is low")
        return command_guard._clean_design_embed(embed)

    command_guard._consistency_embed = _majority_consistency_embed
    command_guard._DANK_MAJORITY_LAYOUT_EMBED_ACTIVE = True


def _patch_guided_flow(command_guard: Any, majority: Any, studio: Any, discord: Any) -> None:
    if getattr(command_guard, "_DANK_GUIDED_MAJORITY_REPAIR_ACTIVE", False):
        return

    async def _load_options(guild_id: int) -> dict[str, Any]:
        return await command_guard._load_design_options(int(guild_id))

    async def _majority_items(guild: Any, options: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        requested = dict(options)
        requested["__use_live_majority_layout"] = True
        items = await command_guard.build_design_plan(guild, requested)
        return items, requested

    def _target_embed(guild: Any, options: Mapping[str, Any], items: list[dict[str, Any]]) -> Any:
        counts = _counts(command_guard, items)
        detected = majority.majority_summary_from_items(items)
        if not detected:
            analysis = majority.infer_live_majority_layout(studio, _records_for_guild(command_guard, guild))
            detected = _analysis_summary(analysis)
        embed = discord.Embed(
            title="🧭 Choose Repair Target",
            description="**Step 1 of 2.** Pick what Dank Design should copy before any apply button appears.",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Live majority detected",
            value=(
                f"Separator: **{detected.get('separator', 'mixed/unknown')}**\n"
                f"Category frame: **{detected.get('category_frame', 'mixed/unknown')}**\n"
                f"Font/style: **{detected.get('font', 'mixed/unknown')}**\n"
                f"Leading emoji: **{detected.get('leading_emoji', 'mixed/unknown')}**"
            )[:1024],
            inline=False,
        )
        embed.add_field(
            name="Using live majority would",
            value=(
                f"Keep matching: **{counts.get('matches', 0)}**\n"
                f"Repair: **{counts.get('needs_fix', 0)}**\n"
                f"Skip: **{counts.get('protected', 0)}**\n"
                f"Need attention: **{counts.get('failed', 0)}**"
            ),
            inline=True,
        )
        embed.add_field(
            name="Saved rules",
            value=f"Saved rules found: **{_saved_rule_count(options)}**\nSaved rules/locks are owner-approved. Use them unless you are only previewing Live Majority.",
            inline=True,
        )
        embed.add_field(
            name="Recommended",
            value=(
                "Use **Saved Layout** when saved rules exist. "
                "Live Majority is only a preview unless there are no saved locks and confidence is high."
            ),
            inline=False,
        )
        embed.add_field(name="Recommended", value=_repair_mode_recommendation_text(), inline=False)
        embed.set_footer(text="Read-only screen. Choose a target to generate the final preview.")
        return command_guard._clean_design_embed(embed)

    def _saved_embed(items: list[dict[str, Any]]) -> Any:
        counts = _counts(command_guard, items)
        embed = discord.Embed(
            title="🔒 Saved Layout Preview",
            description="**Step 2 of 2 — review before apply.** Target: saved theme/rules.",
            color=discord.Color.orange() if counts.get("failed") else discord.Color.blurple(),
        )
        embed.add_field(
            name="Summary",
            value=(
                f"Matches saved layout: **{counts.get('matches', 0)}**\n"
                f"Safe repairs: **{counts.get('needs_fix', 0)}**\n"
                f"Skipped: **{counts.get('protected', 0)}**\n"
                f"Cannot repair: **{counts.get('failed', 0)}**"
            ),
            inline=True,
        )
        embed.add_field(name="Sample safe repairs", value="\n".join(_change_lines(items, limit=8))[:1024], inline=False)
        embed.set_footer(text="Names only • Preview first • Rollback snapshot kept before apply")
        return command_guard._clean_design_embed(embed)

    class RepairTargetView(discord.ui.View):
        def __init__(self) -> None:
            super().__init__(timeout=900)

        @discord.ui.button(label="Preview Live Majority", emoji="👁️", style=discord.ButtonStyle.secondary, custom_id="dank_design:majority_use_live", row=0)
        async def use_live_majority(self, interaction: Any, button: Any) -> None:
            if not await command_guard._require_design_permission(interaction):
                return
            guild = interaction.guild
            assert guild is not None
            await interaction.response.defer(ephemeral=True, thinking=True)
            options = await _load_options(int(guild.id))
            items, requested = await _majority_items(guild, options)
            confidence = repair_confidence.evaluate_repair_plan(items, context='live_majority')
            requested['__repair_confidence_result'] = dict(confidence)
            if not bool(confidence.get('apply_allowed')):
                for _item in items:
                    if _item.get('status') == 'changed':
                        _item.setdefault('blockers', []).append('Repair confidence blocked automatic apply. Review this row or use Manual Editor/Saved Layout.')
                        _item['status'] = 'failed'
                        _item['repair_confidence_blocked'] = True
            counts = _counts(command_guard, items)
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

        @discord.ui.button(label="Use Saved Layout", emoji="🔒", style=discord.ButtonStyle.secondary, custom_id="dank_design:majority_use_saved", row=0)
        async def use_saved_layout(self, interaction: Any, button: Any) -> None:
            if not await command_guard._require_design_permission(interaction):
                return
            guild = interaction.guild
            assert guild is not None
            await interaction.response.defer(ephemeral=True, thinking=True)
            options = await _load_options(int(guild.id))
            items = await command_guard.build_design_plan(guild, options)
            counts = _counts(command_guard, items)
            command_guard._PENDING[command_guard._key(int(guild.id), int(interaction.user.id))] = {
                "created_at": command_guard.time.time(),
                "items": items,
                "options": dict(options),
                "mode": "consistency_saved_layout",
            }
            await interaction.edit_original_response(
                embed=_saved_embed(items),
                view=command_guard.DesignPreviewView(can_apply=not counts.get("failed") and bool(counts.get("needs_fix"))),
            )

        @discord.ui.button(label="Preview Only", emoji="👁️", style=discord.ButtonStyle.secondary, custom_id="dank_design:majority_preview_only", row=1)
        async def preview_only(self, interaction: Any, button: Any) -> None:
            if not await command_guard._require_design_permission(interaction):
                return
            guild = interaction.guild
            assert guild is not None
            await interaction.response.defer(ephemeral=True, thinking=True)
            options = await _load_options(int(guild.id))
            items, requested = await _majority_items(guild, options)
            await interaction.edit_original_response(embed=command_guard._consistency_embed(guild, items, requested), view=RepairTargetView())

        @discord.ui.button(label="Manual Editor", emoji="🎛️", style=discord.ButtonStyle.primary, custom_id="dank_design:majority_manual", row=1)
        async def manual_editor(self, interaction: Any, button: Any) -> None:
            if not await command_guard._require_design_permission(interaction):
                return
            guild = interaction.guild
            assert guild is not None
            await interaction.response.edit_message(embed=command_guard._channel_editor_embed(guild, page=0), view=command_guard.ChannelEditorPickerView(guild, page=0))

        @discord.ui.button(label="Cancel", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="dank_design:majority_cancel", row=4)
        async def cancel(self, interaction: Any, button: Any) -> None:
            if not await command_guard._require_design_permission(interaction):
                return
            guild = interaction.guild
            assert guild is not None
            options = await _load_options(int(guild.id))
            items = await command_guard.build_design_plan(guild, options)
            await interaction.response.edit_message(embed=command_guard._doctor_embed(guild, options, items), view=GuidedDesignDoctorView())

    class GuidedDesignDoctorView(discord.ui.View):
        def __init__(self) -> None:
            super().__init__(timeout=900)

        @discord.ui.button(label="Review Design Repairs", emoji="🧭", style=discord.ButtonStyle.success, custom_id="dank_design:doctor_consistency", row=0)
        async def consistency(self, interaction: Any, button: Any) -> None:
            if not await command_guard._require_design_permission(interaction):
                return
            guild = interaction.guild
            assert guild is not None
            await interaction.response.defer(ephemeral=True, thinking=True)
            options = await _load_options(int(guild.id))
            items, _requested = await _majority_items(guild, options)
            await interaction.edit_original_response(embed=_target_embed(guild, options, items), view=RepairTargetView())

        @discord.ui.button(label="Category Editor", emoji="🗂️", style=discord.ButtonStyle.primary, custom_id="dank_design:doctor_category", row=1)
        async def category_editor(self, interaction: Any, button: Any) -> None:
            if not await command_guard._require_design_permission(interaction):
                return
            guild = interaction.guild
            assert guild is not None
            await interaction.response.edit_message(embed=command_guard._category_editor_embed(guild, page=0), view=command_guard.CategoryEditorPickerView(guild, page=0))

        @discord.ui.button(label="Channel Editor", emoji="#️⃣", style=discord.ButtonStyle.primary, custom_id="dank_design:doctor_channel", row=1)
        async def channel_editor(self, interaction: Any, button: Any) -> None:
            if not await command_guard._require_design_permission(interaction):
                return
            guild = interaction.guild
            assert guild is not None
            await interaction.response.edit_message(embed=command_guard._channel_editor_embed(guild, page=0), view=command_guard.ChannelEditorPickerView(guild, page=0))

        @discord.ui.button(label="Back to Design Studio", emoji="🎨", style=discord.ButtonStyle.secondary, custom_id="dank_design:doctor_back", row=4)
        async def back(self, interaction: Any, button: Any) -> None:
            if not await command_guard._require_design_permission(interaction):
                return
            guild = interaction.guild
            assert guild is not None
            options = await _load_options(int(guild.id))
            await interaction.response.edit_message(embed=command_guard._home_embed(guild, options), view=command_guard.DesignHomeView(options))

    command_guard.DesignDoctorView = GuidedDesignDoctorView
    command_guard._DANK_GUIDED_MAJORITY_REPAIR_ACTIVE = True


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        import sys

        from stoney_verify.services import server_design_majority_layout as majority
        from stoney_verify.services import server_design_studio as studio

        command_guard = (
            sys.modules.get("stoney_verify.commands_ext.public_design_studio")
            or sys.modules.get("stoney_verify.startup_guards.server_design_studio_command_guard")
        )
        if command_guard is None:
            return False
        if getattr(command_guard, "_DANK_MAJORITY_LAYOUT_PLAN_ACTIVE", False):
            _patch_consistency_embed(command_guard, majority, command_guard.discord)
            _patch_guided_flow(command_guard, majority, studio, command_guard.discord)
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
            respect_saved_locks = bool(_saved_rule_count(options))
            inferred = majority.apply_majority_to_options(studio, options, analysis, respect_locks=respect_saved_locks)
            if respect_saved_locks:
                inferred["__live_majority_apply_disabled_by_saved_rules"] = _saved_rule_count(options)
            items = await original(guild, inferred)
            return majority.annotate_plan_items(items, analysis, inferred, studio=studio)

        command_guard.build_design_plan = _build_design_plan_with_majority
        command_guard._DANK_MAJORITY_LAYOUT_PLAN_ACTIVE = True
        _patch_consistency_embed(command_guard, majority, command_guard.discord)
        _patch_guided_flow(command_guard, majority, studio, command_guard.discord)
        _PATCHED = True
        print("✅ server_design_majority_layout_guard active; guided repair target choices use live majority layout")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ server_design_majority_layout_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


__all__ = ["apply"]
