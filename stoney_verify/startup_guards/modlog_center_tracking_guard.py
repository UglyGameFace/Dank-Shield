from __future__ import annotations

"""Turn Modlog Center into a live tracking-control panel."""

from typing import Any

import discord

_PATCHED = False
_ORIGINAL_MODLOG_CALLBACK: Any = None
_ORIGINAL_COVERAGE_SEND: Any = None
_ORIGINAL_PARITY_SEND: Any = None

CATEGORIES: tuple[tuple[str, str, str], ...] = (
    ("messages", "💬", "Messages"),
    ("members", "👥", "Members"),
    ("moderation", "🔨", "Moderation"),
    ("voice", "🔊", "Voice"),
    ("channels", "#️⃣", "Channels"),
    ("roles", "🎭", "Roles"),
    ("threads", "🧵", "Threads"),
    ("invites", "🔗", "Invites"),
    ("server", "🏠", "Server"),
    ("assets", "😀", "Emojis/Stickers"),
    ("webhooks", "🪝", "Webhooks"),
)

DEFAULT_ON = {key for key, _emoji, _label in CATEGORIES}
KEY = "modlog_tracking_categories"


def _as_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(x).strip().lower() for x in value if str(x).strip()]
    if isinstance(value, str):
        return [x.strip().lower() for x in value.replace(";", ",").split(",") if x.strip()]
    return []


def _cfg_value(cfg: Any, name: str) -> Any:
    try:
        if hasattr(cfg, "get") and cfg.get(name) is not None:
            return cfg.get(name)
    except Exception:
        pass
    try:
        value = getattr(cfg, name, None)
        if value is not None:
            return value
    except Exception:
        pass
    for bucket in ("settings", "config", "metadata", "meta"):
        try:
            nested = getattr(cfg, bucket, None)
            if isinstance(nested, dict) and nested.get(name) is not None:
                return nested.get(name)
        except Exception:
            pass
        try:
            if hasattr(cfg, "get"):
                nested = cfg.get(bucket)
                if isinstance(nested, dict) and nested.get(name) is not None:
                    return nested.get(name)
        except Exception:
            pass
    return None


def _saved(cfg: Any) -> set[str]:
    values = set(_as_list(_cfg_value(cfg, KEY)))
    allowed = {key for key, _emoji, _label in CATEGORIES}
    return (values & allowed) if values else set(DEFAULT_ON)


async def _load_cfg(guild: discord.Guild) -> Any:
    from stoney_verify.guild_config import get_guild_config
    return await get_guild_config(int(guild.id), refresh=True)


async def _save_cfg(guild: discord.Guild, values: set[str], actor: discord.abc.User | None = None) -> None:
    from stoney_verify.guild_config import invalidate_guild_config, upsert_guild_config
    payload = {
        KEY: sorted(values),
        "modlog_enabled": bool(values),
        "modlog_updated_by_id": str(getattr(actor, "id", "") or ""),
    }
    await upsert_guild_config(int(guild.id), payload)
    invalidate_guild_config(int(guild.id))


def _category_from_title(title: str) -> str:
    text = str(title or "").lower()
    if "message" in text:
        return "messages"
    if "voice" in text or "vc" in text:
        return "voice"
    if "ban" in text or "kick" in text or "timeout" in text or "warn" in text or "mod" in text:
        return "moderation"
    if "member" in text or "join" in text or "left" in text or "leave" in text:
        return "members"
    if "channel" in text:
        return "channels"
    if "role" in text:
        return "roles"
    if "thread" in text:
        return "threads"
    if "invite" in text:
        return "invites"
    if "emoji" in text or "sticker" in text:
        return "assets"
    if "webhook" in text:
        return "webhooks"
    if "server" in text or "guild" in text:
        return "server"
    return "server"


async def _allowed(guild: discord.Guild, embed: discord.Embed) -> bool:
    try:
        cfg = await _load_cfg(guild)
        category = _category_from_title(getattr(embed, "title", "") or "")
        return category in _saved(cfg)
    except Exception:
        return True


