from __future__ import annotations

"""Public /dank design command for the Server Design Studio.

The runtime guard keeps the command in the existing /dank group and uses the
pure service engine for preview/apply/rollback. It only edits channel/category
names and never mutates permissions, overwrites, topics, order, slowmode, NSFW,
archive settings, or category placement.
"""

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Mapping

import discord

from stoney_verify.services import server_design_studio as studio

_PATCHED = False
_PENDING: dict[str, dict[str, Any]] = {}
_LAST_SNAPSHOTS: dict[str, list[dict[str, Any]]] = {}
_LOCKS: dict[str, asyncio.Lock] = {}
_ROLLBACK_LOCK = asyncio.Lock()
ROLLBACK_FILE = Path(
    os.getenv(
        "STONEY_SERVER_DESIGN_ROLLBACK_FILE",
        str(Path(os.getenv("STONEY_DATA_DIR", "data")) / "server_design_rollback_snapshots.json"),
    )
)
_FORMAT_EDITOR_DRAFTS: dict[str, dict[str, Any]] = {}


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


def _clean_visible_design_text(value: Any) -> str:
    text = _safe_str(value)
    if not text:
        return text

    # Catch every visible newline marker variant users reported:
    # "\\n", "\n" as literal text, "/n", "/N", and accidental double escaping.
    for bad in ("\\\\n", "\\\\N", "\\n", "\\N", "\\/n", "\\/N", "/n", "/N"):
        text = text.replace(bad, "\n")

    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")

    return text.strip()


def _clean_design_embed(embed: discord.Embed) -> discord.Embed:
    try:
        if getattr(embed, "title", None):
            embed.title = _clean_visible_design_text(embed.title)
        if getattr(embed, "description", None):
            embed.description = _clean_visible_design_text(embed.description)

        for index, field in enumerate(list(getattr(embed, "fields", []) or [])):
            embed.set_field_at(
                index,
                name=_clean_visible_design_text(getattr(field, "name", "")),
                value=_clean_visible_design_text(getattr(field, "value", "")),
                inline=bool(getattr(field, "inline", False)),
            )

        footer_text = _safe_str(getattr(getattr(embed, "footer", None), "text", ""))
        if footer_text:
            embed.set_footer(text=_clean_visible_design_text(footer_text))
    except Exception:
        pass
    return embed



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


