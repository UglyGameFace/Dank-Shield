from __future__ import annotations

"""Public /dank design command for the Server Design Studio.

The runtime guard keeps the command in the existing /dank group and uses the
pure service engine for preview/apply/rollback. It only edits channel/category
names and never mutates permissions, overwrites, topics, order, slowmode, NSFW,
archive settings, or category placement.
"""

import asyncio
import time
from typing import Any, Mapping

import discord

from stoney_verify.services import server_design_studio as studio

_PATCHED = False
_PENDING: dict[str, dict[str, Any]] = {}
_LAST_SNAPSHOTS: dict[str, list[dict[str, Any]]] = {}
_LOCKS: dict[str, asyncio.Lock] = {}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text or default
    except Exception:
        return default


def _key(guild_id: int, user_id: int) -> str:
    return f"{int(guild_id)}:{int(user_id)}"


def _guild_key(guild_id: int) -> str:
    return str(int(guild_id))


def _lock_for(guild_id: int) -> asyncio.Lock:
    key = _guild_key(guild_id)
    lock = _LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[key] = lock
    return lock


def _kind(channel: Any) -> str:
    if isinstance(channel, discord.CategoryChannel):
        return "category"
    stage_cls = getattr(discord, "StageChannel", None)
    if stage_cls is not None and isinstance(channel, stage_cls):
        return "stage"
    forum_cls = getattr(discord, "ForumChannel", None)
    if forum_cls is not None and isinstance(channel, forum_cls):
        return "forum"
    if isinstance(channel, discord.VoiceChannel):
        return "voice"
    if isinstance(channel, discord.TextChannel):
        return "text"
    return "other"


def _editable_channels(guild: discord.Guild) -> list[discord.abc.GuildChannel]:
    out: list[discord.abc.GuildChannel] = []
    seen: set[int] = set()
    for channel in list(getattr(guild, "categories", []) or []) + list(getattr(guild, "channels", []) or []):
        cid = _safe_int(getattr(channel, "id", 0), 0)
        if cid <= 0 or cid in seen:
            continue
        seen.add(cid)
        if _kind(channel) != "other":
            out.append(channel)
    return out[: studio.MAX_PLAN_ITEMS]


def _can_user_design(interaction: discord.Interaction) -> bool:
    try:
        return bool(interaction.guild and isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.manage_channels)
    except Exception:
        return False


async def _require_design_permission(interaction: discord.Interaction) -> bool:
    if interaction.guild is None:
        await interaction.response.send_message("❌ This command must be used inside a server.", ephemeral=True)
        return False
    if not _can_user_design(interaction):
        await interaction.response.send_message("❌ Server Design Studio requires **Manage Channels**. It never requires Administrator.", ephemeral=True)
        return False
    return True


async def _load_design_options(guild_id: int) -> dict[str, Any]:
    default = {"theme_id": "gothic_clean", "strength": 4, "icon_mode": "replace_missing", "protection_rules": {}}
    try:
        from stoney_verify.guild_config import get_guild_config

        cfg = await get_guild_config(guild_id, refresh=True)
        raw = cfg.get("server_design_studio_options") if isinstance(cfg, dict) else None
        if isinstance(raw, Mapping):
            merged = {**default, **dict(raw)}
            if not isinstance(merged.get("protection_rules"), Mapping):
                merged["protection_rules"] = {}
            return merged
    except Exception:
        pass
    return default


async def _save_design_options(guild_id: int, options: Mapping[str, Any]) -> None:
    try:
        from stoney_verify.guild_config import clear_guild_config_cache, upsert_guild_config

        await upsert_guild_config(guild_id, {"server_design_studio_options": dict(options)})
        clear_guild_config_cache(guild_id)
    except Exception as exc:
        print(f"⚠️ server_design_studio_command_guard config save skipped: {type(exc).__name__}: {exc}")


def _bot_missing_manage(channel: discord.abc.GuildChannel, bot_member: discord.Member | None) -> str:
    if bot_member is None:
        return "Bot member could not be resolved."
    try:
        perms = channel.permissions_for(bot_member)
        if not perms.view_channel:
            return "Bot cannot view this channel/category."
        if not perms.manage_channels:
            return "Bot lacks Manage Channels here."
    except Exception:
        return "Could not verify bot permissions for this item."
    return ""


def _utc_iso_design() -> str:
    try:
        return discord.utils.utcnow().isoformat()
    except Exception:
        return ""


def _theme_from_options(options: Mapping[str, Any]) -> Any:
    theme_id = _safe_str(options.get("theme_id"), "gothic_clean")
    return next((theme for theme in studio.THEMES if theme.id == theme_id), studio.THEMES[1])


def _current_format_lock(options: Mapping[str, Any], *, scope: str = "global") -> dict[str, Any]:
    """Build a reusable lock from the current draft.

    The lock stores exact format pieces, not just a theme label. That lets the
    consistency scanner reuse the chosen emoji mode, separator, font, category
    frame, and strength without making the user re-pick them for each channel.
    """

    theme = _theme_from_options(options)
    strength = max(1, min(5, _safe_int(options.get("strength"), 4)))
    font = _safe_str(getattr(theme, "font", "normal"), "normal").lower().replace("-", "_")

    # Font-based themes should not accidentally lock as a plain cleanup pass.
    if font != "normal" and strength < 4:
        strength = 4

    return {
        "scope": scope,
        "theme_id": _safe_str(getattr(theme, "id", "gothic_clean"), "gothic_clean"),
        "strength": strength,
        "font": font,
        "separator_id": _safe_str(getattr(theme, "channel_separator", "bar_full"), "bar_full"),
        "category_frame_id": _safe_str(getattr(theme, "category_frame", "line"), "line"),
        "icon_mode": _safe_str(options.get("icon_mode"), "replace_missing"),
        "locked_at": _utc_iso_design(),
    }


def _mapping_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _lock_count(options: Mapping[str, Any]) -> dict[str, int]:
    global_lock = _mapping_dict(options.get("format_lock_global"))
    category_locks = _mapping_dict(options.get("category_format_locks"))
    channel_locks = _mapping_dict(options.get("channel_format_locks"))
    return {
        "global": 1 if global_lock.get("enabled") else 0,
        "categories": len(category_locks),
        "channels": len(channel_locks),
    }


def _effective_format_options(
    options: Mapping[str, Any],
    *,
    channel_id: int,
    category_id: int,
) -> dict[str, Any]:
    """Resolve format priority for one channel/category.

    Priority:
    1. channel override lock
    2. category format lock
    3. global format lock
    4. ordinary auto theme draft
    """

    effective = dict(options)
    scope = "auto"

    channel_locks = _mapping_dict(options.get("channel_format_locks"))
    category_locks = _mapping_dict(options.get("category_format_locks"))
    global_lock = _mapping_dict(options.get("format_lock_global"))

    chosen = None
    channel_key = str(int(channel_id or 0))
    category_key = str(int(category_id or 0))

    if channel_key in channel_locks and isinstance(channel_locks.get(channel_key), Mapping):
        chosen = dict(channel_locks[channel_key])
        scope = "channel"
    elif category_key in category_locks and isinstance(category_locks.get(category_key), Mapping):
        chosen = dict(category_locks[category_key])
        scope = "category"
    elif global_lock.get("enabled"):
        chosen = dict(global_lock)
        scope = "global"

    if chosen:
        for key in ("theme_id", "strength", "icon_mode", "font", "separator_id", "category_frame_id"):
            if chosen.get(key) is not None:
                effective[key] = chosen.get(key)
        effective["__format_lock_scope"] = scope
    else:
        effective["__format_lock_scope"] = "auto"

    return effective


