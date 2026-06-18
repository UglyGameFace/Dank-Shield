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
        result = studio.build_styled_name(
            current_name,
            kind="category" if kind == "category" else "text",
            theme_id=theme_id,
            strength=strength,
            icon_mode=icon_mode,
            protection_rules=protection_rules,
        )
        parent = getattr(channel, "category", None)
        item = result.to_plan_item(channel_id=getattr(channel, "id", ""), category_id=getattr(parent, "id", ""))
        item["kind"] = kind
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



class DesignHomeView(discord.ui.View):
    def __init__(self, options: Mapping[str, Any] | None = None) -> None:
        super().__init__(timeout=900)
        options = options or {}
        self.add_item(ThemeSelect(_safe_str(options.get("theme_id"), "gothic_clean")))
        self.add_item(StrengthSelect(_safe_int(options.get("strength"), 2)))

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