async def _open_embed(guild: discord.Guild) -> discord.Embed:
    cfg = await _load_cfg(guild)
    enabled = _saved(cfg)
    try:
        from stoney_verify.commands_ext import public_modlog_group as group
        channel = group._modlog_channel(guild, cfg)
    except Exception:
        channel = None
    embed = discord.Embed(
        title="🧾 Modlog Tracking Center",
        description="Choose exactly which audit events Dank Shield tracks. Buttons update this card instantly.",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Saved channel", value=channel.mention if isinstance(channel, discord.TextChannel) else "⚠️ No saved modlog channel", inline=False)
    on_lines: list[str] = []
    off_lines: list[str] = []
    for key, emoji, label in CATEGORIES:
        (on_lines if key in enabled else off_lines).append(f"{emoji} {label}")
    embed.add_field(name=f"Tracked now ({len(on_lines)}/{len(CATEGORIES)})", value="\n".join(on_lines)[:1024] or "None", inline=True)
    embed.add_field(name="Ignored now", value="\n".join(off_lines)[:1024] or "None", inline=True)
    embed.set_footer(text="Health checks channel/listener coverage. Send Test uses the saved modlog channel.")
    return embed


class TrackingButton(discord.ui.Button):
    def __init__(self, key: str, emoji: str, label: str, enabled: bool, row: int) -> None:
        super().__init__(label=f"{label} {'ON' if enabled else 'OFF'}", emoji=emoji, style=discord.ButtonStyle.success if enabled else discord.ButtonStyle.secondary, custom_id=f"dank_modlog_track:{key}", row=row)
        self.track_key = key

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        cfg = await _load_cfg(guild)
        enabled = _saved(cfg)
        if self.track_key in enabled:
            enabled.remove(self.track_key)
        else:
            enabled.add(self.track_key)
        await _save_cfg(guild, enabled, interaction.user)
        await interaction.response.edit_message(embed=await _open_embed(guild), view=ModlogTrackingView(guild, enabled))


class ModlogTrackingView(discord.ui.View):
    def __init__(self, guild: discord.Guild, enabled: set[str] | None = None) -> None:
        super().__init__(timeout=900)
        enabled = set(enabled or DEFAULT_ON)
        for index, (key, emoji, label) in enumerate(CATEGORIES):
            self.add_item(TrackingButton(key, emoji, label, key in enabled, row=min(3, index // 3)))

    @discord.ui.button(label="All On", emoji="✅", style=discord.ButtonStyle.success, custom_id="dank_modlog_track:all", row=4)
    async def all_on(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        enabled = set(DEFAULT_ON)
        await _save_cfg(guild, enabled, interaction.user)
        await interaction.response.edit_message(embed=await _open_embed(guild), view=ModlogTrackingView(guild, enabled))

    @discord.ui.button(label="All Off", emoji="⏸️", style=discord.ButtonStyle.danger, custom_id="dank_modlog_track:none", row=4)
    async def all_off(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        enabled: set[str] = set()
        await _save_cfg(guild, enabled, interaction.user)
        await interaction.response.edit_message(embed=await _open_embed(guild), view=ModlogTrackingView(guild, enabled))

    @discord.ui.button(label="Health", emoji="🩺", style=discord.ButtonStyle.primary, custom_id="dank_modlog_track:health", row=4)
    async def health(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require(interaction):
            return
        from stoney_verify.commands_ext import public_modlog_group as modlog
        await modlog.open_modlog_health(interaction)

    @discord.ui.button(label="Send Test", emoji="📨", style=discord.ButtonStyle.primary, custom_id="dank_modlog_track:test", row=4)
    async def test(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require(interaction):
            return
        from stoney_verify.commands_ext import public_modlog_group as modlog
        await modlog.send_modlog_test(interaction)

    @discord.ui.button(label="Back", emoji="↩️", style=discord.ButtonStyle.secondary, custom_id="dank_modlog_track:back", row=4)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        try:
            from stoney_verify.startup_guards import setup_smart_home_menu_guard as smart
            await smart._open_features_home(interaction)
        except Exception:
            await interaction.response.defer(ephemeral=True)


async def _require(interaction: discord.Interaction) -> bool:
    try:
        from stoney_verify.commands_ext import public_setup_solid as solid
        return bool(await solid._require_setup_permission(interaction))
    except Exception:
        return False


async def _open_modlog_tracking(interaction: discord.Interaction) -> None:
    if not await _require(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
    cfg = await _load_cfg(guild)
    enabled = _saved(cfg)
    embed = await _open_embed(guild)
    view = ModlogTrackingView(guild, enabled)
    try:
        await interaction.response.edit_message(embed=embed, view=view)
    except Exception:
        await interaction.followup.send(embed=embed, view=view, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


def apply() -> bool:
    global _PATCHED, _ORIGINAL_MODLOG_CALLBACK, _ORIGINAL_COVERAGE_SEND, _ORIGINAL_PARITY_SEND
    if _PATCHED:
        return True
    try:
        from stoney_verify.startup_guards import setup_smart_home_menu_guard as smart
        _ORIGINAL_MODLOG_CALLBACK = smart.FeatureCentersView.modlog

        async def patched_modlog(self: Any, interaction: discord.Interaction, button: discord.ui.Button) -> None:
            await _open_modlog_tracking(interaction)

        smart.FeatureCentersView.modlog = patched_modlog
    except Exception:
        pass

    try:
        from stoney_verify.commands_ext import public_modlog_coverage as coverage
        _ORIGINAL_COVERAGE_SEND = coverage._send

        async def coverage_send(guild: discord.Guild, embed: discord.Embed) -> None:
            if await _allowed(guild, embed):
                await _ORIGINAL_COVERAGE_SEND(guild, embed)

        coverage._send = coverage_send
    except Exception:
        pass

    try:
        from stoney_verify.startup_guards import modlog_probot_parity_guard as parity
        _ORIGINAL_PARITY_SEND = parity._send

        async def parity_send(guild: discord.Guild, embed: discord.Embed) -> None:
            if await _allowed(guild, embed):
                await _ORIGINAL_PARITY_SEND(guild, embed)

        parity._send = parity_send
    except Exception:
        pass

    _PATCHED = True
    print("✅ modlog_center_tracking_guard active; Modlog Center supports live event-family toggles")
    return True


apply()

__all__ = ["apply"]