async def _save_options(interaction: discord.Interaction, options: Mapping[str, Any]) -> None:
    guild = interaction.guild
    if guild is None:
        raise RuntimeError("This must be used inside a server.")
    await _save_design_options(int(guild.id), dict(options))


async def _save_global_lock(interaction: discord.Interaction) -> dict[str, Any]:
    assert interaction.guild is not None
    options = await _load_design_options(int(interaction.guild.id))
    options["format_lock_global"] = {**_current_format_lock(options, scope="global"), "enabled": True}
    await _save_options(interaction, options)
    return options


async def _save_category_lock(interaction: discord.Interaction, category_id: int) -> dict[str, Any]:
    assert interaction.guild is not None
    options = await _load_design_options(int(interaction.guild.id))
    locks = _mapping_dict(options.get("category_format_locks"))
    locks[str(int(category_id))] = _current_format_lock(options, scope="category")
    options["category_format_locks"] = locks
    await _save_options(interaction, options)
    return options


async def _save_channel_lock(interaction: discord.Interaction, channel_id: int) -> dict[str, Any]:
    assert interaction.guild is not None
    options = await _load_design_options(int(interaction.guild.id))
    locks = _mapping_dict(options.get("channel_format_locks"))
    locks[str(int(channel_id))] = _current_format_lock(options, scope="channel")
    options["channel_format_locks"] = locks
    await _save_options(interaction, options)
    return options


async def _clear_global_lock(interaction: discord.Interaction) -> dict[str, Any]:
    assert interaction.guild is not None
    options = await _load_design_options(int(interaction.guild.id))
    options["format_lock_global"] = {}
    await _save_options(interaction, options)
    return options


async def _clear_all_locks(interaction: discord.Interaction) -> dict[str, Any]:
    assert interaction.guild is not None
    options = await _load_design_options(int(interaction.guild.id))
    options["format_lock_global"] = {}
    options["category_format_locks"] = {}
    options["channel_format_locks"] = {}
    await _save_options(interaction, options)
    return options


