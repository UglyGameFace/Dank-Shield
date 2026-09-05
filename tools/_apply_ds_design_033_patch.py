from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEGACY_PATH = ROOT / "stoney_verify/commands_ext/public_design_studio.py"
V2_PATH = ROOT / "stoney_verify/commands_ext/public_design_studio_v2.py"


def replace_between(text: str, start: str, end: str, replacement: str, *, name: str) -> str:
    start_at = text.find(start)
    if start_at < 0:
        raise RuntimeError(f"{name}: start marker missing")
    end_at = text.find(end, start_at)
    if end_at < 0:
        raise RuntimeError(f"{name}: end marker missing")
    return text[:start_at] + replacement.rstrip() + "\n\n" + text[end_at:]


def replace_once(text: str, old: str, new: str, *, name: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{name}: expected exactly one match, got {count}")
    return text.replace(old, new, 1)


legacy = LEGACY_PATH.read_text(encoding="utf-8")
legacy = replace_once(
    legacy,
    "from stoney_verify.services import server_design_studio as studio\n",
    "from stoney_verify.services import server_design_studio as studio\nfrom stoney_verify.services import server_design_rule_service as rule_service\n",
    name="legacy rule service import",
)

legacy = replace_between(
    legacy,
    "def _current_format_lock(options: Mapping[str, Any], *, scope: str = \"global\") -> dict[str, Any]:\n",
    "def _mapping_dict(value: Any) -> dict[str, Any]:\n",
    '''def _current_format_lock(options: Mapping[str, Any], *, scope: str = "global") -> dict[str, Any]:
    """Build a reusable lock from the current server draft.

    An explicitly saved separator is part of the draft and must win over the
    theme default. Otherwise changing Theme/Strength while a global lock is
    enabled can silently resurrect the theme separator the user already replaced.
    """

    theme = _theme_from_options(options)
    strength = max(1, min(5, _safe_int(options.get("strength"), 4)))
    font = _safe_str(getattr(theme, "font", "normal"), "normal").lower().replace("-", "_")
    theme_separator = _safe_str(getattr(theme, "channel_separator", "bar_full"), "bar_full")

    return {
        "scope": scope,
        "theme_id": _safe_str(getattr(theme, "id", "gothic_clean"), "gothic_clean"),
        "strength": strength,
        "font": font,
        "separator_id": rule_service.effective_draft_separator(options, theme_separator=theme_separator),
        "category_frame_id": _safe_str(getattr(theme, "category_frame", "line"), "line"),
        "emoji_override": _safe_str(options.get("emoji_override"), ""),
        "exact_match": bool(options.get("exact_match", False)),
        "icon_mode": _safe_str(options.get("icon_mode"), "replace_missing"),
        "locked_at": _utc_iso_design(),
    }''',
    name="current format lock",
)

legacy = replace_between(
    legacy,
    "async def _clear_all_locks(interaction: discord.Interaction) -> dict[str, Any]:\n",
    "def _format_locks_embed(guild: discord.Guild, options: Mapping[str, Any]) -> discord.Embed:\n",
    '''def _clear_format_editor_drafts(guild_id: int, *, target_id: int | None = None) -> None:
    prefix = f"{int(guild_id)}:"
    suffix = f":{int(target_id)}" if target_id is not None else ""
    for draft_key in list(_FORMAT_EDITOR_DRAFTS.keys()):
        key = str(draft_key)
        if not key.startswith(prefix):
            continue
        if suffix and not key.endswith(suffix):
            continue
        _FORMAT_EDITOR_DRAFTS.pop(draft_key, None)


def _remaining_style_authority(options: Mapping[str, Any], target: Any) -> str:
    parent = getattr(target, "category", None)
    parent_id = _safe_int(getattr(parent, "id", 0), 0)
    category_locks = _mapping_dict(options.get("category_format_locks"))
    if parent_id > 0 and str(parent_id) in category_locks:
        return "parent category rule"
    global_lock = _mapping_dict(options.get("format_lock_global"))
    if global_lock.get("enabled"):
        return "global rule"
    return "server design draft"


async def _reset_item_design_overrides(
    interaction: discord.Interaction,
    *,
    target_id: int,
) -> tuple[dict[str, Any], dict[str, bool]]:
    guild = interaction.guild
    assert guild is not None
    options = await _load_design_options(int(guild.id))
    reset, removed = rule_service.reset_item_overrides(options, target_id=int(target_id))
    _clear_format_editor_drafts(int(guild.id), target_id=int(target_id))
    await _save_options(interaction, reset)
    return reset, removed


async def _clear_all_locks(interaction: discord.Interaction) -> dict[str, Any]:
    assert interaction.guild is not None
    guild_id = int(interaction.guild.id)
    options = await _load_design_options(guild_id)
    options = rule_service.reset_all_overrides(options)
    _clear_format_editor_drafts(guild_id)
    await _save_options(interaction, options)
    return options''',
    name="reset helpers",
)

legacy = replace_between(
    legacy,
    "async def _preview_scope(\n",
    "class DesignCategoryEditorButton(discord.ui.Button):\n",
    '''async def _preview_scope(
    interaction: discord.Interaction,
    *,
    scope_title: str,
    mode: str,
    category_id: int | None = None,
    channel_id: int | None = None,
) -> None:
    if not await _require_design_permission(interaction):
        return
    guild = interaction.guild
    assert guild is not None
    await interaction.response.defer(ephemeral=True, thinking=True)
    options = await _load_design_options(int(guild.id))

    if mode in {"category_editor", "channel_editor"}:
        from stoney_verify.services import server_design_plan_service as plan_service

        items, repair_options, _analysis = await plan_service.build_scoped_repair_plan(
            guild,
            options,
            category_id=category_id,
            channel_id=channel_id,
        )
    else:
        repair_options = dict(options)
        all_items = await build_design_plan(guild, repair_options)
        if category_id is not None:
            items = _filter_plan_for_category(all_items, int(category_id))
        elif channel_id is not None:
            items = _filter_plan_for_channel(all_items, int(channel_id))
        else:
            items = all_items

    created_at = _store_pending(
        int(guild.id),
        int(interaction.user.id),
        {"items": items, "options": dict(repair_options), "mode": mode, "scope_title": scope_title},
    )
    has_blockers = any(item.get("status") == "failed" for item in items)
    has_changes = any(item.get("status") == "changed" for item in items)
    await interaction.edit_original_response(
        embed=_preview_embed(guild, items, title=scope_title),
        view=DesignPreviewView(
            can_apply=not has_blockers and has_changes,
            pending_created_at=created_at,
        ),
    )''',
    name="scoped preview",
)

legacy = replace_between(
    legacy,
    "def _build_channel_separator_style_change_plan(\n",
    "def _style_change_embed(guild: discord.Guild, options: Mapping[str, Any], *, separator_id: str) -> discord.Embed:\n",
    '''def _build_channel_separator_style_change_plan(
    guild: discord.Guild,
    options: Mapping[str, Any],
    *,
    separator_id: str,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    item_rules = _protection_item_rules(options)

    for channel in _editable_channels(guild):
        kind = _kind(channel)
        if kind == "category" or kind == "other":
            continue

        before = _safe_str(getattr(channel, "name", ""))
        if not before:
            continue

        channel_id = str(getattr(channel, "id", ""))
        base = _base_for_channel(channel)
        inherited = _inherited_protection_mode(options, base)
        protection = item_rules.get(channel_id) or inherited

        if not rule_service.protection_allows_separator(protection):
            items.append(
                {
                    "channel_id": channel_id,
                    "category_id": str(getattr(getattr(channel, "category", None), "id", "")),
                    "kind": kind,
                    "before": before,
                    "after": before,
                    "base_name": base,
                    "status": "protected",
                    "protected": True,
                    "warnings": [f"Safe skip — protection mode `{protection}` does not allow separator changes."],
                    "blockers": [],
                    "substitutions": [],
                    "readability_score": 100,
                    "mobile_score": 100,
                    "clutter_score": 0,
                }
            )
            continue

        after, warnings, blockers = _style_change_separator_after(before, separator_id)
        status = "failed" if blockers else ("changed" if after != before else "unchanged")
        spec = _style_change_separator_spec(separator_id)

        items.append(
            {
                "channel_id": channel_id,
                "category_id": str(getattr(getattr(channel, "category", None), "id", "")),
                "kind": kind,
                "before": before,
                "after": after,
                "base_name": base,
                "status": status,
                "protected": False,
                "warnings": warnings,
                "blockers": blockers,
                "substitutions": [],
                "readability_score": 100,
                "mobile_score": 100,
                "clutter_score": _safe_int(getattr(spec, "clutter", 0), 0) if spec is not None else 0,
                "style_change_dimension": "channel_separator",
                "protection_mode": protection,
            }
        )

        if len(items) >= studio.MAX_PLAN_ITEMS:
            break

    return items''',
    name="separator plan protection",
)

category_reset = '''    @discord.ui.button(label="Reset This Category", emoji="🧹", style=discord.ButtonStyle.danger, custom_id="dank_design:category_reset_item", row=3)
    async def reset_category(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        category = guild.get_channel(self.category_id)
        if not isinstance(category, discord.CategoryChannel):
            return await interaction.response.send_message("That category no longer exists.", ephemeral=True)
        options, removed = await _reset_item_design_overrides(interaction, target_id=self.category_id)
        embed = _category_action_embed(category)
        embed.title = "🧹 Category Overrides Reset"
        embed.add_field(
            name="Reset result",
            value=(
                f"Removed **{rule_service.removal_count(removed)}** same-item override(s). "
                f"This category now inherits the **{_remaining_style_authority(options, category)}**. "
                "Built-in/name protection is inherited separately. Child-channel overrides were not deleted."
            ),
            inline=False,
        )
        await interaction.response.edit_message(embed=embed, view=CategoryEditorActionView(self.category_id))

'''
legacy = replace_once(
    legacy,
    '    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, custom_id="dank_design:category_action_refresh", row=4)\n',
    category_reset + '    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, custom_id="dank_design:category_action_refresh", row=4)\n',
    name="category reset button",
)

channel_reset = '''    @discord.ui.button(label="Reset This Channel", emoji="🧹", style=discord.ButtonStyle.danger, custom_id="dank_design:channel_reset_item", row=3)
    async def reset_channel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        channel = guild.get_channel(self.channel_id)
        if channel is None:
            return await interaction.response.send_message("That channel no longer exists.", ephemeral=True)
        options, removed = await _reset_item_design_overrides(interaction, target_id=self.channel_id)
        embed = _channel_action_embed(channel)
        embed.title = "🧹 Channel Overrides Reset"
        embed.add_field(
            name="Reset result",
            value=(
                f"Removed **{rule_service.removal_count(removed)}** same-item override(s). "
                f"This channel now inherits the **{_remaining_style_authority(options, channel)}**. "
                "Built-in/name protection is inherited separately."
            ),
            inline=False,
        )
        await interaction.response.edit_message(embed=embed, view=ChannelEditorActionView(self.channel_id, category_id=self.category_id))

'''
legacy = replace_once(
    legacy,
    '    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, custom_id="dank_design:channel_action_refresh", row=4)\n',
    channel_reset + '    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, custom_id="dank_design:channel_action_refresh", row=4)\n',
    name="channel reset button",
)

legacy = replace_once(
    legacy,
    '            label=f"Unlock {display_index}. {_short_label(label, 46) if \'_short_label\' in globals() else label[:46]}",\n',
    '            label=f"Remove {display_index}. {_short_label(label, 46) if \'_short_label\' in globals() else label[:46]}",\n',
    name="lock remove wording",
)
legacy = replace_once(
    legacy,
    '        embed.title = "🗑️ Format Lock Removed"\n        await interaction.response.edit_message(embed=embed, view=LockManagerView(guild, options, page=0))\n',
    '        embed.title = "🗑️ One Saved Rule Removed"\n        embed.description = "Removed only the listed rule. Another exact or broader rule may still apply. Use **Reset This Category/Channel** in the item editor to remove every same-item override at once."\n        await interaction.response.edit_message(embed=embed, view=LockManagerView(guild, options, page=0))\n',
    name="lock remove result wording",
)
legacy = replace_once(
    legacy,
    '    @discord.ui.button(label="Clear All Locks", emoji="⚠️", style=discord.ButtonStyle.danger, custom_id="dank_design:clear_all_locks", row=2)\n',
    '    @discord.ui.button(label="Reset All Design Overrides", emoji="⚠️", style=discord.ButtonStyle.danger, custom_id="dank_design:clear_all_locks", row=2)\n',
    name="reset all label",
)
legacy = replace_once(
    legacy,
    '        embed.title = "🧹 All Format Locks Cleared"\n        embed.description = "Global, category, channel, exact manual-name, and exact protection rules were cleared. The current server draft is active again."\n',
    '        embed.title = "🧹 All Design Overrides Reset"\n        embed.description = "Global, category, channel, exact manual-name, exact-item protection, and saved name-level protection overrides were cleared. The ordinary server draft remains selected; built-in protection defaults still apply."\n',
    name="reset all result wording",
)
legacy = replace_once(
    legacy,
    '                can_apply=not has_blockers and has_changes,\n                has_blockers=has_blockers,\n                pending_created_at=created_at,\n',
    '                can_apply=not has_blockers and bool(items),\n                has_blockers=has_blockers,\n                pending_created_at=created_at,\n',
    name="separator apply saves choice without live changes",
)

LEGACY_PATH.write_text(legacy, encoding="utf-8")

v2 = V2_PATH.read_text(encoding="utf-8")
v2 = replace_once(
    v2,
    "from stoney_verify.services import server_design_plan_service as plans\nfrom stoney_verify.services import server_design_repair_confidence as repair_confidence\n",
    "from stoney_verify.services import server_design_plan_service as plans\nfrom stoney_verify.services import server_design_repair_confidence as repair_confidence\nfrom stoney_verify.services import server_design_rule_service as rule_service\n",
    name="v2 rule service import",
)

v2 = replace_once(
    v2,
    '            f"Strength: **{_safe_int(options.get(\'strength\'), 4)}/5**\\n"\n            f"Global rule: **{\'On\' if counts.get(\'global\') else \'Off\'}**"\n',
    '            f"Strength: **{_safe_int(options.get(\'strength\'), 4)}/5**\\n"\n            f"Separator: **{legacy._separator_choice_label(rule_service.effective_draft_separator(options, theme_separator=_safe_str(getattr(theme, \'channel_separator\', \'none\'), \'none\')))}**\\n"  # type: ignore[attr-defined]\n            f"Global rule: **{\'On\' if counts.get(\'global\') else \'Off\'}**"\n',
    name="home separator status",
)

persist_helper = '''async def _persist_separator_settings(
    interaction: discord.Interaction,
    payload: Mapping[str, Any],
    applied: list[apply_service.PreparedRename],
) -> dict[str, Any]:
    guild = interaction.guild
    assert guild is not None
    chosen = _safe_str(payload.get("separator_id"), "")
    if not chosen:
        raise RuntimeError("Separator preview did not contain a selected separator.")
    previous = await _load_design_options(int(guild.id))
    updated = rule_service.persist_separator_choice(
        previous,
        separator_id=chosen,
        applied_rows=applied,
    )
    await legacy._save_options(interaction, updated)  # type: ignore[attr-defined]
    return previous


'''
v2 = replace_once(
    v2,
    "class ReviewedPreviewView(DesignView):\n",
    persist_helper + "class ReviewedPreviewView(DesignView):\n",
    name="separator persistence helper",
)

old_snapshot = '''            snapshot: dict[str, Any] | None = None
            if result.applied:
                try:
                    snapshot = await _store_durable_snapshot(int(guild.id), int(interaction.user.id), result.applied)
                except Exception as snapshot_exc:
                    restored, residual, rollback_failures = await apply_service.compensate_applied(
'''
new_snapshot = '''            separator_previous_options: dict[str, Any] | None = None
            if mode == "style_change_separator":
                try:
                    separator_previous_options = await _persist_separator_settings(interaction, payload, result.applied)
                except Exception as settings_exc:
                    if result.applied:
                        restored, residual, rollback_failures = await apply_service.compensate_applied(
                            guild,
                            result.applied,
                            user_id=int(interaction.user.id),
                            delay_seconds=studio.DEFAULT_DELAY_SECONDS,
                        )
                    else:
                        restored, residual, rollback_failures = 0, [], []
                    emergency, durable = await _store_residual_snapshot(int(guild.id), int(interaction.user.id), residual)
                    legacy._PENDING.pop(key, None)  # type: ignore[attr-defined]
                    embed = discord.Embed(
                        title="⚠️ Separator Apply Reversed Because Its Setting Could Not Be Saved",
                        description=(
                            f"Saving the selected separator failed with **{type(settings_exc).__name__}**. "
                            + (
                                f"Automatically restored **{restored}** live rename(s); the old saved design remains authoritative."
                                if not residual
                                else f"Automatic restore left **{len(residual)}** row(s) changed. An {'durable' if durable else 'emergency memory-only'} Undo record was retained for them."
                            )
                        ),
                        color=discord.Color.orange(),
                    )
                    if rollback_failures:
                        embed.add_field(name="Restore attention", value="\\n".join(f"• {line}" for line in rollback_failures[:8])[:1024], inline=False)
                    await interaction.edit_original_response(content=None, embed=embed, view=DoneView(can_rollback=bool(emergency)))
                    return

            snapshot: dict[str, Any] | None = None
            if result.applied:
                try:
                    snapshot = await _store_durable_snapshot(int(guild.id), int(interaction.user.id), result.applied)
                except Exception as snapshot_exc:
                    settings_restore_error = ""
                    if separator_previous_options is not None:
                        try:
                            await legacy._save_options(interaction, separator_previous_options)  # type: ignore[attr-defined]
                        except Exception as restore_exc:
                            settings_restore_error = type(restore_exc).__name__
                    restored, residual, rollback_failures = await apply_service.compensate_applied(
'''
v2 = replace_once(v2, old_snapshot, new_snapshot, name="separator transactional persistence")

v2 = replace_once(
    v2,
    '''                    if rollback_failures:
                        embed.add_field(name="Restore attention", value="\\n".join(f"• {line}" for line in rollback_failures[:8])[:1024], inline=False)
                    await interaction.edit_original_response(content=None, embed=embed, view=DoneView(can_rollback=bool(emergency)))
                    return

            legacy._PENDING.pop(key, None)  # type: ignore[attr-defined]

        title = "✅ Inconsistent Names Repaired" if "consistency" in mode else "✅ Reviewed Design Applied"
        embed = discord.Embed(
            title=title,
            description=f"Changed **{len(result.applied)}** item(s). Left **{skipped}** reviewed skip(s) untouched. Failed **0**.",
            color=discord.Color.green(),
        )
''',
    '''                    if rollback_failures:
                        embed.add_field(name="Restore attention", value="\\n".join(f"• {line}" for line in rollback_failures[:8])[:1024], inline=False)
                    if settings_restore_error:
                        embed.add_field(name="Saved-setting attention", value=f"The previous separator setting could not be restored automatically (`{settings_restore_error}`). Do not run another design Apply until that setting is reviewed.", inline=False)
                    await interaction.edit_original_response(content=None, embed=embed, view=DoneView(can_rollback=bool(emergency)))
                    return

            legacy._PENDING.pop(key, None)  # type: ignore[attr-defined]

        if mode == "style_change_separator":
            title = "✅ Channel Separator Applied & Saved"
            description = f"Changed **{len(result.applied)}** live channel name(s), left **{skipped}** reviewed skip(s) untouched, and saved **{legacy._separator_choice_label(payload.get('separator_id'))}** as the authoritative separator. Failed **0**."  # type: ignore[attr-defined]
        elif "consistency" in mode:
            title = "✅ Inconsistent Names Repaired"
            description = f"Changed **{len(result.applied)}** item(s). Left **{skipped}** reviewed skip(s) untouched. Failed **0**."
        else:
            title = "✅ Reviewed Design Applied"
            description = f"Changed **{len(result.applied)}** item(s). Left **{skipped}** reviewed skip(s) untouched. Failed **0**."
        embed = discord.Embed(
            title=title,
            description=description,
            color=discord.Color.green(),
        )
''',
    name="separator success wording and settings restore",
)

V2_PATH.write_text(v2, encoding="utf-8")

print("DS-DESIGN-033 patch applied")