def _load_rollback_store_unlocked() -> dict[str, Any]:
    try:
        if not ROLLBACK_FILE.exists():
            return {}
        data = json.loads(ROLLBACK_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_rollback_store_unlocked(data: dict[str, Any]) -> None:
    ROLLBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = ROLLBACK_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    tmp.replace(ROLLBACK_FILE)


async def _persist_rollback_snapshot(guild_id: int, snapshot: dict[str, Any]) -> None:
    async with _ROLLBACK_LOCK:
        data = _load_rollback_store_unlocked()
        key = _guild_key(int(guild_id))
        rows = data.get(key)
        if not isinstance(rows, list):
            rows = []
        rows.append(dict(snapshot))
        data[key] = rows[-10:]
        _save_rollback_store_unlocked(data)


async def _latest_rollback_snapshot(guild_id: int) -> dict[str, Any] | None:
    key = _guild_key(int(guild_id))
    memory_rows = _LAST_SNAPSHOTS.get(key) or []
    if memory_rows:
        latest = memory_rows[-1]
        return dict(latest) if isinstance(latest, dict) else None

    async with _ROLLBACK_LOCK:
        data = _load_rollback_store_unlocked()
        rows = data.get(key)
        if not isinstance(rows, list) or not rows:
            return None
        latest = rows[-1]
        return dict(latest) if isinstance(latest, dict) else None


async def _pop_latest_rollback_snapshot(guild_id: int) -> dict[str, Any] | None:
    key = _guild_key(int(guild_id))
    popped: dict[str, Any] | None = None

    memory_rows = _LAST_SNAPSHOTS.get(key) or []
    if memory_rows:
        latest = memory_rows.pop()
        popped = dict(latest) if isinstance(latest, dict) else None

    async with _ROLLBACK_LOCK:
        data = _load_rollback_store_unlocked()
        rows = data.get(key)
        if isinstance(rows, list) and rows:
            latest = rows.pop()
            if popped is None and isinstance(latest, dict):
                popped = dict(latest)
            data[key] = rows[-10:]
            _save_rollback_store_unlocked(data)

    return popped



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
        "emoji_override": _safe_str(options.get("emoji_override"), ""),
        "exact_match": bool(options.get("exact_match", False)),
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
        for key in ("theme_id", "strength", "icon_mode", "font", "separator_id", "category_frame_id", "emoji_override", "exact_match"):
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
            "Lock a selected layout once, then reuse it for categories or channels.\n\n"
            "Future previews and consistency checks will compare names against these saved locks instead of guessing from one global auto design."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Saved design rule",
        value=(
            f"Theme: **{getattr(theme, 'label', 'Gothic Clean')}**\n"
            f"Font: **{_safe_str(current_lock.get('font'), 'normal').replace('_', ' ').title()}**\n"
            f"Separator: **{_safe_str(current_lock.get('separator_id'), 'bar_full').replace('_', ' ').title()}**\n"
            f"Category frame: **{_safe_str(current_lock.get('category_frame_id'), 'line').replace('_', ' ').title()}**\n"
            f"Strength: **{_safe_int(current_lock.get('strength'), 4)}/5**"
        ),
        inline=False,
    )
    embed.add_field(
        name="Saved locks",
        value=(
            f"Global: **{'On' if counts['global'] else 'Off'}**\n"
            f"Categories: **{counts['categories']}**\n"
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
    return _clean_design_embed(embed)



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
            emoji_override=_safe_str(effective_options.get("emoji_override")) or None,
            exact_match=bool(effective_options.get("exact_match", False)),
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
    font_text = str(getattr(theme, "font", "normal") or "normal").replace("_", " ").title()
    counts = _lock_count(options)

    embed = discord.Embed(
        title="🎨 Dank Design Studio",
        description=(
            "Design channel/category names without touching permissions, roles, topics, order, tickets, or verification.\n\n"
            "**Safe workflow:** review first → preview exact names → apply only when you approve."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Recommended",
        value=(
            "🧭 **Review Repairs** — best for hand-built servers. Copies the live majority layout and repairs only outliers.\n"
            "👁️ **Preview Server** — shows a full-server style preview without changing anything."
        ),
        inline=False,
    )
    embed.add_field(
        name="Edit one thing",
        value=(
            "🗂️ **Category Editor** — preview, rename, or style one category.\n"
            "#️⃣ **Channel Editor** — preview, rename, or style one channel."
        ),
        inline=False,
    )
    embed.add_field(
        name="Current style",
        value=(
            f"Theme: **{theme.label}**\n"
            f"Font: **{font_text}**\n"
            f"Strength: **{strength}/5**\n"
            "Rename speed: **2 seconds per item**"
        ),
        inline=True,
    )
    embed.add_field(
        name="Saved rules",
        value=(
            f"Global: **{'On' if counts['global'] else 'Off'}**\n"
            f"Categories: **{counts['categories']}**\n"
            f"Channels: **{counts['channels']}**"
        ),
        inline=True,
    )
    embed.set_footer(text="Names only • Review before Apply • Use Advanced only for locks, protection, rollback, and help")
    return _clean_design_embed(embed)



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

    changed_items = [item for item in items if item.get("status") == "changed"]
    changed_lines = []
    for item in changed_items[:12]:
        before = _safe_str(item.get("before"))
        after = _safe_str(item.get("after"))
        changed_lines.append(f"✅ `{before}` → `{after}`"[:240])

    if not changed_lines:
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
        warning_kinds = []
        seen_warning_kinds = set()
        for item in items:
            for warning in list(item.get("warnings") or []):
                text = _safe_str(warning)
                if not text:
                    continue
                if "Decorative font" in text:
                    label = "Decorative font readability note"
                elif "fallback glyph" in text or "Auto-Safe Transform" in text:
                    label = "Unsupported glyph fallback note"
                elif "Already matches" in text:
                    label = "Already styled skip note"
                elif "Safe skip" in text:
                    label = "Protected safe-skip note"
                else:
                    label = text[:80]
                if label not in seen_warning_kinds:
                    seen_warning_kinds.add(label)
                    warning_kinds.append(f"• {label}")
        embed.add_field(
            name="Notes",
            value=("\n".join(warning_kinds[:6]) if warning_kinds else "Safe notes only; no blockers.")[:1024],
            inline=False,
        )

    embed.set_footer(text="Apply is disabled only for real failures. Font fallback notes and safe skips do not block Apply.")
    return _clean_design_embed(embed)



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
        rows.append(f"🧩 `{before}` → `{after}`"[:240])
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
            "Dank Shield compared the current channel/category names against the saved design draft.\n\n"
            "Use **Fix All Inconsistencies** to repair only names that drifted from the saved format."
        ),
        color=discord.Color.green() if not summary["failed"] else discord.Color.orange(),
    )
    embed.add_field(
        name="Saved design",
        value=(
            f"Theme: **{theme.label}**\n"
            f"Font: **{font_text}**\n"
            f"Strength: **{strength}/5**\n"
            "Delay: **2 seconds per rename**"
        ),
        inline=False,
    )
    embed.add_field(
        name="Results",
        value=(
            f"Matches saved design: **{summary['matches']}**\n"
            f"Needs fix: **{summary['needs_fix']}**\n"
            f"Protected safe skips: **{summary['protected']}**\n"
            f"Cannot fix yet: **{summary['failed']}**\n"
            f"Notes: **{summary['notes']}**"
        ),
        inline=True,
    )
    embed.add_field(
        name="What will be fixed",
        value="\n".join(_consistency_lines(items, limit=12))[:1024],
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
        embed.add_field(name="Cannot fix yet", value="\n".join(failed_lines)[:1024], inline=False)

    embed.set_footer(text="Fix uses the same one-press apply flow and rollback snapshot.")
    return _clean_design_embed(embed)


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
        embed.description = f"Saved the saved design rule for {category.mention}. Future scans will use this lock for the category and its children unless a channel override exists."
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
        embed.description = f"Saved the saved design rule as an exact override for {channel.mention}."
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
            description="Pick a category. The saved design rule will become the desired format for that category and its children.",
            color=discord.Color.blurple(),
        )
        await interaction.response.edit_message(embed=embed, view=CategoryFormatLockPickerView())

    @discord.ui.button(label="Lock Channel Override", emoji="#️⃣", style=discord.ButtonStyle.primary, custom_id="dank_design:open_channel_lock", row=1)
    async def open_channel_lock(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        embed = discord.Embed(
            title="#️⃣ Lock Format to Channel",
            description="Pick one channel/category. The saved design rule will override global/category rules for that item.",
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
# Exact Format Editor
# ---------------------------------------------------------------------------

EDITOR_SEPARATOR_IDS = (
    "none", "bar_full", "bar_thin", "bar_heavy", "dash", "en_dash", "em_dash",
    "middle_dot", "bullet", "katakana_dot", "colon", "single_angle",
    "tri_right", "tri_small", "premium_sparkle", "premium_thin_sparkle",
    "sparkle_small", "small_dot", "presentation_bar", "bracket_corner",
    "bracket_lenticular",
)

EDITOR_FONT_IDS = (
    "normal", "fraktur", "bold_fraktur", "bold_sans", "serif_bold",
    "monospace", "fullwidth", "small_caps", "script", "bold_script",
    "italic_sans", "bold_italic_sans", "serif_italic", "serif_bold_italic",
    "circled", "parenthesized",
)


def _format_editor_key(guild_id: int, user_id: int, scope: str, target_id: int) -> str:
    return f"{int(guild_id)}:{int(user_id)}:{scope}:{int(target_id)}"


def _target_label(guild: discord.Guild, scope: str, target_id: int) -> str:
    ch = guild.get_channel(int(target_id))
    if ch is None:
        return f"{scope} `{target_id}`"
    return f"{scope} `{_safe_str(getattr(ch, 'name', target_id))}`"


def _live_design_records_for_exact_format(guild: discord.Guild) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []

    for category in list(getattr(guild, "categories", []) or []):
        name = _safe_str(getattr(category, "name", ""))
        if name:
            records.append({"name": name, "kind": "category"})

    for channel in list(getattr(guild, "channels", []) or []):
        kind = _kind(channel)
        if kind in {"text", "voice", "stage"}:
            name = _safe_str(getattr(channel, "name", ""))
            if name:
                records.append({"name": name, "kind": "text"})

    return records


def _live_majority_exact_lock(
    guild: discord.Guild | None,
    options: Mapping[str, Any],
    *,
    scope: str,
    target_id: int,
) -> dict[str, Any]:
    if guild is None:
        return {}

    try:
        from stoney_verify.services import server_design_majority_layout as majority

        records = _live_design_records_for_exact_format(guild)
        if not records:
            return {}

        analysis = majority.infer_live_majority_layout(studio, records)
        inferred = majority.apply_majority_to_options(studio, options, analysis, respect_locks=False)
        summary = dict(inferred.get("__majority_layout_summary") or {})

        return {
            "scope": scope,
            "theme_id": _safe_str(inferred.get("theme_id"), _safe_str(options.get("theme_id"), "gothic_clean")),
            "strength": _safe_int(inferred.get("strength"), _safe_int(options.get("strength"), 4)),
            "font": _safe_str(inferred.get("font"), "normal").lower().replace("-", "_"),
            "separator_id": _safe_str(inferred.get("separator_id"), "none"),
            "category_frame_id": _safe_str(inferred.get("category_frame_id"), "plain"),
            "icon_mode": _safe_str(inferred.get("icon_mode"), "replace_missing"),
            "emoji_override": "",
            "exact_match": False,
            "__source": "live_majority",
            "__majority_layout_summary": summary,
            "__majority_separator_id": _safe_str(inferred.get("separator_id"), ""),
            "__majority_font": _safe_str(inferred.get("font"), ""),
            "__majority_category_frame_id": _safe_str(inferred.get("category_frame_id"), ""),
            "__majority_icon_mode": _safe_str(inferred.get("icon_mode"), ""),
        }
    except Exception:
        return {}


def _separator_choice_label(sep_id: Any) -> str:
    sep_id = _safe_str(sep_id, "none")
    if sep_id == "none":
        return "No separator"
    spec = getattr(studio, "SEPARATORS_BY_ID", {}).get(sep_id)
    if spec is not None:
        return _safe_str(getattr(spec, "label", sep_id), sep_id)
    return sep_id.replace("_", " ").title()


def _category_frame_choice_label(frame_id: Any) -> str:
    frame_id = _safe_str(frame_id, "plain")
    if frame_id == "plain":
        return "Plain category names"
    spec = getattr(studio, "CATEGORY_FRAMES_BY_ID", {}).get(frame_id)
    if spec is not None:
        return _safe_str(getattr(spec, "label", frame_id), frame_id)
    return frame_id.replace("_", " ").title()


def _font_choice_label(font_id: Any) -> str:
    return _safe_str(font_id, "normal").replace("_", " ").title()


def _exact_format_conflicts(lock: Mapping[str, Any]) -> list[str]:
    source = _safe_str(lock.get("__source"), "")
    if source == "live_majority":
        return []

    conflicts: list[str] = []

    majority_sep = _safe_str(lock.get("__majority_separator_id"), "")
    majority_font = _safe_str(lock.get("__majority_font"), "")
    majority_frame = _safe_str(lock.get("__majority_category_frame_id"), "")
    majority_icon = _safe_str(lock.get("__majority_icon_mode"), "")

    current_sep = _safe_str(lock.get("separator_id"), "none")
    current_font = _safe_str(lock.get("font"), "normal")
    current_frame = _safe_str(lock.get("category_frame_id"), "plain")
    current_icon = _safe_str(lock.get("icon_mode"), "replace_missing")

    if majority_sep and current_sep != majority_sep:
        conflicts.append(
            f"Separator differs: rule uses **{_separator_choice_label(current_sep)}**, live server uses **{_separator_choice_label(majority_sep)}**."
        )

    if majority_font and current_font != majority_font:
        conflicts.append(
            f"Font differs: rule uses **{_font_choice_label(current_font)}**, live server uses **{_font_choice_label(majority_font)}**."
        )

    if majority_frame and current_frame != majority_frame:
        conflicts.append(
            f"Category frame differs: rule uses **{_category_frame_choice_label(current_frame)}**, live server uses **{_category_frame_choice_label(majority_frame)}**."
        )

    if majority_icon and current_icon != majority_icon:
        conflicts.append(
            f"Emoji behavior differs: rule uses **{current_icon.replace('_', ' ').title()}**, live server uses **{majority_icon.replace('_', ' ').title()}**."
        )

    return conflicts[:4]


def _persistable_exact_lock(lock: Mapping[str, Any]) -> dict[str, Any]:
    return {str(k): v for k, v in dict(lock).items() if not str(k).startswith("__")}


def _initial_editor_lock(
    options: Mapping[str, Any],
    *,
    scope: str,
    target_id: int,
    guild: discord.Guild | None = None,
) -> dict[str, Any]:
    majority_lock = _live_majority_exact_lock(guild, options, scope=scope, target_id=target_id)

    if scope == "category":
        locks = _mapping_dict(options.get("category_format_locks"))
    elif scope == "channel":
        locks = _mapping_dict(options.get("channel_format_locks"))
    else:
        locks = {}

    existing = locks.get(str(int(target_id)))
    if isinstance(existing, Mapping):
        lock = dict(existing)
        lock["__source"] = "saved_exact_rule"

        # Keep the warning/comparison data from live majority without changing the saved rule.
        for key in (
            "__majority_layout_summary",
            "__majority_separator_id",
            "__majority_font",
            "__majority_category_frame_id",
            "__majority_icon_mode",
        ):
            if majority_lock.get(key) is not None:
                lock[key] = majority_lock.get(key)
    elif majority_lock:
        lock = dict(majority_lock)
    else:
        lock = _current_format_lock(options, scope=scope)
        lock["__source"] = "saved_design_rule"

    lock.setdefault("scope", scope)
    lock.setdefault("theme_id", _safe_str(options.get("theme_id"), "gothic_clean"))
    lock.setdefault("strength", max(2, _safe_int(options.get("strength"), 4)))
    lock.setdefault("font", _safe_str(lock.get("font") or _theme_from_options(options).font, "normal").lower().replace("-", "_"))
    lock.setdefault("separator_id", _safe_str(lock.get("separator_id"), "none"))
    lock.setdefault("category_frame_id", _safe_str(lock.get("category_frame_id"), "plain"))
    lock.setdefault("icon_mode", _safe_str(lock.get("icon_mode"), "replace_missing"))
    lock.setdefault("emoji_override", _safe_str(lock.get("emoji_override"), ""))
    return lock


async def _open_exact_format_editor(interaction: discord.Interaction, *, scope: str, target_id: int) -> None:
    if not await _require_design_permission(interaction):
        return

    guild = interaction.guild
    assert guild is not None

    try:
        options = await _load_design_options(int(guild.id))
        lock = _initial_editor_lock(options, scope=scope, target_id=int(target_id), guild=guild)
        key = _format_editor_key(int(guild.id), int(interaction.user.id), scope, int(target_id))
        _FORMAT_EDITOR_DRAFTS[key] = lock

        embed = _exact_format_embed(guild, scope=scope, target_id=int(target_id), lock=lock)
        view = ExactFormatEditorViewFactory(guild, scope, int(target_id), lock)

        await interaction.response.edit_message(embed=embed, view=view)
    except Exception as exc:
        message = (
            "❌ Exact Format could not open. "
            f"`{type(exc).__name__}: {_safe_str(exc)[:160]}`"
        )
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except Exception:
            raise


def _exact_format_embed(guild: discord.Guild, *, scope: str, target_id: int, lock: Mapping[str, Any]) -> discord.Embed:
    font = _safe_str(lock.get("font"), "normal")
    sep = _safe_str(lock.get("separator_id"), "bar_full")
    frame = _safe_str(lock.get("category_frame_id"), "line")
    strength = _safe_int(lock.get("strength"), 4)
    icon_mode = _safe_str(lock.get("icon_mode"), "replace_missing")
    emoji_override = _safe_str(lock.get("emoji_override"), "")

    embed = discord.Embed(
        title="🎛️ Exact Format Editor",
        description=(
            f"Editing **{_target_label(guild, scope, target_id)}**\n\n"
            "**1. Choose** font, separator/layout, frame, and strength.\n"
            "**2. Optional:** set an emoji.\n"
            "**3. Press Save & Preview.**\n"
            "**4. Press Apply Reviewed Changes** on the preview."
        ),
        color=discord.Color.blurple(),
    )
    source_label = {
        "live_majority": "Live server majority",
        "saved_exact_rule": "Saved exact rule",
        "saved_design_rule": "Saved design rule",
        "manual_override": "Manual draft override",
    }.get(_safe_str(lock.get("__source")), "Current draft")

    majority_summary = lock.get("__majority_layout_summary") if isinstance(lock.get("__majority_layout_summary"), Mapping) else {}
    conflicts = _exact_format_conflicts(lock)

    selected_value = (
        f"Source: **{source_label}**\n"
        f"Font: **{font.replace('_', ' ').title()}**\n"
        f"Separator: **{_separator_choice_label(sep)}**\n"
        f"Category frame: **{_category_frame_choice_label(frame)}**\n"
        f"Strength: **{strength}/5**\n"
        f"Icon mode: **{icon_mode.replace('_', ' ').title()}**\n"
        f"Custom emoji: **{emoji_override or 'None'}**\n"
        f"Preview mode: **{'Exact Enforce' if bool(lock.get('exact_match', False)) else 'Smart Fix'}**"
    )

    if majority_summary:
        selected_value += (
            "\n\nDetected server style:"
            f"\nSeparator: **{_safe_str(majority_summary.get('separator'), 'mixed/unknown')}**"
            f"\nCategories: **{_safe_str(majority_summary.get('category_frame'), 'mixed/unknown')}**"
            f"\nFont: **{_safe_str(majority_summary.get('font'), 'mixed/unknown')}**"
        )

    embed.add_field(
        name="Selected format",
        value=selected_value[:1024],
        inline=False,
    )

    embed.add_field(
        name="Conflict check",
        value=(
            "✅ No conflicts found. This follows the detected server style."
            if not conflicts
            else "⚠️ This draft differs from the detected server style.\n"
            + "\n".join(f"• {line}" for line in conflicts)
            + "\n\nUse **Server Style** to reset this draft, or **Save & Preview** to keep the override."
        )[:1024],
        inline=False,
    )
    embed.add_field(
        name="Current layout example",
        value=f"`{_exact_separator_example_text(sep, lock)}`",
        inline=False,
    )
    embed.add_field(
        name="Preview sample",
        value="\n".join(_exact_format_sample_lines(guild, scope=scope, target_id=target_id, lock=lock))[:1024],
        inline=False,
    )
    embed.set_footer(text="Save & Preview first. Nothing is renamed until the preview screen shows Apply.")
    return _clean_design_embed(embed)


def _exact_format_sample_lines(guild: discord.Guild, *, scope: str, target_id: int, lock: Mapping[str, Any]) -> list[str]:
    if scope == "category":
        channels = _category_channels(guild, int(target_id))[:5]
    else:
        ch = guild.get_channel(int(target_id))
        channels = [ch] if ch is not None else []

    if not channels:
        return ["No preview item found."]

    lines: list[str] = []
    for ch in channels:
        kind = _kind(ch)
        try:
            result = studio.build_styled_name(
                _safe_str(getattr(ch, "name", "")),
                kind="category" if kind == "category" else "text",
                theme_id=_safe_str(lock.get("theme_id"), "gothic_clean"),
                strength=_safe_int(lock.get("strength"), 4),
                icon_mode=_safe_str(lock.get("icon_mode"), "replace_missing"),
                protection_rules={},
                separator_id=_safe_str(lock.get("separator_id")) or None,
                category_frame_id=_safe_str(lock.get("category_frame_id")) or None,
                font=_safe_str(lock.get("font")) or None,
                emoji_override=_safe_str(lock.get("emoji_override")) or None,
                exact_match=bool(lock.get("exact_match", False)),
            )
            lines.append(f"`{result.before}` → `{result.after}`")
        except Exception as exc:
            lines.append(f"`{getattr(ch, 'name', ch)}` → preview failed: {type(exc).__name__}")
    return lines


async def _save_exact_lock(interaction: discord.Interaction, *, scope: str, target_id: int) -> dict[str, Any]:
    guild = interaction.guild
    assert guild is not None
    key = _format_editor_key(int(guild.id), int(interaction.user.id), scope, int(target_id))
    lock = dict(_FORMAT_EDITOR_DRAFTS.get(key) or {})
    if not lock:
        options = await _load_design_options(int(guild.id))
        lock = _initial_editor_lock(options, scope=scope, target_id=target_id, guild=guild)

    options = await _load_design_options(int(guild.id))
    lock["scope"] = scope
    lock["locked_at"] = _utc_iso_design()
    persist_lock = _persistable_exact_lock(lock)

    if scope == "category":
        locks = _mapping_dict(options.get("category_format_locks"))
        locks[str(int(target_id))] = persist_lock
        options["category_format_locks"] = locks
    else:
        locks = _mapping_dict(options.get("channel_format_locks"))
        locks[str(int(target_id))] = persist_lock
        options["channel_format_locks"] = locks

    await _save_options(interaction, options)
    return options


class ExactFontSelect(discord.ui.Select):
    def __init__(self, scope: str, target_id: int, current: str) -> None:
        options = [
            discord.SelectOption(
                label=font.replace("_", " ").title(),
                value=font,
                default=font == current,
                description=("Plain searchable text" if font == "normal" else f"Use {font.replace('_', ' ')} font with fallback glyphs")[:100],
            )
            for font in EDITOR_FONT_IDS
        ]
        super().__init__(placeholder="1) Choose font style", min_values=1, max_values=1, options=options[:25], row=0)
        self.scope = scope
        self.target_id = int(target_id)

    async def callback(self, interaction: discord.Interaction) -> None:
        await _update_exact_draft(interaction, scope=self.scope, target_id=self.target_id, patch={"font": self.values[0]})


class ExactSeparatorSelect(discord.ui.Select):
    def __init__(self, scope: str, target_id: int, current: str) -> None:
        options = []
        for sep_id in EDITOR_SEPARATOR_IDS:
            spec = studio.SEPARATORS_BY_ID.get(sep_id)
            if spec is None:
                continue
            options.append(
                discord.SelectOption(
                    label=f"{spec.label} ({sep_id})"[:100],
                    value=sep_id,
                    default=sep_id == current,
                    description=f"Example: {studio.separator_preview(sep_id, emoji='🎮', name='gaming-news')}"[:100],
                )
            )
        super().__init__(placeholder="2) Choose separator / layout", min_values=1, max_values=1, options=options[:25], row=1)
        self.scope = scope
        self.target_id = int(target_id)

    async def callback(self, interaction: discord.Interaction) -> None:
        await _update_exact_draft(interaction, scope=self.scope, target_id=self.target_id, patch={"separator_id": self.values[0]})


class ExactFrameSelect(discord.ui.Select):
    def __init__(self, scope: str, target_id: int, current: str) -> None:
        options = [
            discord.SelectOption(
                label=frame.label[:100],
                value=frame.id,
                default=frame.id == current,
                description=studio.category_frame_preview(frame.id, emoji="🎮", name="gaming")[:100],
            )
            for frame in studio.CATEGORY_FRAMES[:25]
        ]
        super().__init__(placeholder="3) Choose category frame", min_values=1, max_values=1, options=options, row=2)
        self.scope = scope
        self.target_id = int(target_id)

    async def callback(self, interaction: discord.Interaction) -> None:
        await _update_exact_draft(interaction, scope=self.scope, target_id=self.target_id, patch={"category_frame_id": self.values[0]})


class ExactStrengthSelect(discord.ui.Select):
    def __init__(self, scope: str, target_id: int, current: int) -> None:
        labels = {
            1: "1 Minimal",
            2: "2 Clean",
            3: "3 Category Style",
            4: "4 Recommended",
            5: "5 Full Theme",
        }
        options = [
            discord.SelectOption(label=label, value=str(value), default=value == current)
            for value, label in labels.items()
        ]
        super().__init__(placeholder="4) Choose styling strength", min_values=1, max_values=1, options=options, row=3)
        self.scope = scope
        self.target_id = int(target_id)

    async def callback(self, interaction: discord.Interaction) -> None:
        await _update_exact_draft(interaction, scope=self.scope, target_id=self.target_id, patch={"strength": _safe_int(self.values[0], 4)})


class CustomEmojiModal(discord.ui.Modal, title="Set Custom Emoji"):
    emoji = discord.ui.TextInput(
        label="Emoji",
        placeholder="Example: 🎮 — leave blank to clear",
        required=False,
        max_length=16,
    )

    def __init__(self, *, scope: str, target_id: int) -> None:
        super().__init__()
        self.scope = scope
        self.target_id = int(target_id)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = _safe_str(self.emoji.value, "")
        await _update_exact_draft(interaction, scope=self.scope, target_id=self.target_id, patch={"emoji_override": raw})


async def _update_exact_draft(
    interaction: discord.Interaction,
    *,
    scope: str,
    target_id: int,
    patch: Mapping[str, Any],
) -> None:
    if not await _require_design_permission(interaction):
        return
    guild = interaction.guild
    assert guild is not None
    key = _format_editor_key(int(guild.id), int(interaction.user.id), scope, int(target_id))
    current = dict(_FORMAT_EDITOR_DRAFTS.get(key) or {})
    if not current:
        options = await _load_design_options(int(guild.id))
        current = _initial_editor_lock(options, scope=scope, target_id=target_id, guild=guild)
    current.update(dict(patch))
    if any(not str(key).startswith("__") for key in dict(patch)):
        current["__source"] = "manual_override"
    _FORMAT_EDITOR_DRAFTS[key] = current
    await interaction.response.edit_message(
        embed=_exact_format_embed(guild, scope=scope, target_id=target_id, lock=current),
        view=ExactFormatEditorViewFactory(guild, scope, target_id, current),
    )


SEP_EXAMPLE_PAGE_SIZE = 6


def _exact_lock_for_user(guild: discord.Guild, user_id: int, scope: str, target_id: int) -> dict[str, Any]:
    key = _format_editor_key(int(guild.id), int(user_id), scope, int(target_id))
    lock = dict(_FORMAT_EDITOR_DRAFTS.get(key) or {})
    if lock:
        return lock

    return {
        "scope": scope,
        "theme_id": "gothic_clean",
        "strength": 4,
        "font": "fraktur",
        "separator_id": "bar_full",
        "category_frame_id": "line",
        "icon_mode": "replace_missing",
        "emoji_override": "",
    }


def _exact_separator_example_text(sep_id: str, lock: Mapping[str, Any]) -> str:
    spec = studio.SEPARATORS_BY_ID.get(sep_id)
    if spec is None:
        return sep_id

    emoji = _safe_str(lock.get("emoji_override"), "") or "🎮"
    font = _safe_str(lock.get("font"), "normal")
    name_text, _subs = studio.transform_text_safe(
        "gaming-news",
        font,
        fallback_order=studio.fallback_ladder(font),
    )

    try:
        return spec.template.format(emoji=emoji, separator=spec.value, name=name_text).strip()
    except Exception:
        return studio.separator_preview(sep_id, emoji=emoji, name=name_text)


def _separator_gallery_embed(
    guild: discord.Guild,
    *,
    scope: str,
    target_id: int,
    lock: Mapping[str, Any],
    page: int = 0,
) -> discord.Embed:
    separators = list(studio.SEPARATOR_LIBRARY)
    total_pages = max(1, (len(separators) + SEP_EXAMPLE_PAGE_SIZE - 1) // SEP_EXAMPLE_PAGE_SIZE)
    page = max(0, min(int(page), total_pages - 1))
    chunk = separators[page * SEP_EXAMPLE_PAGE_SIZE:(page + 1) * SEP_EXAMPLE_PAGE_SIZE]

    embed = discord.Embed(
        title="🧩 Separator / Layout Examples",
        description=(
            f"Editing **{_target_label(guild, scope, target_id)}**\n\n"
            "Pick the example that looks closest to what you want. "
            "This changes the draft only; press **Save & Preview** after."
        ),
        color=discord.Color.blurple(),
    )

    lines = []
    current = _safe_str(lock.get("separator_id"), "bar_full")
    for index, spec in enumerate(chunk, start=1):
        marker = "✅" if spec.id == current else "▫️"
        lines.append(f"**{index}.** {marker} `{spec.label}` → `{_exact_separator_example_text(spec.id, lock)}`")

    embed.add_field(name=f"Examples page {page + 1}/{total_pages}", value="\n".join(lines)[:1024], inline=False)
    embed.set_footer(text="Use the numbered buttons below. Examples use your selected font and emoji.")
    return _clean_design_embed(embed)


class SeparatorExamplePickButton(discord.ui.Button):
    def __init__(self, sep_id: str, *, label: str, display_index: int, row: int) -> None:
        super().__init__(
            label=f"{display_index}. {_short_label(label, 46) if '_short_label' in globals() else label[:46]}",
            emoji="🧩",
            style=discord.ButtonStyle.secondary,
            custom_id=f"dank_design:sep_example:{sep_id}",
            row=row,
        )
        self.sep_id = sep_id

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        view = getattr(self, "view", None)
        scope = getattr(view, "scope", "channel")
        target_id = int(getattr(view, "target_id", 0))
        await _update_exact_draft(interaction, scope=scope, target_id=target_id, patch={"separator_id": self.sep_id})


class SeparatorExamplesPageButton(discord.ui.Button):
    def __init__(self, page: int, *, label: str, emoji: str, row: int) -> None:
        super().__init__(
            label=label,
            emoji=emoji,
            style=discord.ButtonStyle.secondary,
            custom_id=f"dank_design:sep_examples_page:{page}",
            row=row,
        )
        self.page = int(page)

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not await _require_design_permission(interaction):
            return

        guild = interaction.guild
        assert guild is not None

        view = getattr(self, "view", None)
        scope = getattr(view, "scope", "channel")
        target_id = int(getattr(view, "target_id", 0))
        lock = _exact_lock_for_user(guild, int(interaction.user.id), scope, target_id)

        await interaction.response.edit_message(
            embed=_separator_gallery_embed(guild, scope=scope, target_id=target_id, lock=lock, page=self.page),
            view=SeparatorExamplesView(guild, scope=scope, target_id=target_id, lock=lock, page=self.page),
        )


class SeparatorExamplesBackButton(discord.ui.Button):
    def __init__(self, *, row: int) -> None:
        super().__init__(
            label="Back to Exact Format",
            emoji="⬅️",
            style=discord.ButtonStyle.secondary,
            custom_id="dank_design:sep_examples_back",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not await _require_design_permission(interaction):
            return

        guild = interaction.guild
        assert guild is not None

        view = getattr(self, "view", None)
        scope = getattr(view, "scope", "channel")
        target_id = int(getattr(view, "target_id", 0))
        lock = _exact_lock_for_user(guild, int(interaction.user.id), scope, target_id)

        await interaction.response.edit_message(
            embed=_exact_format_embed(guild, scope=scope, target_id=target_id, lock=lock),
            view=ExactFormatEditorViewFactory(guild, scope, target_id, lock),
        )


class SeparatorExamplesView(discord.ui.View):
    def __init__(self, guild: discord.Guild, *, scope: str, target_id: int, lock: Mapping[str, Any], page: int = 0) -> None:
        super().__init__(timeout=900)
        self.scope = scope
        self.target_id = int(target_id)

        separators = list(studio.SEPARATOR_LIBRARY)
        total_pages = max(1, (len(separators) + SEP_EXAMPLE_PAGE_SIZE - 1) // SEP_EXAMPLE_PAGE_SIZE)
        page = max(0, min(int(page), total_pages - 1))
        chunk = separators[page * SEP_EXAMPLE_PAGE_SIZE:(page + 1) * SEP_EXAMPLE_PAGE_SIZE]

        for offset, spec in enumerate(chunk):
            self.add_item(SeparatorExamplePickButton(spec.id, label=spec.label, display_index=offset + 1, row=offset // 2))

        nav_row = 4
        if page > 0:
            self.add_item(SeparatorExamplesPageButton(page - 1, label="Prev", emoji="⬅️", row=nav_row))
        if page < total_pages - 1:
            self.add_item(SeparatorExamplesPageButton(page + 1, label="Next", emoji="➡️", row=nav_row))
        self.add_item(SeparatorExamplesBackButton(row=nav_row))


async def _save_exact_and_preview(interaction: discord.Interaction, *, scope: str, target_id: int) -> None:
    if not await _require_design_permission(interaction):
        return

    guild = interaction.guild
    assert guild is not None

    await interaction.response.defer(ephemeral=True, thinking=True)

    await _save_exact_lock(interaction, scope=scope, target_id=int(target_id))

    options = await _load_design_options(int(guild.id))
    repair_options = dict(options)

    all_items = await build_design_plan(guild, repair_options)

    if scope == "category":
        items = _filter_plan_for_category(all_items, int(target_id))
        title = "👁️ Category Format Preview"
    else:
        items = _filter_plan_for_channel(all_items, int(target_id))
        title = "👁️ Channel Format Preview"

    key = _key(int(guild.id), int(interaction.user.id))
    _PENDING[key] = {
        "created_at": time.time(),
        "items": items,
        "options": dict(repair_options),
        "mode": f"{scope}_exact_format",
    }

    has_blockers = any(item.get("status") == "failed" for item in items)
    has_changes = any(item.get("status") == "changed" for item in items)

    await interaction.edit_original_response(
        embed=_preview_embed(guild, items, title=title),
        view=DesignPreviewView(can_apply=not has_blockers and has_changes),
    )



class ExactFormatEditorView(discord.ui.View):
    def __init__(self, *, scope: str, target_id: int) -> None:
        super().__init__(timeout=900)
        self.scope = scope
        self.target_id = int(target_id)

    async def _ensure_selects(self, interaction: discord.Interaction) -> None:
        # Not used directly. Selects are built in factory below.
        pass

    @discord.ui.button(label="Layout Examples", emoji="🧩", style=discord.ButtonStyle.primary, custom_id="dank_design:exact_layout_examples", row=4)
    async def layout_examples(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        key = _format_editor_key(int(guild.id), int(interaction.user.id), self.scope, self.target_id)
        lock = dict(_FORMAT_EDITOR_DRAFTS.get(key) or {})
        if not lock:
            options = await _load_design_options(int(guild.id))
            lock = _initial_editor_lock(options, scope=self.scope, target_id=self.target_id, guild=guild)
            _FORMAT_EDITOR_DRAFTS[key] = lock
        await interaction.response.edit_message(
            embed=_separator_gallery_embed(guild, scope=self.scope, target_id=self.target_id, lock=lock, page=0),
            view=SeparatorExamplesView(guild, scope=self.scope, target_id=self.target_id, lock=lock, page=0),
        )

    @discord.ui.button(label="Save & Preview", emoji="👁️", style=discord.ButtonStyle.primary, custom_id="dank_design:exact_save_preview", row=4)
    async def save_and_preview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _save_exact_and_preview(interaction, scope=self.scope, target_id=self.target_id)


    @discord.ui.button(label="Server Style", emoji="🧭", style=discord.ButtonStyle.secondary, custom_id="dank_design:exact_use_majority", row=4)
    async def use_server_style(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None

        options = await _load_design_options(int(guild.id))
        current = _live_majority_exact_lock(guild, options, scope=self.scope, target_id=self.target_id)
        if not current:
            return await interaction.response.send_message(
                "I could not detect a clear server style yet. Use Save & Preview before applying.",
                ephemeral=True,
            )

        key = _format_editor_key(int(guild.id), int(interaction.user.id), self.scope, self.target_id)
        _FORMAT_EDITOR_DRAFTS[key] = current

        await interaction.response.edit_message(
            embed=_exact_format_embed(guild, scope=self.scope, target_id=self.target_id, lock=current),
            view=ExactFormatEditorViewFactory(guild, self.scope, self.target_id, current),
        )

    @discord.ui.button(label="Emoji", emoji="😀", style=discord.ButtonStyle.secondary, custom_id="dank_design:exact_emoji", row=4)
    async def set_emoji(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        await interaction.response.send_modal(CustomEmojiModal(scope=self.scope, target_id=self.target_id))

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="dank_design:exact_back", row=4)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        if self.scope == "category":
            category = guild.get_channel(self.target_id)
            if isinstance(category, discord.CategoryChannel):
                await interaction.response.edit_message(embed=_category_action_embed(category), view=CategoryEditorActionView(self.target_id))
            else:
                await interaction.response.edit_message(embed=_category_editor_embed(guild, page=0), view=CategoryEditorPickerView(guild, page=0))
        else:
            channel = guild.get_channel(self.target_id)
            if channel is not None:
                await interaction.response.edit_message(embed=_channel_action_embed(channel), view=ChannelEditorActionView(self.target_id))
            else:
                await interaction.response.edit_message(embed=_channel_editor_embed(guild, page=0), view=ChannelEditorPickerView(guild, page=0))


def ExactFormatEditorViewFactory(guild: discord.Guild, scope: str, target_id: int, lock: Mapping[str, Any]) -> ExactFormatEditorView:
    view = ExactFormatEditorView(scope=scope, target_id=target_id)
    # Insert selects before buttons. Discord allows max 5 rows; four selects + row-4 buttons.
    view.add_item(ExactFontSelect(scope, target_id, _safe_str(lock.get("font"), "normal")))
    view.add_item(ExactSeparatorSelect(scope, target_id, _safe_str(lock.get("separator_id"), "bar_full")))
    view.add_item(ExactFrameSelect(scope, target_id, _safe_str(lock.get("category_frame_id"), "line")))
    view.add_item(ExactStrengthSelect(scope, target_id, _safe_int(lock.get("strength"), 4)))
    return view



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
    for channel in list(getattr(category, "channels", []) or []):
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



def _channel_editor_groups(guild: discord.Guild) -> list[dict[str, Any]]:
    """Return Channel Editor pages grouped by category.

    Each page is one category and a chunk of channels inside it. This keeps the
    Channel Editor from feeling like a random flat list.
    """

    groups: list[dict[str, Any]] = []
    seen: set[int] = set()

    for category in list(getattr(guild, "categories", []) or []):
        category_id = _safe_int(getattr(category, "id", 0), 0)
        children = [
            channel
            for channel in list(getattr(category, "channels", []) or [])
            if _kind(channel) != "other"
        ]

        chunks = [children[i:i + EDITOR_PAGE_SIZE] for i in range(0, len(children), EDITOR_PAGE_SIZE)] or [[]]
        for part_index, chunk in enumerate(chunks, start=1):
            groups.append({
                "category": category,
                "category_id": category_id,
                "channels": chunk,
                "part": part_index,
                "parts": len(chunks),
                "label": _safe_str(getattr(category, "name", "Category"), "Category"),
            })
            seen.add(category_id)
            for channel in chunk:
                cid = _safe_int(getattr(channel, "id", 0), 0)
                if cid > 0:
                    seen.add(cid)

    uncategorized = []
    for channel in list(getattr(guild, "channels", []) or []):
        cid = _safe_int(getattr(channel, "id", 0), 0)
        if cid <= 0 or cid in seen:
            continue
        if _kind(channel) in {"category", "other"}:
            continue
        if getattr(channel, "category", None) is not None:
            continue
        uncategorized.append(channel)
        seen.add(cid)

    for part_index, start in enumerate(range(0, len(uncategorized), EDITOR_PAGE_SIZE), start=1):
        chunk = uncategorized[start:start + EDITOR_PAGE_SIZE]
        groups.append({
            "category": None,
            "category_id": None,
            "channels": chunk,
            "part": part_index,
            "parts": max(1, (len(uncategorized) + EDITOR_PAGE_SIZE - 1) // EDITOR_PAGE_SIZE),
            "label": "No Category",
        })

    return groups or [{
        "category": None,
        "category_id": None,
        "channels": [],
        "part": 1,
        "parts": 1,
        "label": "No Category",
    }]



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
    repair_options = dict(options)

    if mode in {"category_editor", "channel_editor"}:
        repair_options["__use_live_majority_layout"] = True

    all_items = await build_design_plan(guild, repair_options)

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
        "options": dict(repair_options),
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
            "Pick one category below.\n\n"
            "Choose one category to review. You can preview repairs, rename it, or edit channels inside."
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
        embed.add_field(name=f"Categories page {page + 1}/{total_pages}", value="\n".join(lines)[:1024], inline=False)
    embed.set_footer(text="Step 1: pick a category. Step 2: preview, rename, or edit channels inside.")
    return _clean_design_embed(embed)


def _channel_editor_embed(guild: discord.Guild, *, page: int, category_id: int | None = None) -> discord.Embed:
    category = guild.get_channel(int(category_id)) if category_id is not None else None

    if category_id is not None and isinstance(category, discord.CategoryChannel):
        source = _category_channels(guild, int(category_id))
        total_pages = max(1, (len(source) + EDITOR_PAGE_SIZE - 1) // EDITOR_PAGE_SIZE)
        page = max(0, min(page, total_pages - 1))
        start = page * EDITOR_PAGE_SIZE
        chunk = source[start:start + EDITOR_PAGE_SIZE]
        category_label = _safe_str(getattr(category, "name", "Category"), "Category")
        part_text = f" · channels {page + 1}/{total_pages}" if total_pages > 1 else ""
    else:
        groups = _channel_editor_groups(guild)
        total_pages = max(1, len(groups))
        page = max(0, min(page, total_pages - 1))
        group = groups[page]
        category = group.get("category")
        category_id = _safe_int(group.get("category_id"), 0) or None
        chunk = list(group.get("channels") or [])
        category_label = _safe_str(group.get("label"), "No Category")
        part = _safe_int(group.get("part"), 1)
        parts = _safe_int(group.get("parts"), 1)
        part_text = f" · part {part}/{parts}" if parts > 1 else ""

    embed = discord.Embed(
        title=f"#️⃣ Channel Editor · {category_label}",
        description=(
            "This page shows one category and the channels inside it.\n\n"
            "Pick a channel to preview repairs, rename it, or edit its rule."
        ),
        color=discord.Color.blurple(),
    )

    if category is not None:
        embed.add_field(
            name="Category on this page",
            value=f"🗂️ `{_safe_str(getattr(category, 'name', 'Category'))}`\nUse **Edit This Category** for the category name/rules.",
            inline=False,
        )
    else:
        embed.add_field(
            name="Category on this page",
            value="No category. These are uncategorized channels.",
            inline=False,
        )

    if not chunk:
        embed.add_field(name=f"Channels page {page + 1}/{total_pages}{part_text}", value="No child channels found here.", inline=False)
    else:
        lines = []
        for index, channel in enumerate(chunk, start=1):
            lines.append(f"**{index}.** `{_channel_display_line(channel)}`")
        embed.add_field(name=f"Channels page {page + 1}/{total_pages}{part_text}", value="\n".join(lines)[:1024], inline=False)

    embed.set_footer(text="Each page is grouped by category. Use Category Editor for the full category list.")
    return _clean_design_embed(embed)



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


class EditCategoryFromChannelEditorButton(discord.ui.Button):
    def __init__(self, category_id: int, *, row: int = 4) -> None:
        super().__init__(
            label="Edit This Category",
            emoji="🗂️",
            style=discord.ButtonStyle.secondary,
            custom_id=f"dank_design:channel_editor_edit_category:{int(category_id)}",
            row=row,
        )
        self.category_id = int(category_id)

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

        active_category_id: int | None = int(category_id) if category_id is not None else None

        if category_id is not None:
            source = _category_channels(guild, int(category_id))
            total_pages = max(1, (len(source) + EDITOR_PAGE_SIZE - 1) // EDITOR_PAGE_SIZE)
            page = max(0, min(page, total_pages - 1))
            start = page * EDITOR_PAGE_SIZE
            chunk = source[start:start + EDITOR_PAGE_SIZE]
            active_category_id = int(category_id)
        else:
            groups = _channel_editor_groups(guild)
            total_pages = max(1, len(groups))
            page = max(0, min(page, total_pages - 1))
            group = groups[page]
            chunk = list(group.get("channels") or [])
            group_category_id = _safe_int(group.get("category_id"), 0)
            active_category_id = group_category_id if group_category_id > 0 else None

        for offset, channel in enumerate(chunk):
            self.add_item(ChannelPickButton(channel, display_index=offset + 1, row=offset // 2, category_id=active_category_id))

        nav_row = 4

        if active_category_id is not None:
            self.add_item(EditCategoryFromChannelEditorButton(active_category_id, row=nav_row))

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


class DirectRenameModal(discord.ui.Modal):
    def __init__(self, *, target_id: int, scope: str, current_name: str, category_id: int | None = None) -> None:
        super().__init__(title=f"Rename {scope.title()}")
        self.target_id = int(target_id)
        self.scope = str(scope)
        self.category_id = int(category_id) if category_id is not None else None
        self.new_name = discord.ui.TextInput(
            label="New channel/category name",
            placeholder="Example: staff-commands",
            default=_short_label(current_name, 90),
            min_length=1,
            max_length=100,
            required=True,
        )
        self.add_item(self.new_name)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _require_design_permission(interaction):
            return

        guild = interaction.guild
        assert guild is not None

        channel = guild.get_channel(self.target_id)
        if channel is None:
            return await interaction.response.send_message("That item no longer exists.", ephemeral=True)

        old_name = _safe_str(getattr(channel, "name", ""), "unknown")
        new_name = _safe_str(self.new_name.value, "")

        if not new_name:
            return await interaction.response.send_message("Name cannot be blank.", ephemeral=True)

        try:
            await channel.edit(
                name=new_name,
                reason=f"Dank Design direct rename by {interaction.user} ({interaction.user.id})",
            )
        except discord.Forbidden:
            return await interaction.response.send_message(
                "❌ I cannot rename that. I need **Manage Channels**, and my role must be high enough.",
                ephemeral=True,
            )
        except discord.HTTPException as exc:
            return await interaction.response.send_message(
                f"❌ Discord rejected that rename: `{exc}`",
                ephemeral=True,
            )

        refreshed = guild.get_channel(self.target_id) or channel

        if self.scope == "category" and isinstance(refreshed, discord.CategoryChannel):
            embed = _category_action_embed(refreshed)
            view = CategoryEditorActionView(self.target_id)
        else:
            embed = _channel_action_embed(refreshed)
            view = ChannelEditorActionView(self.target_id, category_id=self.category_id)

        embed.title = "✅ Name Updated"
        embed.add_field(name="Renamed", value=f"`{old_name}`\n→ `{new_name}`", inline=False)

        try:
            await interaction.response.edit_message(embed=embed, view=view)
        except Exception:
            await interaction.response.send_message(f"✅ Renamed `{old_name}` → `{new_name}`", ephemeral=True)


def _category_action_embed(category: discord.CategoryChannel) -> discord.Embed:
    child_count = len(list(getattr(category, "channels", []) or []))
    embed = discord.Embed(
        title="🗂️ Category Design",
        description=(
            "Start with **Preview Repairs**. Nothing changes until the next screen shows an Apply button.\n\n"
            "Use **Rename** when you want to directly change the category's actual Discord name."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Selected category",
        value=(
            f"Name: `{_safe_str(getattr(category, 'name', 'Category'))}`\n"
            f"Children: **{child_count}** channel(s)\n"
            f"ID: `{getattr(category, 'id', '')}`"
        ),
        inline=False,
    )
    embed.add_field(
        name="Recommended next step",
        value="Press **Preview Repairs** to see exactly what would change before anything is renamed.",
        inline=False,
    )
    embed.add_field(
        name="Advanced options",
        value=(
            "**Exact Format** = choose font/separator/frame manually.\n"
            "**Save Category Rule** = remember a special rule for this category.\n"
            "**Protection Settings** = control whether this category is skipped."
        ),
        inline=False,
    )
    embed.set_footer(text="Preview first • Apply later • Rename is direct")
    return _clean_design_embed(embed)

def _channel_action_embed(channel: discord.abc.GuildChannel) -> discord.Embed:
    kind = _kind(channel)
    mention = getattr(channel, "mention", f"`{getattr(channel, 'id', '')}`")
    embed = discord.Embed(
        title="#️⃣ Channel Design",
        description=(
            "Start with **Preview Repairs**. Nothing changes until the next screen shows an Apply button.\n\n"
            "Use **Rename** when you want to directly change this channel's actual Discord name."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Selected item",
        value=(
            f"Name: `{_safe_str(getattr(channel, 'name', 'Channel'))}`\n"
            f"Kind: **{kind}**\n"
            f"Channel: {mention}\n"
            f"ID: `{getattr(channel, 'id', '')}`"
        ),
        inline=False,
    )
    embed.add_field(
        name="Recommended next step",
        value="Press **Preview Repairs** to check this item only.",
        inline=False,
    )
    embed.add_field(
        name="Advanced options",
        value=(
            "**Exact Format** = choose this item's exact look.\n"
            "**Save Channel Rule** = remember a special rule for this item.\n"
            "**Protection Settings** = control whether this item is skipped."
        ),
        inline=False,
    )
    embed.set_footer(text="Preview first • Apply later • Rename is direct")
    return _clean_design_embed(embed)

class CategoryEditorActionView(discord.ui.View):
    def __init__(self, category_id: int) -> None:
        super().__init__(timeout=900)
        self.category_id = int(category_id)

    @discord.ui.button(label="Preview Repairs", emoji="👁️", style=discord.ButtonStyle.success, custom_id="dank_design:category_preview_scope", row=0)
    async def preview_category(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _preview_scope(
            interaction,
            scope_title="👁️ Category Repair Preview",
            mode="category_editor",
            category_id=self.category_id,
        )

    @discord.ui.button(label="Rename", emoji="✏️", style=discord.ButtonStyle.primary, custom_id="dank_design:category_rename", row=0)
    async def rename_category(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        category = guild.get_channel(self.category_id)
        if not isinstance(category, discord.CategoryChannel):
            return await interaction.response.send_message("That category no longer exists.", ephemeral=True)
        await interaction.response.send_modal(
            DirectRenameModal(
                target_id=self.category_id,
                scope="category",
                current_name=getattr(category, "name", ""),
            )
        )

    @discord.ui.button(label="Edit Channels Inside", emoji="#️⃣", style=discord.ButtonStyle.primary, custom_id="dank_design:category_children", row=1)
    async def edit_children(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.edit_message(
            embed=_channel_editor_embed(guild, page=0, category_id=self.category_id),
            view=ChannelEditorPickerView(guild, page=0, category_id=self.category_id),
        )

    @discord.ui.button(label="Exact Format", emoji="🎛️", style=discord.ButtonStyle.secondary, custom_id="dank_design:category_exact_format", row=2)
    async def edit_exact_format(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_exact_format_editor(interaction, scope="category", target_id=self.category_id)

    @discord.ui.button(label="Save Category Rule", emoji="🔒", style=discord.ButtonStyle.secondary, custom_id="dank_design:category_lock_here", row=2)
    async def lock_here(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        try:
            category = guild.get_channel(self.category_id)
            if not isinstance(category, discord.CategoryChannel):
                return await interaction.response.send_message("That category no longer exists.", ephemeral=True)
            options = await _save_category_lock(interaction, self.category_id)
            counts = _lock_count(options)
            embed = _category_action_embed(category)
            embed.title = "✅ Category Rule Saved"
            embed.add_field(
                name="Saved rules",
                value=f"Global: {counts['global']} • Categories: {counts['categories']} • Channels: {counts['channels']}",
                inline=False,
            )
            await interaction.response.edit_message(embed=embed, view=CategoryEditorActionView(self.category_id))
        except Exception as exc:
            if interaction.response.is_done():
                await interaction.followup.send(f"❌ Could not save category rule: `{type(exc).__name__}: {_safe_str(exc)[:120]}`", ephemeral=True)
            else:
                await interaction.response.send_message(f"❌ Could not save category rule: `{type(exc).__name__}: {_safe_str(exc)[:120]}`", ephemeral=True)

    @discord.ui.button(label="Protection Settings", emoji="🛡️", style=discord.ButtonStyle.secondary, custom_id="dank_design:category_protection_mode", row=3)
    async def protection_mode(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_protection_mode_editor(interaction, channel_id=self.category_id)

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="dank_design:category_action_back", row=4)
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

    @discord.ui.button(label="Preview Repairs", emoji="👁️", style=discord.ButtonStyle.success, custom_id="dank_design:channel_preview_scope", row=0)
    async def preview_channel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _preview_scope(
            interaction,
            scope_title="👁️ Channel Repair Preview",
            mode="channel_editor",
            channel_id=self.channel_id,
        )

    @discord.ui.button(label="Rename", emoji="✏️", style=discord.ButtonStyle.primary, custom_id="dank_design:channel_rename", row=0)
    async def rename_channel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        channel = guild.get_channel(self.channel_id)
        if channel is None:
            return await interaction.response.send_message("That channel no longer exists.", ephemeral=True)
        await interaction.response.send_modal(
            DirectRenameModal(
                target_id=self.channel_id,
                scope="channel",
                current_name=getattr(channel, "name", ""),
                category_id=self.category_id,
            )
        )

    @discord.ui.button(label="Exact Format", emoji="🎛️", style=discord.ButtonStyle.secondary, custom_id="dank_design:channel_exact_format", row=1)
    async def edit_exact_format(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_exact_format_editor(interaction, scope="channel", target_id=self.channel_id)

    @discord.ui.button(label="Save Channel Rule", emoji="🔒", style=discord.ButtonStyle.secondary, custom_id="dank_design:channel_lock_here", row=1)
    async def lock_here(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        try:
            channel = guild.get_channel(self.channel_id)
            if channel is None:
                return await interaction.response.send_message("That channel no longer exists.", ephemeral=True)
            options = await _save_channel_lock(interaction, self.channel_id)
            counts = _lock_count(options)
            embed = _channel_action_embed(channel)
            embed.title = "✅ Channel Rule Saved"
            embed.add_field(
                name="Saved rules",
                value=f"Global: {counts['global']} • Categories: {counts['categories']} • Channels: {counts['channels']}",
                inline=False,
            )
            await interaction.response.edit_message(embed=embed, view=ChannelEditorActionView(self.channel_id, category_id=self.category_id))
        except Exception as exc:
            if interaction.response.is_done():
                await interaction.followup.send(f"❌ Could not save channel rule: `{type(exc).__name__}: {_safe_str(exc)[:120]}`", ephemeral=True)
            else:
                await interaction.response.send_message(f"❌ Could not save channel rule: `{type(exc).__name__}: {_safe_str(exc)[:120]}`", ephemeral=True)

    @discord.ui.button(label="Protection Settings", emoji="🛡️", style=discord.ButtonStyle.secondary, custom_id="dank_design:channel_protection_mode", row=2)
    async def protection_mode(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_protection_mode_editor(interaction, channel_id=self.channel_id)

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



# ---------------------------------------------------------------------------
# Design Doctor
# ---------------------------------------------------------------------------

def _guild_categories(guild: discord.Guild) -> list[discord.CategoryChannel]:
    return list(getattr(guild, "categories", []) or [])


def _guild_category_ids(guild: discord.Guild) -> set[str]:
    return {str(int(getattr(category, "id", 0))) for category in _guild_categories(guild) if _safe_int(getattr(category, "id", 0), 0) > 0}


def _doctor_missing_category_locks(guild: discord.Guild, options: Mapping[str, Any]) -> list[str]:
    if "_mapping_dict" not in globals():
        return []
    category_locks = _mapping_dict(options.get("category_format_locks"))
    existing = _guild_category_ids(guild)
    missing: list[str] = []
    for category in _guild_categories(guild):
        cid = str(int(category.id))
        if cid not in existing:
            continue
        if cid not in category_locks:
            missing.append(f"• `{_safe_str(getattr(category, 'name', 'Category'))}`")
    return missing


def _doctor_stale_lock_lines(guild: discord.Guild, options: Mapping[str, Any]) -> list[str]:
    if "_mapping_dict" not in globals():
        return []
    lines: list[str] = []
    category_locks = _mapping_dict(options.get("category_format_locks"))
    channel_locks = _mapping_dict(options.get("channel_format_locks"))

    for cid in list(category_locks.keys()):
        if guild.get_channel(_safe_int(cid, 0)) is None:
            lines.append(f"• stale category lock `{cid}`")
    for cid in list(channel_locks.keys()):
        if guild.get_channel(_safe_int(cid, 0)) is None:
            lines.append(f"• stale channel lock `{cid}`")
    return lines


def _doctor_top_changed(items: list[dict[str, Any]], *, limit: int = 8) -> list[str]:
    lines: list[str] = []
    for item in items:
        if item.get("status") != "changed":
            continue
        scope = _safe_str(item.get("format_lock_scope"), "auto")
        lines.append(f"• `{item.get('before')}` → `{item.get('after')}`"[:220])
        if len(lines) >= limit:
            break
    return lines


def _doctor_permission_blockers(items: list[dict[str, Any]], *, limit: int = 8) -> list[str]:
    lines: list[str] = []
    for item in items:
        if item.get("status") != "failed":
            continue
        blockers = list(item.get("blockers") or [])
        reason = _safe_str(blockers[0] if blockers else "Unknown blocker")
        lines.append(f"• `{item.get('before')}` — {reason}"[:220])
        if len(lines) >= limit:
            break
    return lines


def _doctor_scope_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        scope = _safe_str(item.get("format_lock_scope"), "auto")
        counts[scope] = counts.get(scope, 0) + 1
    return counts


def _doctor_embed(guild: discord.Guild, options: Mapping[str, Any], items: list[dict[str, Any]]) -> discord.Embed:
    summary = studio.summarize_plan(items)
    score = studio.design_score(items)
    duplicates = studio.detect_duplicate_outputs(items)
    counts = _lock_count(options) if "_lock_count" in globals() else {"global": 0, "categories": 0, "channels": 0}
    scope_counts = _doctor_scope_counts(items)

    missing_locks = _doctor_missing_category_locks(guild, options)
    stale_locks = _doctor_stale_lock_lines(guild, options)
    changed = _doctor_top_changed(items)
    blockers = _doctor_permission_blockers(items)

    health_points = 100
    health_points -= min(30, summary.get("failed", 0) * 10)
    health_points -= min(20, len(duplicates) * 10)
    health_points -= min(20, summary.get("changed", 0))
    health_points -= 10 if stale_locks else 0
    health_points -= 10 if counts.get("global", 0) == 0 and counts.get("categories", 0) == 0 else 0
    health_points = max(0, min(100, health_points))

    if summary.get("failed", 0) or duplicates:
        status = "Needs fixes before apply"
        color = discord.Color.orange()
    elif summary.get("changed", 0):
        status = "Ready to repair drift"
        color = discord.Color.blurple()
    else:
        status = "Looks consistent"
        color = discord.Color.green()

    embed = discord.Embed(
        title="🩺 Server Design Doctor",
        description=(
            f"Design health: **{health_points}/100** · **{status}**\n\n"
            "This is a read-only audit. Nothing has been renamed."
        ),
        color=color,
    )

    embed.add_field(
        name="Plan health",
        value=(
            f"Already matching: **{summary.get('unchanged', 0)}**\n"
            f"Needs repair: **{summary.get('changed', 0)}**\n"
            f"Protected safe skips: **{summary.get('protected', 0)}**\n"
            f"Must fix: **{summary.get('failed', 0)}**\n"
            f"Notes: **{summary.get('warnings', 0)}**"
        ),
        inline=True,
    )
    embed.add_field(
        name="Saved locks",
        value=(
            f"Global: **{'On' if counts.get('global') else 'Off'}**\n"
            f"Category locks: **{counts.get('categories', 0)}**\n"
            f"Channel overrides: **{counts.get('channels', 0)}**"
        ),
        inline=True,
    )
    embed.add_field(
        name="Design score",
        value=(
            f"Readability: **{score['readability']}/100**\n"
            f"Mobile: **{score['mobile_fit']}/100**\n"
            f"Clutter: **{score['clutter_risk']}**\n"
            f"Accessibility: **{score['accessibility']}**"
        ),
        inline=True,
    )

    scope_line = " • ".join(f"{k}: {v}" for k, v in sorted(scope_counts.items())) or "No scoped items."
    embed.add_field(name="Rule coverage", value=scope_line[:1024], inline=False)

    if missing_locks:
        embed.add_field(
            name="Unlocked categories",
            value=("\n".join(missing_locks[:8]) + (f"\n…and {len(missing_locks) - 8} more" if len(missing_locks) > 8 else ""))[:1024],
            inline=False,
        )

    if changed:
        embed.add_field(name="Top drift to repair", value="\n".join(changed)[:1024], inline=False)

    if blockers:
        embed.add_field(name="Must fix first", value="\n".join(blockers)[:1024], inline=False)

    if duplicates:
        embed.add_field(name="Duplicate output risk", value="\n".join(f"• {x}" for x in duplicates[:5])[:1024], inline=False)

    if stale_locks:
        embed.add_field(name="Stale saved locks", value="\n".join(stale_locks[:8])[:1024], inline=False)

    if not changed and not blockers and not duplicates and not stale_locks:
        embed.add_field(
            name="Next step",
            value="Everything looks aligned. Use Category/Channel Editor only when you want to intentionally change the design.",
            inline=False,
        )
    else:
        embed.add_field(
            name="Recommended next step",
            value="Use **Find & Fix Inconsistencies** for drift, or **Category/Channel Editor** to lock missing categories.",
            inline=False,
        )

    embed.set_footer(text="Doctor checks saved design rules, locks, drift, duplicates, protected skips, and edit blockers.")
    return _clean_design_embed(embed)


class DesignDoctorButton(discord.ui.Button):
    def __init__(self, *, row: int = 4) -> None:
        super().__init__(
            label="Design Doctor",
            emoji="🩺",
            style=discord.ButtonStyle.secondary,
            custom_id="dank_design:doctor",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None

        await interaction.response.defer(ephemeral=True, thinking=True)
        options = await _load_design_options(int(guild.id))
        items = await build_design_plan(guild, options)
        await interaction.edit_original_response(embed=_doctor_embed(guild, options, items), view=DesignDoctorView())


class DesignDoctorView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Find & Fix Inconsistencies", emoji="🧭", style=discord.ButtonStyle.success, custom_id="dank_design:doctor_consistency", row=0)
    async def consistency(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
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

        if "_consistency_embed" in globals():
            embed = _consistency_embed(guild, items, options)
        else:
            embed = _preview_embed(guild, items, title="🧭 Server Design Consistency Check")

        await interaction.edit_original_response(
            embed=embed,
            view=DesignPreviewView(can_apply=not has_blockers and has_changes),
        )

    @discord.ui.button(label="Category Editor", emoji="🗂️", style=discord.ButtonStyle.primary, custom_id="dank_design:doctor_category", row=1)
    async def category_editor(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        if "CategoryEditorPickerView" not in globals():
            return await interaction.response.send_message("Category Editor is not installed yet.", ephemeral=True)
        await interaction.response.edit_message(embed=_category_editor_embed(guild, page=0), view=CategoryEditorPickerView(guild, page=0))

    @discord.ui.button(label="Channel Editor", emoji="#️⃣", style=discord.ButtonStyle.primary, custom_id="dank_design:doctor_channel", row=1)
    async def channel_editor(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        if "ChannelEditorPickerView" not in globals():
            return await interaction.response.send_message("Channel Editor is not installed yet.", ephemeral=True)
        await interaction.response.edit_message(embed=_channel_editor_embed(guild, page=0), view=ChannelEditorPickerView(guild, page=0))

    @discord.ui.button(label="Back to Design Studio", emoji="🎨", style=discord.ButtonStyle.secondary, custom_id="dank_design:doctor_back", row=4)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        assert interaction.guild is not None
        options = await _load_design_options(int(interaction.guild.id))
        await interaction.response.edit_message(embed=_home_embed(interaction.guild, options), view=DesignHomeView(options))



# ---------------------------------------------------------------------------
# Format Lock Manager
# ---------------------------------------------------------------------------

LOCK_MANAGER_PAGE_SIZE = 8


def _lock_manager_rows(guild: discord.Guild, options: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    global_lock = _mapping_dict(options.get("format_lock_global")) if "_mapping_dict" in globals() else {}
    if global_lock.get("enabled"):
        rows.append({
            "scope": "global",
            "target_id": "0",
            "label": "Global default format",
            "exists": True,
            "font": _safe_str(global_lock.get("font"), "normal"),
            "separator_id": _safe_str(global_lock.get("separator_id"), ""),
            "strength": _safe_int(global_lock.get("strength"), 4),
        })

    category_locks = _mapping_dict(options.get("category_format_locks")) if "_mapping_dict" in globals() else {}
    for raw_id, lock in sorted(category_locks.items(), key=lambda pair: str(pair[0])):
        cid = _safe_int(raw_id, 0)
        channel = guild.get_channel(cid)
        label = _safe_str(getattr(channel, "name", ""), f"Deleted category {cid}")
        lock_map = _mapping_dict(lock)
        rows.append({
            "scope": "category",
            "target_id": str(cid),
            "label": label,
            "exists": isinstance(channel, discord.CategoryChannel),
            "font": _safe_str(lock_map.get("font"), "normal"),
            "separator_id": _safe_str(lock_map.get("separator_id"), ""),
            "strength": _safe_int(lock_map.get("strength"), 4),
        })

    channel_locks = _mapping_dict(options.get("channel_format_locks")) if "_mapping_dict" in globals() else {}
    for raw_id, lock in sorted(channel_locks.items(), key=lambda pair: str(pair[0])):
        cid = _safe_int(raw_id, 0)
        channel = guild.get_channel(cid)
        label = _safe_str(getattr(channel, "name", ""), f"Deleted channel {cid}")
        lock_map = _mapping_dict(lock)
        rows.append({
            "scope": "channel",
            "target_id": str(cid),
            "label": label,
            "exists": channel is not None,
            "font": _safe_str(lock_map.get("font"), "normal"),
            "separator_id": _safe_str(lock_map.get("separator_id"), ""),
            "strength": _safe_int(lock_map.get("strength"), 4),
        })

    return rows


def _format_lock_manager_embed(guild: discord.Guild, options: Mapping[str, Any], *, page: int = 0) -> discord.Embed:
    rows = _lock_manager_rows(guild, options)
    total_pages = max(1, (len(rows) + LOCK_MANAGER_PAGE_SIZE - 1) // LOCK_MANAGER_PAGE_SIZE)
    page = max(0, min(int(page), total_pages - 1))
    start = page * LOCK_MANAGER_PAGE_SIZE
    chunk = rows[start:start + LOCK_MANAGER_PAGE_SIZE]

    stale_count = sum(1 for row in rows if not row.get("exists"))
    embed = discord.Embed(
        title="🔐 Format Lock Manager",
        description=(
            "Review saved global/category/channel locks, remove individual overrides, or clean stale locks."
        ),
        color=discord.Color.blurple() if not stale_count else discord.Color.orange(),
    )

    if not rows:
        embed.add_field(
            name="Saved locks",
            value="No format locks saved yet. Use **Category Editor** or **Channel Editor** to create locks.",
            inline=False,
        )
    else:
        lines: list[str] = []
        for index, row in enumerate(chunk, start=1):
            exists = "✅" if row.get("exists") else "⚠️"
            scope = _safe_str(row.get("scope"), "lock").title()
            label = _safe_str(row.get("label"), "Unknown")
            font = _safe_str(row.get("font"), "normal").replace("_", " ").title()
            sep = _safe_str(row.get("separator_id"), "none").replace("_", " ").title()
            strength = _safe_int(row.get("strength"), 4)
            lines.append(f"**{index}.** {exists} **{scope}** `{label}` · Font: `{font}` · Sep: `{sep}` · Strength: `{strength}`")
        embed.add_field(name=f"Locks page {page + 1}/{total_pages}", value="\n".join(lines)[:1024], inline=False)

    embed.add_field(
        name="Priority order",
        value="Protected item → Channel override → Category lock → Global lock → Auto theme",
        inline=False,
    )

    if stale_count:
        embed.add_field(
            name="Stale locks found",
            value=f"**{stale_count}** saved lock(s) point to deleted/missing channels or categories.",
            inline=False,
        )

    embed.set_footer(text="Use the numbered buttons to remove one lock, or clean stale locks only.")
    return _clean_design_embed(embed)


async def _remove_format_lock(interaction: discord.Interaction, *, scope: str, target_id: int) -> dict[str, Any]:
    guild = interaction.guild
    assert guild is not None

    options = await _load_design_options(int(guild.id))
    scope = _safe_str(scope).lower()

    if scope == "global":
        options["format_lock_global"] = {}
    elif scope == "category":
        locks = _mapping_dict(options.get("category_format_locks"))
        locks.pop(str(int(target_id)), None)
        options["category_format_locks"] = locks
    elif scope == "channel":
        locks = _mapping_dict(options.get("channel_format_locks"))
        locks.pop(str(int(target_id)), None)
        options["channel_format_locks"] = locks

    await _save_options(interaction, options)
    return options


async def _clean_stale_format_locks(interaction: discord.Interaction) -> tuple[dict[str, Any], int]:
    guild = interaction.guild
    assert guild is not None

    options = await _load_design_options(int(guild.id))
    removed = 0

    category_locks = _mapping_dict(options.get("category_format_locks"))
    for raw_id in list(category_locks.keys()):
        channel = guild.get_channel(_safe_int(raw_id, 0))
        if not isinstance(channel, discord.CategoryChannel):
            category_locks.pop(raw_id, None)
            removed += 1
    options["category_format_locks"] = category_locks

    channel_locks = _mapping_dict(options.get("channel_format_locks"))
    for raw_id in list(channel_locks.keys()):
        channel = guild.get_channel(_safe_int(raw_id, 0))
        if channel is None:
            channel_locks.pop(raw_id, None)
            removed += 1
    options["channel_format_locks"] = channel_locks

    await _save_options(interaction, options)
    return options, removed


class LockManagerButton(discord.ui.Button):
    def __init__(self, *, row: int = 4) -> None:
        super().__init__(
            label="Manage Locks",
            emoji="🔐",
            style=discord.ButtonStyle.secondary,
            custom_id="dank_design:manage_locks",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        options = await _load_design_options(int(guild.id))
        await interaction.response.edit_message(
            embed=_format_lock_manager_embed(guild, options, page=0),
            view=LockManagerView(guild, options, page=0),
        )


class LockRemoveButton(discord.ui.Button):
    def __init__(self, row_data: Mapping[str, Any], *, display_index: int, row: int) -> None:
        scope = _safe_str(row_data.get("scope"), "lock")
        label = _safe_str(row_data.get("label"), "Unknown")
        emoji = {"global": "🌐", "category": "🗂️", "channel": "#️⃣"}.get(scope, "🔒")
        super().__init__(
            label=f"Remove {display_index}. {_short_label(label, 46) if '_short_label' in globals() else label[:46]}",
            emoji=emoji,
            style=discord.ButtonStyle.danger if not row_data.get("exists") else discord.ButtonStyle.secondary,
            custom_id=f"dank_design:remove_lock:{scope}:{row_data.get('target_id')}",
            row=row,
        )
        self.scope = scope
        self.target_id = _safe_int(row_data.get("target_id"), 0)

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        options = await _remove_format_lock(interaction, scope=self.scope, target_id=self.target_id)
        embed = _format_lock_manager_embed(guild, options, page=0)
        embed.title = "🗑️ Format Lock Removed"
        await interaction.response.edit_message(embed=embed, view=LockManagerView(guild, options, page=0))


class LockManagerPageButton(discord.ui.Button):
    def __init__(self, page: int, *, label: str, emoji: str, row: int) -> None:
        super().__init__(
            label=label,
            emoji=emoji,
            style=discord.ButtonStyle.secondary,
            custom_id=f"dank_design:lock_manager_page:{page}",
            row=row,
        )
        self.page = int(page)

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        options = await _load_design_options(int(guild.id))
        await interaction.response.edit_message(
            embed=_format_lock_manager_embed(guild, options, page=self.page),
            view=LockManagerView(guild, options, page=self.page),
        )


class LockManagerView(discord.ui.View):
    def __init__(self, guild: discord.Guild, options: Mapping[str, Any], *, page: int = 0) -> None:
        super().__init__(timeout=900)

        rows = _lock_manager_rows(guild, options)
        total_pages = max(1, (len(rows) + LOCK_MANAGER_PAGE_SIZE - 1) // LOCK_MANAGER_PAGE_SIZE)
        page = max(0, min(int(page), total_pages - 1))
        start = page * LOCK_MANAGER_PAGE_SIZE
        chunk = rows[start:start + LOCK_MANAGER_PAGE_SIZE]

        for offset, row_data in enumerate(chunk):
            self.add_item(LockRemoveButton(row_data, display_index=offset + 1, row=offset // 2))

        nav_row = 4
        if page > 0:
            self.add_item(LockManagerPageButton(page - 1, label="Prev", emoji="⬅️", row=nav_row))
        if page < total_pages - 1:
            self.add_item(LockManagerPageButton(page + 1, label="Next", emoji="➡️", row=nav_row))
        self.add_item(CleanStaleLocksButton(row=nav_row))
        self.add_item(BackToLocksOrDesignButton(row=nav_row))


class CleanStaleLocksButton(discord.ui.Button):
    def __init__(self, *, row: int) -> None:
        super().__init__(
            label="Clean Stale",
            emoji="🧹",
            style=discord.ButtonStyle.secondary,
            custom_id="dank_design:clean_stale_locks",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        options, removed = await _clean_stale_format_locks(interaction)
        embed = _format_lock_manager_embed(guild, options, page=0)
        embed.title = "🧹 Stale Format Locks Cleaned"
        embed.description = f"Removed **{removed}** stale lock(s)."
        await interaction.response.edit_message(embed=embed, view=LockManagerView(guild, options, page=0))


class BackToLocksOrDesignButton(discord.ui.Button):
    def __init__(self, *, row: int) -> None:
        super().__init__(
            label="Back",
            emoji="⬅️",
            style=discord.ButtonStyle.secondary,
            custom_id="dank_design:locks_manager_back",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        options = await _load_design_options(int(guild.id))
        await interaction.response.edit_message(embed=_format_locks_embed(guild, options), view=FormatLocksView() if "FormatLocksView" in globals() else DesignHomeView(options))



# ---------------------------------------------------------------------------
# Protection Manager
# ---------------------------------------------------------------------------

PROTECTION_LABELS: dict[str, tuple[str, str]] = {
    "never": ("Never rename", "Fully protected. Bot will not rename it."),
    "emoji_only": ("Emoji only", "Allow emoji cleanup/suggestion only."),
    "separator_only": ("Separator only", "Allow emoji + separator/layout, no font."),
    "font_only": ("Font only", "Allow font styling without category frame."),
    "category_frame_only": ("Category frame only", "Allow category frame styling."),
    "full": ("Full styling", "Allow full design formatting."),
}


def _base_for_channel(channel: discord.abc.GuildChannel) -> str:
    try:
        parsed = studio.parse_channel_name(_safe_str(getattr(channel, "name", "")), kind="category" if isinstance(channel, discord.CategoryChannel) else "text")
        return studio.normalize_base_name(parsed.get("base_name") or getattr(channel, "name", ""))
    except Exception:
        return studio.normalize_base_name(_safe_str(getattr(channel, "name", "")))


def _protection_rules(options: Mapping[str, Any]) -> dict[str, str]:
    raw = options.get("protection_rules")
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        base = studio.normalize_base_name(_safe_str(key))
        mode = _safe_str(value).lower().replace("-", "_")
        if base and mode in PROTECTION_LABELS:
            out[base] = mode
    return out


def _protection_mode_label(mode: str) -> str:
    return PROTECTION_LABELS.get(mode, ("Unknown", ""))[0]


async def _save_protection_rule(interaction: discord.Interaction, *, base_name: str, mode: str | None) -> dict[str, Any]:
    guild = interaction.guild
    assert guild is not None

    options = await _load_design_options(int(guild.id))
    rules = _protection_rules(options)
    base = studio.normalize_base_name(base_name)

    if mode is None:
        rules.pop(base, None)
    else:
        clean = _safe_str(mode).lower().replace("-", "_")
        if clean not in PROTECTION_LABELS:
            clean = "never"
        rules[base] = clean

    options["protection_rules"] = rules
    await _save_options(interaction, options) if "_save_options" in globals() else await _save_design_options(int(guild.id), options)
    return options


def _protection_manager_embed(guild: discord.Guild, options: Mapping[str, Any]) -> discord.Embed:
    rules = _protection_rules(options)

    embed = discord.Embed(
        title="🛡️ Server Design Protection Manager",
        description=(
            "Control which ticket/log/system items are protected and which ones may be styled.\n\n"
            "Use the Category/Channel Editor to pick an exact item, then set its protection mode."
        ),
        color=discord.Color.blurple(),
    )

    default_lines = []
    for name in sorted(studio.DEFAULT_PROTECTED_NAMES)[:16]:
        override = rules.get(studio.normalize_base_name(name))
        if override:
            default_lines.append(f"• `{name}` → **{_protection_mode_label(override)}**")
        else:
            default_lines.append(f"• `{name}` → **Never rename**")
    embed.add_field(name="Default protected names", value="\n".join(default_lines)[:1024], inline=False)

    if rules:
        lines = []
        for base, mode in sorted(rules.items()):
            lines.append(f"• `{base}` → **{_protection_mode_label(mode)}**")
        embed.add_field(name="Saved overrides", value="\n".join(lines[:20])[:1024], inline=False)
    else:
        embed.add_field(name="Saved overrides", value="None yet.", inline=False)

    embed.add_field(
        name="Modes",
        value=(
            "**Never rename** = safest\n"
            "**Emoji only / Separator only / Font only** = partial styling\n"
            "**Full styling** = allow all design formatting"
        ),
        inline=False,
    )
    embed.set_footer(text="Protected items do not block Apply. They are safe skips unless overridden.")
    return _clean_design_embed(embed)


class ProtectionManagerButton(discord.ui.Button):
    def __init__(self, *, row: int = 4) -> None:
        super().__init__(
            label="Protection Manager",
            emoji="🛡️",
            style=discord.ButtonStyle.secondary,
            custom_id="dank_design:protection_manager",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        options = await _load_design_options(int(guild.id))
        await interaction.response.edit_message(
            embed=_protection_manager_embed(guild, options),
            view=ProtectionManagerView(),
        )


class ProtectionManagerView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Pick Item with Editor", emoji="#️⃣", style=discord.ButtonStyle.primary, custom_id="dank_design:protection_pick_item", row=0)
    async def pick_item(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        if "ChannelEditorPickerView" not in globals():
            return await interaction.response.send_message("Channel Editor is not installed yet.", ephemeral=True)
        await interaction.response.edit_message(
            embed=_channel_editor_embed(guild, page=0),
            view=ChannelEditorPickerView(guild, page=0),
        )

    @discord.ui.button(label="Back to Design Studio", emoji="🎨", style=discord.ButtonStyle.secondary, custom_id="dank_design:protection_back", row=4)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        assert interaction.guild is not None
        options = await _load_design_options(int(interaction.guild.id))
        await interaction.response.edit_message(embed=_home_embed(interaction.guild, options), view=DesignHomeView(options))


class ProtectionModeSelect(discord.ui.Select):
    def __init__(self, *, channel_id: int, current: str | None = None) -> None:
        current = _safe_str(current or "")
        options = [
            discord.SelectOption(
                label=label,
                value=mode,
                default=mode == current,
                description=description[:100],
            )
            for mode, (label, description) in PROTECTION_LABELS.items()
        ]
        options.append(discord.SelectOption(label="Clear override", value="__clear__", description="Return this item to default protection behavior."))
        super().__init__(placeholder="Choose protection mode for this item", min_values=1, max_values=1, options=options[:25], row=0)
        self.channel_id = int(channel_id)

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        channel = guild.get_channel(self.channel_id)
        if channel is None:
            return await interaction.response.send_message("That channel/category no longer exists.", ephemeral=True)

        base = _base_for_channel(channel)
        selected = self.values[0]
        mode = None if selected == "__clear__" else selected

        options = await _save_protection_rule(interaction, base_name=base, mode=mode)
        embed = _channel_action_embed(channel) if "_channel_action_embed" in globals() else discord.Embed(title="🛡️ Protection Updated", color=discord.Color.green())
        embed.title = "✅ Protection Rule Updated"
        embed.description = (
            f"`{base}` now uses **{_protection_mode_label(mode or 'never') if mode else 'default protection'}**."
        )
        embed.add_field(
            name="Next step",
            value="Run **Review Repairs** or **Preview Server** to see the result.",
            inline=False,
        )
        await interaction.response.edit_message(embed=embed, view=ChannelEditorActionView(self.channel_id) if "ChannelEditorActionView" in globals() else None)


class ProtectionModeView(discord.ui.View):
    def __init__(self, *, channel_id: int, current: str | None = None) -> None:
        super().__init__(timeout=900)
        self.channel_id = int(channel_id)
        self.add_item(ProtectionModeSelect(channel_id=self.channel_id, current=current))

    @discord.ui.button(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="dank_design:protection_mode_back", row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        channel = guild.get_channel(self.channel_id)
        if channel is None:
            return await interaction.response.edit_message(embed=_protection_manager_embed(guild, await _load_design_options(int(guild.id))), view=ProtectionManagerView())
        await interaction.response.edit_message(embed=_channel_action_embed(channel), view=ChannelEditorActionView(self.channel_id))

async def _open_protection_mode_editor(interaction: discord.Interaction, *, channel_id: int) -> None:
    if not await _require_design_permission(interaction):
        return
    guild = interaction.guild
    assert guild is not None
    channel = guild.get_channel(int(channel_id))
    if channel is None:
        return await interaction.response.send_message("That channel/category no longer exists.", ephemeral=True)

    options = await _load_design_options(int(guild.id))
    rules = _protection_rules(options)
    base = _base_for_channel(channel)
    current = rules.get(base)

    embed = discord.Embed(
        title=f"🛡️ Protection Mode · {_safe_str(getattr(channel, 'name', 'Channel'))}",
        description=(
            f"Base name: `{base}`\n\n"
            "Choose how much the design engine may change this item."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Current mode", value=f"**{_protection_mode_label(current or 'never') if current else 'Default'}**", inline=False)
    await interaction.response.edit_message(embed=embed, view=ProtectionModeView(channel_id=int(channel.id), current=current))



def _start_here_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🧭 Dank Design Start Here",
        description=(
            "Use this order when you want a clean server design without guessing."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Fast whole-server design",
        value=(
            "**1.** Pick a theme and strength.\n"
            "**2.** Press **Preview Design**.\n"
            "**3.** Review the preview.\n"
            "**4.** Press **Apply Reviewed Changes** on the preview screen."
        ),
        inline=False,
    )
    embed.add_field(
        name="Fix only messy/inconsistent names",
        value=(
            "**1.** Press **Fix Inconsistencies**.\n"
            "**2.** Review what drifted.\n"
            "**3.** Press **Apply Reviewed Changes**."
        ),
        inline=False,
    )
    embed.add_field(
        name="Edit one category or channel",
        value=(
            "**1.** Open **Category Editor** or **Channel Editor**.\n"
            "**2.** Pick the item using Dank Shield's buttons.\n"
            "**3.** Press **Edit Exact Format**.\n"
            "**4.** Choose font/separator/frame/strength.\n"
            "**5.** Press **Save & Preview**.\n"
            "**6.** Press **Apply Reviewed Changes**."
        ),
        inline=False,
    )
    embed.set_footer(text="Nothing applies until you reach a preview and press Apply Reviewed Changes.")
    return _clean_design_embed(embed)


class StartHereButton(discord.ui.Button):
    def __init__(self, *, row: int = 2) -> None:
        super().__init__(
            label="Start Here",
            emoji="🧭",
            style=discord.ButtonStyle.success,
            custom_id="dank_design:start_here",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not await _require_design_permission(interaction):
            return
        await interaction.response.edit_message(embed=_start_here_embed(), view=StartHereView())


class StartHereView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Back to Design Studio", emoji="🎨", style=discord.ButtonStyle.secondary, custom_id="dank_design:start_here_back", row=4)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        assert interaction.guild is not None
        options = await _load_design_options(int(interaction.guild.id))
        await interaction.response.edit_message(embed=_home_embed(interaction.guild, options), view=DesignHomeView(options))



def _editors_locks_embed(guild: discord.Guild, options: Mapping[str, Any]) -> discord.Embed:
    counts = _lock_count(options) if "_lock_count" in globals() else {"global": 0, "categories": 0, "channels": 0}
    embed = discord.Embed(
        title="🧰 Editors & Locks",
        description=(
            "Use this section only when you want exact control.\n\n"
            "**Category Editor** = design a whole category.\n"
            "**Channel Editor** = override one channel.\n"
            "**Format Locks** = save reusable layouts.\n"
            "**Protection Manager** = decide what the bot may rename.\n"
            "**Design Doctor** = audit before applying."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Saved locks",
        value=(
            f"Global: **{'On' if counts.get('global') else 'Off'}**\n"
            f"Category locks: **{counts.get('categories', 0)}**\n"
            f"Channel overrides: **{counts.get('channels', 0)}**"
        ),
        inline=False,
    )
    embed.add_field(
        name="Recommended path",
        value=(
            "1. Open **Category Editor** or **Channel Editor**.\n"
            "2. Pick an item using Dank Shield buttons.\n"
            "3. Press **Edit Exact Format**.\n"
            "4. Press **Save & Preview**.\n"
            "5. Press **Apply Reviewed Changes**."
        ),
        inline=False,
    )
    return _clean_design_embed(embed)


class EditorsLocksButton(discord.ui.Button):
    def __init__(self, *, row: int = 3) -> None:
        super().__init__(
            label="Editors & Locks",
            emoji="🧰",
            style=discord.ButtonStyle.primary,
            custom_id="dank_design:editors_locks",
            row=row,
        )

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        options = await _load_design_options(int(guild.id))
        await interaction.response.edit_message(
            embed=_editors_locks_embed(guild, options),
            view=EditorsLocksView(),
        )


class EditorsLocksView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Category Editor", emoji="🗂️", style=discord.ButtonStyle.primary, custom_id="dank_design:submenu_category_editor", row=0)
    async def category_editor(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.edit_message(
            embed=_category_editor_embed(guild, page=0),
            view=CategoryEditorPickerView(guild, page=0),
        )

    @discord.ui.button(label="Channel Editor", emoji="#️⃣", style=discord.ButtonStyle.primary, custom_id="dank_design:submenu_channel_editor", row=0)
    async def channel_editor(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.edit_message(
            embed=_channel_editor_embed(guild, page=0),
            view=ChannelEditorPickerView(guild, page=0),
        )

    @discord.ui.button(label="Format Locks / Layouts", emoji="🔒", style=discord.ButtonStyle.secondary, custom_id="dank_design:submenu_format_locks", row=1)
    async def format_locks(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        options = await _load_design_options(int(guild.id))
        await interaction.response.edit_message(
            embed=_format_locks_embed(guild, options),
            view=FormatLocksView(),
        )

    @discord.ui.button(label="Manage Saved Locks", emoji="🔐", style=discord.ButtonStyle.secondary, custom_id="dank_design:submenu_manage_locks", row=1)
    async def manage_locks(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        options = await _load_design_options(int(guild.id))
        await interaction.response.edit_message(
            embed=_format_lock_manager_embed(guild, options, page=0),
            view=LockManagerView(guild, options, page=0),
        )

    @discord.ui.button(label="Protection Manager", emoji="🛡️", style=discord.ButtonStyle.secondary, custom_id="dank_design:submenu_protection", row=2)
    async def protection(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        options = await _load_design_options(int(guild.id))
        await interaction.response.edit_message(
            embed=_protection_manager_embed(guild, options),
            view=ProtectionManagerView(),
        )

    @discord.ui.button(label="Design Doctor", emoji="🩺", style=discord.ButtonStyle.secondary, custom_id="dank_design:submenu_doctor", row=2)
    async def doctor(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.defer(ephemeral=True, thinking=True)
        options = await _load_design_options(int(guild.id))
        items = await build_design_plan(guild, options)
        await interaction.edit_original_response(
            embed=_doctor_embed(guild, options, items),
            view=DesignDoctorView(),
        )

    @discord.ui.button(label="Back to Design Studio", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="dank_design:submenu_back", row=4)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        assert interaction.guild is not None
        options = await _load_design_options(int(interaction.guild.id))
        await interaction.response.edit_message(
            embed=_home_embed(interaction.guild, options),
            view=DesignHomeView(options),
        )



def _design_help_embed() -> discord.Embed:
    embed = discord.Embed(
        title="❓ Dank Design Help",
        description=(
            "Dank Design only changes visible channel/category names. "
            "It never changes permissions, topics, order, slowmode, NSFW, archive settings, or category placement."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="How to apply changes",
        value=(
            "Changes are never applied from the home screen.\n"
            "**Preview Server** or **Save & Preview** first, then press **Apply Reviewed Changes**."
        ),
        inline=False,
    )
    embed.add_field(
        name="Best workflow",
        value=(
            "**Quick server style:** Preview Server\n"
            "**Fix drift:** Review Repairs\n"
            "**Exact control:** Category Editor or Channel Editor → Edit Exact Format → Save & Preview"
        ),
        inline=False,
    )
    embed.add_field(
        name="Font fallback",
        value=(
            "If one font cannot style a letter, Dank Shield tries safe fallback glyphs instead of blocking the rename."
        ),
        inline=False,
    )
    embed.set_footer(text="Use Advanced Tools only for doctor checks, locks, protection, rollback, and help.")
    return _clean_design_embed(embed)


def _advanced_tools_embed() -> discord.Embed:
    embed = discord.Embed(
        title="⚙️ Dank Design Advanced Tools",
        description=(
            "These tools are useful after the basic workflow. "
            "Most users only need Preview Server, Review Repairs, Category Editor, or Channel Editor."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Tools",
        value=(
            "🩺 **Design Doctor** — audit saved rules, drift, duplicates, blockers.\n"
            "🔒 **Format Locks / Layouts** — save reusable layouts.\n"
            "🔐 **Manage Saved Locks** — remove old overrides or stale locks.\n"
            "🛡 **Protection Manager** — choose what should never be renamed.\n"
            "↩️ **Rollback** — undo the last applied rename batch.\n"
            "❓ **Help** — explain the workflow."
        ),
        inline=False,
    )
    return _clean_design_embed(embed)


class AdvancedToolsView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Design Doctor", emoji="🩺", style=discord.ButtonStyle.secondary, custom_id="dank_design:advanced_doctor", row=0)
    async def doctor(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.defer(ephemeral=True, thinking=True)
        options = await _load_design_options(int(guild.id))
        items = await build_design_plan(guild, options)
        await interaction.edit_original_response(embed=_doctor_embed(guild, options, items), view=DesignDoctorView())

    @discord.ui.button(label="Format Locks / Layouts", emoji="🔒", style=discord.ButtonStyle.primary, custom_id="dank_design:advanced_format_locks", row=0)
    async def format_locks(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        options = await _load_design_options(int(guild.id))
        await interaction.response.edit_message(embed=_format_locks_embed(guild, options), view=FormatLocksView())

    @discord.ui.button(label="Manage Saved Locks", emoji="🔐", style=discord.ButtonStyle.secondary, custom_id="dank_design:advanced_manage_locks", row=1)
    async def manage_locks(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        options = await _load_design_options(int(guild.id))
        await interaction.response.edit_message(embed=_format_lock_manager_embed(guild, options, page=0), view=LockManagerView(guild, options, page=0))

    @discord.ui.button(label="Protection Manager", emoji="🛡️", style=discord.ButtonStyle.secondary, custom_id="dank_design:advanced_protection", row=1)
    async def protection(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        options = await _load_design_options(int(guild.id))
        await interaction.response.edit_message(embed=_protection_manager_embed(guild, options), view=ProtectionManagerView())

    @discord.ui.button(label="Rollback", emoji="↩️", style=discord.ButtonStyle.danger, custom_id="dank_design:advanced_rollback", row=2)
    async def rollback(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_rollback(interaction)

    @discord.ui.button(label="Help", emoji="❓", style=discord.ButtonStyle.secondary, custom_id="dank_design:advanced_help", row=2)
    async def help(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        await interaction.response.edit_message(embed=_design_help_embed(), view=AdvancedToolsView())

    @discord.ui.button(label="Back to Design Studio", emoji="⬅️", style=discord.ButtonStyle.primary, custom_id="dank_design:advanced_back", row=4)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        assert interaction.guild is not None
        options = await _load_design_options(int(interaction.guild.id))
        await interaction.response.edit_message(embed=_home_embed(interaction.guild, options), view=DesignHomeView(options))




class DesignHomeView(discord.ui.View):
    def __init__(self, options: Mapping[str, Any] | None = None) -> None:
        super().__init__(timeout=900)
        options = options or {}
        self.add_item(ThemeSelect(_safe_str(options.get("theme_id"), "gothic_clean")))
        self.add_item(StrengthSelect(_safe_int(options.get("strength"), 2)))

    @discord.ui.button(label="Review Repairs", emoji="🧭", style=discord.ButtonStyle.success, custom_id="dank_design:consistency_check", row=2)
    async def consistency_check(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None

        await interaction.response.defer(ephemeral=True, thinking=True)

        options = await _load_design_options(int(guild.id))
        repair_options = dict(options)
        repair_options["__use_live_majority_layout"] = True

        items = await build_design_plan(guild, repair_options)
        key = _key(int(guild.id), int(interaction.user.id))
        _PENDING[key] = {
            "created_at": time.time(),
            "items": items,
            "options": dict(repair_options),
            "mode": "consistency_check",
        }

        has_blockers = any(item.get("status") == "failed" for item in items)
        has_changes = any(item.get("status") == "changed" for item in items)

        await interaction.edit_original_response(
            embed=_consistency_embed(guild, items, repair_options),
            view=DesignPreviewView(can_apply=not has_blockers and has_changes),
        )

    @discord.ui.button(label="Preview Server", emoji="👁️", style=discord.ButtonStyle.primary, custom_id="dank_design:preview", row=2)
    async def preview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.defer(ephemeral=True, thinking=True)
        options = await _load_design_options(int(guild.id))
        items = await build_design_plan(guild, options)
        _PENDING[_key(int(guild.id), int(interaction.user.id))] = {
            "created_at": time.time(),
            "items": items,
            "options": dict(options),
            "mode": "preview_server",
        }
        has_blockers = any(item.get("status") == "failed" for item in items)
        await interaction.edit_original_response(
            embed=_preview_embed(guild, items, title="👁️ Server Design Preview"),
            view=DesignPreviewView(can_apply=not has_blockers and any(item.get("status") == "changed" for item in items)),
        )

    @discord.ui.button(label="Category Editor", emoji="🗂️", style=discord.ButtonStyle.primary, custom_id="dank_design:category_editor", row=3)
    async def category_editor(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.edit_message(
            embed=_category_editor_embed(guild, page=0),
            view=CategoryEditorPickerView(guild, page=0),
        )

    @discord.ui.button(label="Channel Editor", emoji="#️⃣", style=discord.ButtonStyle.primary, custom_id="dank_design:channel_editor", row=3)
    async def channel_editor(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        await interaction.response.edit_message(
            embed=_channel_editor_embed(guild, page=0),
            view=ChannelEditorPickerView(guild, page=0),
        )

    @discord.ui.button(label="Guide", emoji="❓", style=discord.ButtonStyle.secondary, custom_id="dank_design:start_here", row=4)
    async def guide(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        await interaction.response.edit_message(embed=_start_here_embed(), view=StartHereView())

    @discord.ui.button(label="Advanced", emoji="⚙️", style=discord.ButtonStyle.secondary, custom_id="dank_design:advanced_tools", row=4)
    async def advanced_tools(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        await interaction.response.edit_message(embed=_advanced_tools_embed(), view=AdvancedToolsView())



class DesignPreviewView(discord.ui.View):
    def __init__(self, *, can_apply: bool) -> None:
        super().__init__(timeout=900)
        self.apply.disabled = not can_apply

    @discord.ui.button(label="Apply Reviewed Changes", emoji="✅", style=discord.ButtonStyle.success, custom_id="dank_design:apply", row=0)
    async def apply(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_design_permission(interaction):
            return
        guild = interaction.guild
        assert guild is not None
        key = _key(int(guild.id), int(interaction.user.id))
        payload = _PENDING.get(key) or {}
        items = list(payload.get("items") or [])
        if not items:
            await interaction.response.send_message("No saved preview found. Press **Preview Server** first.", ephemeral=True)
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
            snapshot_payload = {"created_at": time.time(), "items": snapshot, "admin_id": str(int(interaction.user.id))}
            _LAST_SNAPSHOTS.setdefault(_guild_key(int(guild.id)), []).append(snapshot_payload)
            _LAST_SNAPSHOTS[_guild_key(int(guild.id))] = _LAST_SNAPSHOTS[_guild_key(int(guild.id))][-10:]
            await _persist_rollback_snapshot(int(guild.id), snapshot_payload)
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
    latest = await _latest_rollback_snapshot(int(guild.id))
    if not latest:
        await interaction.response.send_message("No rollback snapshot is available for this server.", ephemeral=True)
        return
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
        latest = await _latest_rollback_snapshot(int(guild.id))
        if not latest:
            await interaction.response.send_message("No rollback snapshot found.", ephemeral=True)
            return
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
        await _pop_latest_rollback_snapshot(int(guild.id))
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