def _format_locks_embed(guild: discord.Guild, options: Mapping[str, Any]) -> discord.Embed:
    counts = _lock_count(options)
    theme = _theme_from_options(options)
    current_lock = _current_format_lock(options)

    embed = discord.Embed(
        title="🔒 Server Design Format Locks",
        description=(
            "Lock a selected layout once, then reuse it for categories or channels.\\n\\n"
            "Future previews and consistency checks will compare names against these saved locks instead of guessing from one global auto design."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Current draft format",
        value=(
            f"Theme: **{getattr(theme, 'label', 'Gothic Clean')}**\\n"
            f"Font: **{_safe_str(current_lock.get('font'), 'normal').replace('_', ' ').title()}**\\n"
            f"Separator: **{_safe_str(current_lock.get('separator_id'), 'bar_full').replace('_', ' ').title()}**\\n"
            f"Category frame: **{_safe_str(current_lock.get('category_frame_id'), 'line').replace('_', ' ').title()}**\\n"
            f"Strength: **{_safe_int(current_lock.get('strength'), 4)}/5**"
        ),
        inline=False,
    )
    embed.add_field(
        name="Saved locks",
        value=(
            f"Global: **{'On' if counts['global'] else 'Off'}**\\n"
            f"Categories: **{counts['categories']}**\\n"
            f"Channels: **{counts['channels']}**"
        ),
        inline=True,
    )
    embed.add_field(
        name="Priority",
        value="Protected item → Channel lock → Category lock → Global lock → Auto theme",
        inline=False,
    )
    embed.set_footer(text="Use Find & Fix Inconsistencies after saving locks.")
    return embed



async def build_design_plan(guild: discord.Guild, options: Mapping[str, Any]) -> list[dict[str, Any]]:
    bot_member = guild.me if isinstance(guild.me, discord.Member) else None
    theme_id = _safe_str(options.get("theme_id"), "gothic_clean")
    strength = _safe_int(options.get("strength"), 2)
    icon_mode = _safe_str(options.get("icon_mode"), "replace_missing")
    protection_rules = options.get("protection_rules") if isinstance(options.get("protection_rules"), Mapping) else {}
    items: list[dict[str, Any]] = []
    for channel in _editable_channels(guild):
        kind = _kind(channel)
        current_name = _safe_str(getattr(channel, "name", ""))
        parent = getattr(channel, "category", None)
        channel_id = _safe_int(getattr(channel, "id", 0), 0)
        category_id = channel_id if kind == "category" else _safe_int(getattr(parent, "id", 0), 0)
        effective_options = _effective_format_options(options, channel_id=channel_id, category_id=category_id)

        result = studio.build_styled_name(
            current_name,
            kind="category" if kind == "category" else "text",
            theme_id=_safe_str(effective_options.get("theme_id"), theme_id),
            strength=_safe_int(effective_options.get("strength"), strength),
            icon_mode=_safe_str(effective_options.get("icon_mode"), icon_mode),
            protection_rules=protection_rules,
            separator_id=_safe_str(effective_options.get("separator_id")) or None,
            category_frame_id=_safe_str(effective_options.get("category_frame_id")) or None,
            font=_safe_str(effective_options.get("font")) or None,
        )
        item = result.to_plan_item(channel_id=getattr(channel, "id", ""), category_id=getattr(parent, "id", ""))
        item["kind"] = kind
        item["format_lock_scope"] = _safe_str(effective_options.get("__format_lock_scope"), "auto")
        missing = _bot_missing_manage(channel, bot_member)
        if missing and item.get("status") != "protected":
            item.setdefault("blockers", []).append(missing)
            item["status"] = "failed"
        items.append(item)
    duplicates = studio.detect_duplicate_outputs(items)
    if duplicates:
        duplicate_names = {line.split(" would both become ")[-1].strip("`") for line in duplicates}
        for item in items:
            if _safe_str(item.get("after")) in duplicate_names and item.get("status") != "protected":
                item.setdefault("blockers", []).append("Duplicate output name; edit or skip one of the conflicting channels.")
                item["status"] = "failed"
    return items


def _home_embed(guild: discord.Guild, options: Mapping[str, Any] | None = None) -> discord.Embed:
    options = options or {}
    theme_id = _safe_str(options.get("theme_id"), "gothic_clean")
    strength = _safe_int(options.get("strength"), 2)
    theme = next((t for t in studio.THEMES if t.id == theme_id), studio.THEMES[1])
    embed = discord.Embed(
        title="🎨 Dank Shield Server Design Studio",
        description=(
            "Customize visible channel/category naming parts: emojis, separators, fonts, category frames, cleanup, preview, apply, and rollback.\n\n"
            "This tool only changes names. It never changes permissions, overwrites, topics, order, slowmode, NSFW, archive settings, or category placement."
        ),
        color=discord.Color.blurple(),
    )
    font_text = str(getattr(theme, "font", "normal") or "normal").replace("_", " ").title()
    embed.add_field(
        name="Current Draft",
        value=(
            f"Theme: **{theme.label}**\n"
            f"Theme font: **{font_text}**\n"
            f"Strength: **{strength}/5**\n"
            "Apply mode: **one press, paced at 2 seconds per rename**"
        ),
        inline=False,
    )
    embed.add_field(
        name="Fast path",
        value=(
            "**Preview Selected Design** shows only the important changes first. "
            "Safe skips are summarized instead of dumped as scary errors. "
            "Rollback is available after apply."
        ),
        inline=False,
    )
    counts = _lock_count(options)
    embed.add_field(
        name="Format Locks",
        value=(
            f"Global: **{'On' if counts['global'] else 'Off'}** • "
            f"Categories: **{counts['categories']}** • "
            f"Channels: **{counts['channels']}**"
        ),
        inline=False,
    )
    embed.set_footer(text="/dank design • also reachable from /dank setup Advanced Tools")
    return embed


def _preview_embed(guild: discord.Guild, items: list[dict[str, Any]], *, title: str = "👁 Server Design Preview") -> discord.Embed:
    summary = studio.summarize_plan(items)
    score = studio.design_score(items)
    has_failures = bool(summary["failed"])

    embed = discord.Embed(
        title=title,
        description=(
            "Nothing has been changed yet. This preview shows the actual final names that will be applied.\n\n"
            "Safe skips are intentionally left alone and do not block Apply."
        ),
        color=discord.Color.red() if has_failures else discord.Color.green(),
    )

    embed.add_field(
        name="Plan",
        value=(
            f"Ready changes: **{summary['changed']}**\n"
            f"Safe skips: **{summary['protected']}**\n"
            f"Must fix: **{summary['failed']}**\n"
            f"Notes: **{summary['warnings']}**"
        ),
        inline=True,
    )
    embed.add_field(
        name="Design Score",
        value=(
            f"Readability: **{score['readability']}/100**\n"
            f"Mobile: **{score['mobile_fit']}/100**\n"
            f"Clutter: **{score['clutter_risk']}**\n"
            f"Accessibility: **{score['accessibility']}**"
        ),
        inline=True,
    )

    changed_lines = studio.preview_lines(items, filter_mode="changed", limit=12)
    if changed_lines == ["No matching preview rows."]:
        changed_text = "No rename changes are needed for this draft."
    else:
        changed_text = "\n".join(changed_lines)[:1024]
    embed.add_field(name="Will Change", value=changed_text, inline=False)

    if summary["protected"]:
        embed.add_field(
            name="Safe Skips",
            value=(
                f"**{summary['protected']}** ticket/log/system item(s) are protected by default. "
                "They are not errors and they will not be renamed unless you later override that policy."
            ),
            inline=False,
        )

    failed_lines = studio.preview_lines(items, filter_mode="failed", limit=5)
    if failed_lines and failed_lines != ["No matching preview rows."]:
        embed.add_field(name="Must Fix Before Apply", value="\n".join(failed_lines)[:1024], inline=False)

    if summary["warnings"] and not has_failures:
        embed.add_field(
            name="Notes",
            value=(
                "Decorative fonts and fallback glyphs are treated as safe notes, not blockers. "
                "The preview above is still the final rename output."
            ),
            inline=False,
        )

    embed.set_footer(text="Apply is disabled only for real failures. Font fallback notes and safe skips do not block Apply.")
    return embed



class ThemeSelect(discord.ui.Select):
    def __init__(self, current: str) -> None:
        options = []
        for theme in studio.THEMES[:25]:
            font_text = str(getattr(theme, "font", "normal") or "normal").replace("_", " ").title()
            frame_text = str(getattr(theme, "category_frame", "plain") or "plain").replace("_", " ").title()
            options.append(
                discord.SelectOption(
                    label=theme.label[:100],
                    value=theme.id,
                    default=theme.id == current,
                    description=f"Font: {font_text} • Category frame: {frame_text}"[:100],
                )
            )
        super().__init__(placeholder="Choose a design theme…", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require_design_permission(interaction):
            return
        assert interaction.guild is not None
        options = await _load_design_options(int(interaction.guild.id))
        options["theme_id"] = self.values[0]
        picked_theme = next((theme for theme in studio.THEMES if theme.id == self.values[0]), None)
        picked_font = _safe_str(getattr(picked_theme, "font", "normal"), "normal").lower().replace("-", "_") if picked_theme else "normal"
        if picked_font != "normal" and _safe_int(options.get("strength"), 4) < 4:
            options["strength"] = 4
        await _save_design_options(int(interaction.guild.id), options)
        await interaction.response.edit_message(embed=_home_embed(interaction.guild, options), view=DesignHomeView(options))


class StrengthSelect(discord.ui.Select):
    def __init__(self, current: int) -> None:
        labels = {
            1: ("1 — Minimal", "Emoji only. No font styling."),
            2: ("2 — Clean", "Emoji + separator. Font themes still keep their font."),
            3: ("3 — Category style", "Adds category headers where safe."),
            4: ("4 — Recommended", "Best default for Goth/Clean and other font themes."),
            5: ("5 — Full theme", "Most decorative option."),
        }
        options = [
            discord.SelectOption(
                label=label,
                value=str(value),
                default=value == current,
                description=description[:100],
            )
            for value, (label, description) in labels.items()
        ]
        super().__init__(placeholder="Choose how much styling to apply…", min_values=1, max_values=1, options=options, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require_design_permission(interaction):
            return
        assert interaction.guild is not None
        options = await _load_design_options(int(interaction.guild.id))
        options["strength"] = _safe_int(self.values[0], 2)
        await _save_design_options(int(interaction.guild.id), options)
        await interaction.response.edit_message(embed=_home_embed(interaction.guild, options), view=DesignHomeView(options))

def _consistency_summary(items: list[dict[str, Any]]) -> dict[str, int]:
    out = {"matches": 0, "needs_fix": 0, "protected": 0, "failed": 0, "notes": 0}
    for item in items:
        status = _safe_str(item.get("status"), "unchanged")
        if status == "changed":
            out["needs_fix"] += 1
        elif status == "protected":
            out["protected"] += 1
        elif status == "failed":
            out["failed"] += 1
        else:
            out["matches"] += 1
        if item.get("warnings"):
            out["notes"] += 1
    return out


def _consistency_lines(items: list[dict[str, Any]], *, limit: int = 12) -> list[str]:
    rows: list[str] = []
    for item in items:
        if item.get("status") != "changed":
            continue
        before = _safe_str(item.get("before"))
        after = _safe_str(item.get("after"))
        kind = _safe_str(item.get("kind"), "channel")
        rows.append(f"🧩 `{before}` → `{after}` · `{kind}`"[:260])
        if len(rows) >= limit:
            break
    return rows or ["No inconsistent channel names found."]


def _consistency_embed(guild: discord.Guild, items: list[dict[str, Any]], options: Mapping[str, Any]) -> discord.Embed:
    summary = _consistency_summary(items)
    theme_id = _safe_str(options.get("theme_id"), "gothic_clean")
    strength = _safe_int(options.get("strength"), 4)
    theme = next((t for t in studio.THEMES if t.id == theme_id), studio.THEMES[1])
    font_text = str(getattr(theme, "font", "normal") or "normal").replace("_", " ").title()

    embed = discord.Embed(
        title="🧭 Server Design Consistency Check",
        description=(
            "Dank Shield compared the current channel/category names against the saved design draft.\\n\\n"
            "Use **Fix All Inconsistencies** to repair only names that drifted from the saved format."
        ),
        color=discord.Color.green() if not summary["failed"] else discord.Color.orange(),
    )
    embed.add_field(
        name="Saved design",
        value=(
            f"Theme: **{theme.label}**\\n"
            f"Font: **{font_text}**\\n"
            f"Strength: **{strength}/5**\\n"
            "Delay: **2 seconds per rename**"
        ),
        inline=False,
    )
    embed.add_field(
        name="Results",
        value=(
            f"Matches saved design: **{summary['matches']}**\\n"
            f"Needs fix: **{summary['needs_fix']}**\\n"
            f"Protected safe skips: **{summary['protected']}**\\n"
            f"Cannot fix yet: **{summary['failed']}**\\n"
            f"Notes: **{summary['notes']}**"
        ),
        inline=True,
    )
    embed.add_field(
        name="What will be fixed",
        value="\\n".join(_consistency_lines(items, limit=12))[:1024],
        inline=False,
    )

    if summary["protected"]:
        embed.add_field(
            name="Protected safe skips",
            value=(
                "Ticket/log/system names are intentionally protected unless you override them later. "
                "They are not treated as failures."
            ),
            inline=False,
        )

    failed_lines = studio.preview_lines(items, filter_mode="failed", limit=5)
    if failed_lines and failed_lines != ["No matching preview rows."]:
        embed.add_field(name="Cannot fix yet", value="\\n".join(failed_lines)[:1024], inline=False)

    embed.set_footer(text="Fix uses the same one-press apply flow and rollback snapshot.")
    return embed


class FormatLocksButton(discord.ui.Button):
    def __init__(self, *, row: int = 4) -> None:
        super().__init__(
            label="Format Locks",
            emoji="🔒",
            style=discord.ButtonStyle.primary,
            custom_id="dank_design:format_locks",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        options = await _load_design_options(int(guild.id))
        await interaction.response.edit_message(embed=_format_locks_embed(guild, options), view=FormatLocksView())


class CategoryFormatLockSelect(discord.ui.ChannelSelect):
    def __init__(self) -> None:
        super().__init__(
            placeholder="Lock current format to one category",
            min_values=1,
            max_values=1,
            channel_types=[discord.ChannelType.category],
            custom_id="dank_design:lock_category_select",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        category = self.values[0]
        options = await _save_category_lock(interaction, int(category.id))
        embed = _format_locks_embed(guild, options)
        embed.title = "✅ Category Format Lock Saved"
        embed.description = f"Saved the current draft format for {category.mention}. Future scans will use this lock for the category and its children unless a channel override exists."
        await interaction.response.edit_message(embed=embed, view=FormatLocksView())


class ChannelFormatLockSelect(discord.ui.ChannelSelect):
    def __init__(self) -> None:
        channel_types = [discord.ChannelType.text, discord.ChannelType.voice]
        try:
            channel_types.append(discord.ChannelType.category)
        except Exception:
            pass
        try:
            channel_types.append(discord.ChannelType.forum)
        except Exception:
            pass
        try:
            channel_types.append(discord.ChannelType.stage_voice)
        except Exception:
            pass
        super().__init__(
            placeholder="Lock current format to one channel/category override",
            min_values=1,
            max_values=1,
            channel_types=channel_types,
            custom_id="dank_design:lock_channel_select",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        channel = self.values[0]
        options = await _save_channel_lock(interaction, int(channel.id))
        embed = _format_locks_embed(guild, options)
        embed.title = "✅ Channel Override Lock Saved"
        embed.description = f"Saved the current draft format as an exact override for {channel.mention}."
        await interaction.response.edit_message(embed=embed, view=FormatLocksView())


class CategoryFormatLockPickerView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)
        self.add_item(CategoryFormatLockSelect())

    @discord.ui.button(label="Back to Format Locks", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="dank_design:format_locks_back_from_category", row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        assert interaction.guild is not None
        options = await _load_design_options(int(interaction.guild.id))
        await interaction.response.edit_message(embed=_format_locks_embed(interaction.guild, options), view=FormatLocksView())


class ChannelFormatLockPickerView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)
        self.add_item(ChannelFormatLockSelect())

    @discord.ui.button(label="Back to Format Locks", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="dank_design:format_locks_back_from_channel", row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        assert interaction.guild is not None
        options = await _load_design_options(int(interaction.guild.id))
        await interaction.response.edit_message(embed=_format_locks_embed(interaction.guild, options), view=FormatLocksView())


class FormatLocksView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Lock Current as Global", emoji="🔒", style=discord.ButtonStyle.success, custom_id="dank_design:lock_global", row=0)
    async def lock_global(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        options = await _save_global_lock(interaction)
        embed = _format_locks_embed(guild, options)
        embed.title = "✅ Global Format Lock Saved"
        embed.description = "Future scans will use this format as the server default unless a category or channel override exists."
        await interaction.response.edit_message(embed=embed, view=FormatLocksView())

    @discord.ui.button(label="Lock Category", emoji="🗂️", style=discord.ButtonStyle.primary, custom_id="dank_design:open_category_lock", row=1)
    async def open_category_lock(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        embed = discord.Embed(
            title="🗂️ Lock Format to Category",
            description="Pick a category. The current draft format will become the desired format for that category and its children.",
            color=discord.Color.blurple(),
        )
        await interaction.response.edit_message(embed=embed, view=CategoryFormatLockPickerView())

    @discord.ui.button(label="Lock Channel Override", emoji="#️⃣", style=discord.ButtonStyle.primary, custom_id="dank_design:open_channel_lock", row=1)
    async def open_channel_lock(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        embed = discord.Embed(
            title="#️⃣ Lock Format to Channel",
            description="Pick one channel/category. The current draft format will override global/category rules for that item.",
            color=discord.Color.blurple(),
        )
        await interaction.response.edit_message(embed=embed, view=ChannelFormatLockPickerView())

    @discord.ui.button(label="Clear Global Lock", emoji="🧹", style=discord.ButtonStyle.secondary, custom_id="dank_design:clear_global_lock", row=2)
    async def clear_global(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        options = await _clear_global_lock(interaction)
        embed = _format_locks_embed(guild, options)
        embed.title = "🧹 Global Format Lock Cleared"
        await interaction.response.edit_message(embed=embed, view=FormatLocksView())

    @discord.ui.button(label="Clear All Locks", emoji="⚠️", style=discord.ButtonStyle.danger, custom_id="dank_design:clear_all_locks", row=2)
    async def clear_all(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        options = await _clear_all_locks(interaction)
        embed = _format_locks_embed(guild, options)
        embed.title = "🧹 All Format Locks Cleared"
        embed.description = "Global, category, and channel format locks were cleared. Auto theme rules are active again."
        await interaction.response.edit_message(embed=embed, view=FormatLocksView())

    @discord.ui.button(label="Back to Design Studio", emoji="🎨", style=discord.ButtonStyle.secondary, custom_id="dank_design:format_locks_back", row=4)
    async def back_to_studio(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        assert interaction.guild is not None
        options = await _load_design_options(int(interaction.guild.id))
        await interaction.response.edit_message(embed=_home_embed(interaction.guild, options), view=DesignHomeView(options))



# ---------------------------------------------------------------------------
# Category / Channel Design Editor
# Bot-owned picker. Do not use Discord ChannelSelect here because styled names
# can be hard to search/select from mobile share/picker UI.
# ---------------------------------------------------------------------------

EDITOR_PAGE_SIZE = 8


def _short_label(value: Any, limit: int = 64) -> str:
    text = _safe_str(value, "Unnamed")
    return text if len(text) <= limit else text[: max(1, limit - 1)] + "…"


def _channel_display_line(channel: discord.abc.GuildChannel) -> str:
    kind = _kind(channel)
    icon = {
        "category": "🗂️",
        "text": "#️⃣",
        "voice": "🔊",
        "stage": "🎙️",
        "forum": "💬",
    }.get(kind, "▫️")
    return f"{icon} {_safe_str(getattr(channel, 'name', 'unnamed'))}"


def _category_channels(guild: discord.Guild, category_id: int) -> list[discord.abc.GuildChannel]:
    category = guild.get_channel(int(category_id))
    if not isinstance(category, discord.CategoryChannel):
        return []
    out: list[discord.abc.GuildChannel] = []
    for channel in [category] + list(getattr(category, "channels", []) or []):
        if _kind(channel) != "other":
            out.append(channel)
    return out


def _all_editor_channels(guild: discord.Guild) -> list[discord.abc.GuildChannel]:
    out: list[discord.abc.GuildChannel] = []
    seen: set[int] = set()
    for category in list(getattr(guild, "categories", []) or []):
        cid = _safe_int(getattr(category, "id", 0), 0)
        if cid > 0 and cid not in seen:
            seen.add(cid)
            out.append(category)
        for child in list(getattr(category, "channels", []) or []):
            child_id = _safe_int(getattr(child, "id", 0), 0)
            if child_id > 0 and child_id not in seen and _kind(child) != "other":
                seen.add(child_id)
                out.append(child)
    for channel in list(getattr(guild, "channels", []) or []):
        cid = _safe_int(getattr(channel, "id", 0), 0)
        if cid > 0 and cid not in seen and _kind(channel) != "other":
            seen.add(cid)
            out.append(channel)
    return out[: studio.MAX_PLAN_ITEMS]


def _filter_plan_for_category(items: list[dict[str, Any]], category_id: int) -> list[dict[str, Any]]:
    wanted = str(int(category_id))
    out: list[dict[str, Any]] = []
    for item in items:
        kind = _safe_str(item.get("kind"))
        channel_id = str(item.get("channel_id") or "")
        item_category_id = str(item.get("category_id") or "")
        if kind == "category" and channel_id == wanted:
            out.append(item)
        elif item_category_id == wanted:
            out.append(item)
    return out


def _filter_plan_for_channel(items: list[dict[str, Any]], channel_id: int) -> list[dict[str, Any]]:
    wanted = str(int(channel_id))
    return [item for item in items if str(item.get("channel_id") or "") == wanted]


async def _preview_scope(
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
    all_items = await build_design_plan(guild, options)

    if category_id is not None:
        items = _filter_plan_for_category(all_items, int(category_id))
    elif channel_id is not None:
        items = _filter_plan_for_channel(all_items, int(channel_id))
    else:
        items = all_items

    key = _key(int(guild.id), int(interaction.user.id))
    _PENDING[key] = {
        "created_at": time.time(),
        "items": items,
        "options": dict(options),
        "mode": mode,
        "scope_title": scope_title,
    }

    has_blockers = any(item.get("status") == "failed" for item in items)
    has_changes = any(item.get("status") == "changed" for item in items)
    await interaction.edit_original_response(
        embed=_preview_embed(guild, items, title=scope_title),
        view=DesignPreviewView(can_apply=not has_blockers and has_changes),
    )


class DesignCategoryEditorButton(discord.ui.Button):
    def __init__(self, *, row: int = 3) -> None:
        super().__init__(
            label="Category Editor",
            emoji="🗂️",
            style=discord.ButtonStyle.primary,
            custom_id="dank_design:category_editor",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.edit_message(
            embed=_category_editor_embed(guild, page=0),
            view=CategoryEditorPickerView(guild, page=0),
        )


class DesignChannelEditorButton(discord.ui.Button):
    def __init__(self, *, row: int = 3) -> None:
        super().__init__(
            label="Channel Editor",
            emoji="#️⃣",
            style=discord.ButtonStyle.primary,
            custom_id="dank_design:channel_editor",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.edit_message(
            embed=_channel_editor_embed(guild, page=0),
            view=ChannelEditorPickerView(guild, page=0),
        )


def _category_editor_embed(guild: discord.Guild, *, page: int) -> discord.Embed:
    categories = list(getattr(guild, "categories", []) or [])
    total_pages = max(1, (len(categories) + EDITOR_PAGE_SIZE - 1) // EDITOR_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * EDITOR_PAGE_SIZE
    chunk = categories[start:start + EDITOR_PAGE_SIZE]

    embed = discord.Embed(
        title="🗂️ Category Design Editor",
        description=(
            "Pick a category using the buttons below.\\n\\n"
            "This is Dank Shield's own picker, built for styled names and mobile use."
        ),
        color=discord.Color.blurple(),
    )
    if not chunk:
        embed.add_field(name="Categories", value="No categories found.", inline=False)
    else:
        lines = []
        for index, category in enumerate(chunk, start=1):
            child_count = len(list(getattr(category, "channels", []) or []))
            lines.append(f"**{index}.** `{_safe_str(getattr(category, 'name', 'unnamed'))}` · {child_count} child channel(s)")
        embed.add_field(name=f"Categories page {page + 1}/{total_pages}", value="\\n".join(lines)[:1024], inline=False)
    embed.set_footer(text="Select a category to preview, lock format, or open its channels.")
    return embed


def _channel_editor_embed(guild: discord.Guild, *, page: int, category_id: int | None = None) -> discord.Embed:
    if category_id is not None:
        source = _category_channels(guild, int(category_id))
        category = guild.get_channel(int(category_id))
        title = f"#️⃣ Channel Editor · {_safe_str(getattr(category, 'name', 'Category'))}"
    else:
        source = _all_editor_channels(guild)
        title = "#️⃣ Channel Design Editor"

    total_pages = max(1, (len(source) + EDITOR_PAGE_SIZE - 1) // EDITOR_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * EDITOR_PAGE_SIZE
    chunk = source[start:start + EDITOR_PAGE_SIZE]

    embed = discord.Embed(
        title=title,
        description=(
            "Pick a channel/category using the buttons below.\\n\\n"
            "This is Dank Shield's own picker, not Discord's native picker."
        ),
        color=discord.Color.blurple(),
    )
    if not chunk:
        embed.add_field(name="Channels", value="No channels found.", inline=False)
    else:
        lines = []
        for index, channel in enumerate(chunk, start=1):
            lines.append(f"**{index}.** `{_channel_display_line(channel)}`")
        embed.add_field(name=f"Items page {page + 1}/{total_pages}", value="\\n".join(lines)[:1024], inline=False)
    embed.set_footer(text="Select an item to preview, lock format, or fix only that item.")
    return embed


class CategoryPickButton(discord.ui.Button):
    def __init__(self, category: discord.CategoryChannel, *, display_index: int, row: int) -> None:
        super().__init__(
            label=f"{display_index}. {_short_label(getattr(category, 'name', 'Category'), 54)}",
            emoji="🗂️",
            style=discord.ButtonStyle.secondary,
            custom_id=f"dank_design:pick_category:{int(category.id)}",
            row=row,
        )
        self.category_id = int(category.id)

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        category = guild.get_channel(self.category_id)
        if not isinstance(category, discord.CategoryChannel):
            return await interaction.response.send_message("That category no longer exists.", ephemeral=True)
        await interaction.response.edit_message(
            embed=_category_action_embed(category),
            view=CategoryEditorActionView(self.category_id),
        )


class ChannelPickButton(discord.ui.Button):
    def __init__(self, channel: discord.abc.GuildChannel, *, display_index: int, row: int, category_id: int | None = None) -> None:
        super().__init__(
            label=f"{display_index}. {_short_label(getattr(channel, 'name', 'Channel'), 54)}",
            emoji={"category": "🗂️", "voice": "🔊", "text": "#️⃣", "forum": "💬", "stage": "🎙️"}.get(_kind(channel), "#️⃣"),
            style=discord.ButtonStyle.secondary,
            custom_id=f"dank_design:pick_channel:{int(channel.id)}",
            row=row,
        )
        self.channel_id = int(channel.id)
        self.category_id = int(category_id) if category_id is not None else None

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        channel = guild.get_channel(self.channel_id)
        if channel is None:
            return await interaction.response.send_message("That channel no longer exists.", ephemeral=True)
        await interaction.response.edit_message(
            embed=_channel_action_embed(channel),
            view=ChannelEditorActionView(self.channel_id, category_id=self.category_id),
        )


class CategoryEditorPickerView(discord.ui.View):
    def __init__(self, guild: discord.Guild, *, page: int = 0) -> None:
        super().__init__(timeout=900)
        categories = list(getattr(guild, "categories", []) or [])
        total_pages = max(1, (len(categories) + EDITOR_PAGE_SIZE - 1) // EDITOR_PAGE_SIZE)
        page = max(0, min(page, total_pages - 1))
        start = page * EDITOR_PAGE_SIZE
        chunk = categories[start:start + EDITOR_PAGE_SIZE]

        for offset, category in enumerate(chunk):
            self.add_item(CategoryPickButton(category, display_index=offset + 1, row=offset // 2))

        nav_row = 4
        if page > 0:
            self.add_item(CategoryPageButton(page - 1, label="Prev", emoji="⬅️", row=nav_row))
        if page < total_pages - 1:
            self.add_item(CategoryPageButton(page + 1, label="Next", emoji="➡️", row=nav_row))
        self.add_item(BackToDesignButton(row=nav_row))


class CategoryPageButton(discord.ui.Button):
    def __init__(self, page: int, *, label: str, emoji: str, row: int) -> None:
        super().__init__(label=label, emoji=emoji, style=discord.ButtonStyle.secondary, custom_id=f"dank_design:category_page:{page}", row=row)
        self.page = int(page)

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.edit_message(embed=_category_editor_embed(guild, page=self.page), view=CategoryEditorPickerView(guild, page=self.page))


class ChannelEditorPickerView(discord.ui.View):
    def __init__(self, guild: discord.Guild, *, page: int = 0, category_id: int | None = None) -> None:
        super().__init__(timeout=900)
        source = _category_channels(guild, int(category_id)) if category_id is not None else _all_editor_channels(guild)
        total_pages = max(1, (len(source) + EDITOR_PAGE_SIZE - 1) // EDITOR_PAGE_SIZE)
        page = max(0, min(page, total_pages - 1))
        start = page * EDITOR_PAGE_SIZE
        chunk = source[start:start + EDITOR_PAGE_SIZE]

        for offset, channel in enumerate(chunk):
            self.add_item(ChannelPickButton(channel, display_index=offset + 1, row=offset // 2, category_id=category_id))

        nav_row = 4
        if page > 0:
            self.add_item(ChannelPageButton(page - 1, label="Prev", emoji="⬅️", row=nav_row, category_id=category_id))
        if page < total_pages - 1:
            self.add_item(ChannelPageButton(page + 1, label="Next", emoji="➡️", row=nav_row, category_id=category_id))
        if category_id is not None:
            self.add_item(BackToCategoryButton(int(category_id), row=nav_row))
        else:
            self.add_item(BackToDesignButton(row=nav_row))


class ChannelPageButton(discord.ui.Button):
    def __init__(self, page: int, *, label: str, emoji: str, row: int, category_id: int | None = None) -> None:
        super().__init__(label=label, emoji=emoji, style=discord.ButtonStyle.secondary, custom_id=f"dank_design:channel_page:{page}", row=row)
        self.page = int(page)
        self.category_id = int(category_id) if category_id is not None else None

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.edit_message(
            embed=_channel_editor_embed(guild, page=self.page, category_id=self.category_id),
            view=ChannelEditorPickerView(guild, page=self.page, category_id=self.category_id),
        )


def _category_action_embed(category: discord.CategoryChannel) -> discord.Embed:
    embed = discord.Embed(
        title=f"🗂️ Edit Category · {_safe_str(getattr(category, 'name', 'Category'))}",
        description=(
            "Use the current draft format on this category, preview/fix only this category, or open its child channels."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Category", value=f"{category.mention} (`{category.id}`)", inline=False)
    embed.add_field(name="Options", value="Lock format, preview/fix this category scope, or edit child channels.", inline=False)
    return embed


def _channel_action_embed(channel: discord.abc.GuildChannel) -> discord.Embed:
    embed = discord.Embed(
        title=f"#️⃣ Edit Channel · {_safe_str(getattr(channel, 'name', 'Channel'))}",
        description="Lock the current draft format to this item, or preview/fix only this item.",
        color=discord.Color.blurple(),
    )
    mention = getattr(channel, "mention", f"`{getattr(channel, 'id', '')}`")
    embed.add_field(name="Item", value=f"{mention} (`{getattr(channel, 'id', '')}`)", inline=False)
    embed.add_field(name="Kind", value=_kind(channel), inline=True)
    return embed


class CategoryEditorActionView(discord.ui.View):
    def __init__(self, category_id: int) -> None:
        super().__init__(timeout=900)
        self.category_id = int(category_id)

    @discord.ui.button(label="Lock Current Format Here", emoji="🔒", style=discord.ButtonStyle.success, custom_id="dank_design:category_lock_here", row=0)
    async def lock_here(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        category = guild.get_channel(self.category_id)
        if not isinstance(category, discord.CategoryChannel):
            return await interaction.response.send_message("That category no longer exists.", ephemeral=True)
        options = await _save_category_lock(interaction, self.category_id)
        embed = _category_action_embed(category)
        embed.title = "✅ Category Format Locked"
        embed.description = "Saved the current draft format for this category and its children."
        counts = _lock_count(options)
        embed.add_field(name="Saved locks", value=f"Global: {counts['global']} • Categories: {counts['categories']} • Channels: {counts['channels']}", inline=False)
        await interaction.response.edit_message(embed=embed, view=CategoryEditorActionView(self.category_id))

    @discord.ui.button(label="Preview / Fix This Category", emoji="👁️", style=discord.ButtonStyle.primary, custom_id="dank_design:category_preview_scope", row=1)
    async def preview_category(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _preview_scope(
            interaction,
            scope_title="👁 Category Design Preview",
            mode="category_editor",
            category_id=self.category_id,
        )

    @discord.ui.button(label="Edit Child Channels", emoji="#️⃣", style=discord.ButtonStyle.secondary, custom_id="dank_design:category_children", row=2)
    async def edit_children(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.edit_message(
            embed=_channel_editor_embed(guild, page=0, category_id=self.category_id),
            view=ChannelEditorPickerView(guild, page=0, category_id=self.category_id),
        )

    @discord.ui.button(label="Back to Categories", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="dank_design:category_action_back", row=4)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.edit_message(embed=_category_editor_embed(guild, page=0), view=CategoryEditorPickerView(guild, page=0))


class ChannelEditorActionView(discord.ui.View):
    def __init__(self, channel_id: int, *, category_id: int | None = None) -> None:
        super().__init__(timeout=900)
        self.channel_id = int(channel_id)
        self.category_id = int(category_id) if category_id is not None else None

    @discord.ui.button(label="Lock Current Format to This", emoji="🔒", style=discord.ButtonStyle.success, custom_id="dank_design:channel_lock_here", row=0)
    async def lock_here(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        channel = guild.get_channel(self.channel_id)
        if channel is None:
            return await interaction.response.send_message("That channel no longer exists.", ephemeral=True)
        options = await _save_channel_lock(interaction, self.channel_id)
        embed = _channel_action_embed(channel)
        embed.title = "✅ Channel Override Locked"
        embed.description = "Saved the current draft format as an override for this item."
        counts = _lock_count(options)
        embed.add_field(name="Saved locks", value=f"Global: {counts['global']} • Categories: {counts['categories']} • Channels: {counts['channels']}", inline=False)
        await interaction.response.edit_message(embed=embed, view=ChannelEditorActionView(self.channel_id, category_id=self.category_id))

    @discord.ui.button(label="Preview / Fix This Only", emoji="👁️", style=discord.ButtonStyle.primary, custom_id="dank_design:channel_preview_scope", row=1)
    async def preview_channel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _preview_scope(
            interaction,
            scope_title="👁 Channel Design Preview",
            mode="channel_editor",
            channel_id=self.channel_id,
        )

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="dank_design:channel_action_back", row=4)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.edit_message(
            embed=_channel_editor_embed(guild, page=0, category_id=self.category_id),
            view=ChannelEditorPickerView(guild, page=0, category_id=self.category_id),
        )


class BackToDesignButton(discord.ui.Button):
    def __init__(self, *, row: int) -> None:
        super().__init__(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="dank_design:editor_back_home", row=row)

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not await _require_design_permission(interaction):
            return
        assert interaction.guild is not None
        options = await _load_design_options(int(interaction.guild.id))
        await interaction.response.edit_message(embed=_home_embed(interaction.guild, options), view=DesignHomeView(options))


class BackToCategoryButton(discord.ui.Button):
    def __init__(self, category_id: int, *, row: int) -> None:
        super().__init__(label="Category", emoji="🗂️", style=discord.ButtonStyle.secondary, custom_id="dank_design:editor_back_category", row=row)
        self.category_id = int(category_id)

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        category = guild.get_channel(self.category_id)
        if not isinstance(category, discord.CategoryChannel):
            return await interaction.response.edit_message(embed=_category_editor_embed(guild, page=0), view=CategoryEditorPickerView(guild, page=0))
        await interaction.response.edit_message(embed=_category_action_embed(category), view=CategoryEditorActionView(self.category_id))




class DesignHomeView(discord.ui.View):
    def __init__(self, options: Mapping[str, Any] | None = None) -> None:
        super().__init__(timeout=900)
        options = options or {}
        self.add_item(ThemeSelect(_safe_str(options.get("theme_id"), "gothic_clean")))
        self.add_item(StrengthSelect(_safe_int(options.get("strength"), 2)))
        existing_ids = {str(getattr(child, "custom_id", "")) for child in getattr(self, "children", []) or []}
        if "dank_design:category_editor" not in existing_ids:
            self.add_item(DesignCategoryEditorButton(row=3))
        if "dank_design:channel_editor" not in existing_ids:
            self.add_item(DesignChannelEditorButton(row=3))
        existing_ids = {str(getattr(child, "custom_id", "")) for child in getattr(self, "children", []) or []}
        if "dank_design:format_locks" not in existing_ids:
            self.add_item(FormatLocksButton(row=4))

    @discord.ui.button(label="Preview Selected Design", emoji="👁️", style=discord.ButtonStyle.primary, custom_id="dank_design:preview", row=2)
    async def preview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.defer(ephemeral=True, thinking=True)
        options = await _load_design_options(int(guild.id))
        items = await build_design_plan(guild, options)
        _PENDING[_key(int(guild.id), int(interaction.user.id))] = {"created_at": time.time(), "items": items, "options": dict(options)}
        has_blockers = any(item.get("status") == "failed" for item in items)
        await interaction.edit_original_response(embed=_preview_embed(guild, items), view=DesignPreviewView(can_apply=not has_blockers and any(item.get("status") == "changed" for item in items)))

    @discord.ui.button(label="Find & Fix Inconsistencies", emoji="🧭", style=discord.ButtonStyle.success, custom_id="dank_design:consistency_check", row=3)
    async def consistency_check(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None

        await interaction.response.defer(ephemeral=True, thinking=True)

        options = await _load_design_options(int(guild.id))
        items = await build_design_plan(guild, options)
        key = _key(int(guild.id), int(interaction.user.id))
        _PENDING[key] = {"created_at": time.time(), "items": items, "options": dict(options), "mode": "consistency_check"}

        has_blockers = any(item.get("status") == "failed" for item in items)
        has_changes = any(item.get("status") == "changed" for item in items)

        await interaction.edit_original_response(
            embed=_consistency_embed(guild, items, options),
            view=DesignPreviewView(can_apply=not has_blockers and has_changes),
        )

    @discord.ui.button(label="Rollback", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank_design:rollback_home", row=2)
    async def rollback_home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_rollback(interaction)

    @discord.ui.button(label="Help", emoji="❓", style=discord.ButtonStyle.secondary, custom_id="dank_design:help", row=2)
    async def help(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        embed = discord.Embed(
            title="❓ Server Design Studio Help",
            description="Use Safe/Balanced styles for important public channels. Full Drip is prettier but heavier on mobile and screen readers.",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Font guarantee", value="If a letter cannot transform, Dank Shield uses Auto-Safe Transform: requested font → fallback font → readable normal text. It will not block the rename for that alone.", inline=False)
        embed.add_field(name="Safety", value="Hard blockers are only: missing Manage Channels, empty/too-long names, duplicate outputs, protected items, or dangerous invisible characters.", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class DesignPreviewView(discord.ui.View):
    def __init__(self, *, can_apply: bool) -> None:
        super().__init__(timeout=900)
        self.apply.disabled = not can_apply

    @discord.ui.button(label="Apply / Fix All", emoji="✅", style=discord.ButtonStyle.danger, custom_id="dank_design:apply", row=0)
    async def apply(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        key = _key(int(guild.id), int(interaction.user.id))
        payload = _PENDING.get(key) or {}
        items = list(payload.get("items") or [])
        if not items:
            await interaction.response.send_message("No saved preview found. Press **Preview** first.", ephemeral=True)
            return
        if any(item.get("status") == "failed" for item in items):
            await interaction.response.send_message("❌ This preview has hard blockers. Fix them before applying.", ephemeral=True)
            return
        lock = _lock_for(int(guild.id))
        if lock.locked():
            await interaction.response.send_message("⏳ A design job is already running for this server. Wait for it to finish.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=False)
        changed = 0
        skipped = 0
        failed: list[str] = []
        snapshot: list[dict[str, Any]] = []
        async with lock:
            for index, item in enumerate(items, start=1):
                if item.get("status") != "changed":
                    skipped += 1
                    continue
                channel = guild.get_channel(_safe_int(item.get("channel_id"), 0))
                if channel is None:
                    failed.append(f"missing `{item.get('before')}`")
                    continue
                before = _safe_str(item.get("before"))
                after = _safe_str(item.get("after"))[: studio.DISCORD_NAME_LIMIT]
                current = _safe_str(getattr(channel, "name", ""))
                if current != before:
                    failed.append(f"stale `{before}` is now `{current}`")
                    continue
                try:
                    await channel.edit(name=after, reason=f"Dank Shield Server Design apply by {int(interaction.user.id)}")
                    changed += 1
                    snapshot.append({**item, "old_name": before, "new_name": after, "admin_id": str(int(interaction.user.id)), "timestamp": time.time(), "action_type": "apply"})
                    if changed % 5 == 0:
                        await interaction.edit_original_response(content=f"🚀 Applying design… changed {changed}, skipped {skipped}, failed {len(failed)}. Current: `{after}`")
                    await asyncio.sleep(studio.DEFAULT_DELAY_SECONDS)
                except Exception as exc:
                    failed.append(f"`{current}`: {type(exc).__name__}")
        if snapshot:
            _LAST_SNAPSHOTS.setdefault(_guild_key(int(guild.id)), []).append({"created_at": time.time(), "items": snapshot, "admin_id": str(int(interaction.user.id))})
            _LAST_SNAPSHOTS[_guild_key(int(guild.id))] = _LAST_SNAPSHOTS[_guild_key(int(guild.id))][-5:]
        _PENDING.pop(key, None)
        mode = _safe_str(payload.get("mode"), "preview")
        complete_title = "✅ Design Inconsistencies Fixed" if mode == "consistency_check" else "✅ Server Design Apply Complete"
        complete_description = (
            f"Changed **{changed}** item(s). Skipped **{skipped}**. Failed **{len(failed)}**."
            if mode != "consistency_check"
            else f"Repaired **{changed}** inconsistent name(s). Safe skipped **{skipped}**. Failed **{len(failed)}**."
        )
        embed = discord.Embed(
            title=complete_title,
            description=complete_description,
            color=discord.Color.green() if not failed else discord.Color.orange(),
        )
        if failed:
            embed.add_field(name="Skipped / Failed", value="\n".join(failed[:10])[:1024], inline=False)
        if snapshot:
            embed.add_field(name="Rollback", value="A rollback snapshot was created. Use **Rollback** if the style does not look right.", inline=False)
        await interaction.edit_original_response(content=None, embed=embed, view=DesignDoneView(can_rollback=bool(snapshot)))

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="dank_design:preview_back", row=0)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        assert interaction.guild is not None
        options = await _load_design_options(int(interaction.guild.id))
        await interaction.response.edit_message(embed=_home_embed(interaction.guild, options), view=DesignHomeView(options))


class DesignDoneView(discord.ui.View):
    def __init__(self, *, can_rollback: bool) -> None:
        super().__init__(timeout=900)
        self.rollback.disabled = not can_rollback

    @discord.ui.button(label="Rollback", emoji="↩️", style=discord.ButtonStyle.danger, custom_id="dank_design:rollback_done", row=0)
    async def rollback(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_rollback(interaction)

    @discord.ui.button(label="Back to Studio", emoji="🎨", style=discord.ButtonStyle.secondary, custom_id="dank_design:done_back", row=0)
    async def done_back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        assert interaction.guild is not None
        options = await _load_design_options(int(interaction.guild.id))
        await interaction.response.edit_message(embed=_home_embed(interaction.guild, options), view=DesignHomeView(options))


async def _open_rollback(interaction: discord.Interaction) -> None:
    if not await _require_design_permission(interaction):
        return
    guild = interaction.guild
    assert guild is not None
    snapshots = _LAST_SNAPSHOTS.get(_guild_key(int(guild.id))) or []
    if not snapshots:
        await interaction.response.send_message("No rollback snapshot is available for this server in the current bot session.", ephemeral=True)
        return
    latest = snapshots[-1]
    items = list(latest.get("items") or [])
    preview = []
    for item in reversed(items[-10:]):
        preview.append(f"↩️ `{item.get('new_name')}` → `{item.get('old_name')}`")
    embed = discord.Embed(title="↩️ Rollback Preview", description="Rollback uses the same safe 2-second rename queue.", color=discord.Color.orange())
    embed.add_field(name="Items", value=str(len(items)), inline=True)
    embed.add_field(name="Preview", value="\n".join(preview)[:1024] or "No items.", inline=False)
    await interaction.response.send_message(embed=embed, view=RollbackConfirmView(), ephemeral=True)


class RollbackConfirmView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Rollback Last Apply", emoji="↩️", style=discord.ButtonStyle.danger, custom_id="dank_design:rollback_confirm", row=0)
    async def rollback_confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        lock = _lock_for(int(guild.id))
        if lock.locked():
            await interaction.response.send_message("⏳ A design job is already running for this server. Wait for it to finish.", ephemeral=True)
            return
        snapshots = _LAST_SNAPSHOTS.get(_guild_key(int(guild.id))) or []
        if not snapshots:
            await interaction.response.send_message("No rollback snapshot found.", ephemeral=True)
            return
        latest = snapshots[-1]
        items = list(latest.get("items") or [])
        await interaction.response.defer(ephemeral=True, thinking=False)
        reverted = 0
        failed: list[str] = []
        async with lock:
            for item in reversed(items):
                channel = guild.get_channel(_safe_int(item.get("channel_id"), 0))
                if channel is None:
                    failed.append(f"missing `{item.get('new_name')}`")
                    continue
                current = _safe_str(getattr(channel, "name", ""))
                new_name = _safe_str(item.get("new_name"))
                old_name = _safe_str(item.get("old_name"))[: studio.DISCORD_NAME_LIMIT]
                if current != new_name:
                    failed.append(f"stale `{new_name}` is now `{current}`")
                    continue
                try:
                    await channel.edit(name=old_name, reason=f"Dank Shield Server Design rollback by {int(interaction.user.id)}")
                    reverted += 1
                    await asyncio.sleep(studio.DEFAULT_DELAY_SECONDS)
                except Exception as exc:
                    failed.append(f"`{current}`: {type(exc).__name__}")
        snapshots.pop()
        embed = discord.Embed(title="↩️ Rollback Complete", description=f"Restored **{reverted}** item(s). Failed **{len(failed)}**.", color=discord.Color.green() if not failed else discord.Color.orange())
        if failed:
            embed.add_field(name="Skipped / Failed", value="\n".join(failed[:10])[:1024], inline=False)
        await interaction.edit_original_response(embed=embed, view=None)


async def open_design_studio(interaction: discord.Interaction) -> None:
    if not await _require_design_permission(interaction):
        return
    assert interaction.guild is not None
    options = await _load_design_options(int(interaction.guild.id))
    await interaction.response.send_message(embed=_home_embed(interaction.guild, options), view=DesignHomeView(options), ephemeral=True)


def _register_command() -> None:
    import stoney_verify.commands_ext as commands_ext
    from stoney_verify.commands_ext.public_setup_group import stoney_group

    allowed = set(getattr(commands_ext, "_ALLOWED_STONEY_CHILDREN", set()) or set())
    allowed.add("design")
    commands_ext._ALLOWED_STONEY_CHILDREN = allowed
    if stoney_group.get_command("design") is not None:
        return

    @stoney_group.command(name="design", description="Open the Server Design Studio for channel/category name styling.")
    async def dank_design(interaction: discord.Interaction) -> None:
        await open_design_studio(interaction)


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        _register_command()
        _PATCHED = True
        print("✅ server_design_studio_command_guard active; /dank design registered")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ server_design_studio_command_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply", "open_design_studio", "build_design_plan"]
