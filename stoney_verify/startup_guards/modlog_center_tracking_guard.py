from __future__ import annotations

"""Make the setup Modlog Center button open visible tracking toggles."""

from typing import Any

import discord

_PATCHED = False
_ORIGINAL_FEATURE_INIT: Any = None

CATEGORIES: tuple[tuple[str, str, str], ...] = (
    ("messages", "💬", "Messages: deletes/edits"),
    ("members", "👥", "Members: joins/leaves/names"),
    ("moderation", "🔨", "Moderation: bans/kicks/timeouts"),
    ("voice", "🔊", "Voice: VC joins/leaves/moves"),
    ("channels", "#️⃣", "Channels: create/edit/delete"),
    ("roles", "🎭", "Roles: create/edit/delete/member roles"),
    ("threads", "🧵", "Threads: create/edit/delete"),
    ("invites", "🔗", "Invites: create/delete/usage"),
    ("server", "🏠", "Server: name/icon/settings"),
    ("assets", "😀", "Emojis/Stickers: changes"),
    ("webhooks", "🪝", "Webhooks: create/edit/delete"),
)
DETAILS: dict[str, str] = {
    "messages": "Message deletes, edits, purge-style cleanup, and content changes.",
    "members": "Server joins, server leaves, nickname/name changes, and member state changes.",
    "moderation": "Bans, unbans, kicks, timeouts, warnings, and staff moderation actions.",
    "voice": "Voice channel joins, leaves, moves, server mute/deafen, self mute/deafen, stream, and video changes.",
    "channels": "Text/voice/category/forum channel create, edit, delete, and permission changes when available.",
    "roles": "Role create, edit, delete, and member role add/remove events.",
    "threads": "Thread create, archive, unarchive, edit, and delete events.",
    "invites": "Invite create/delete events and invite-related attribution when available.",
    "server": "Guild name, icon, moderation-level, and major server-setting changes.",
    "assets": "Emoji and sticker create, edit, and delete events.",
    "webhooks": "Webhook create, edit, and delete events.",
}
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
    return None


def _saved(cfg: Any) -> set[str]:
    allowed = {key for key, _emoji, _label in CATEGORIES}
    values = set(_as_list(_cfg_value(cfg, KEY))) & allowed
    return values if values else set(DEFAULT_ON)


async def _load_cfg(guild: discord.Guild) -> Any:
    from stoney_verify.guild_config import get_guild_config
    return await get_guild_config(int(guild.id), refresh=True)


async def _save_cfg(guild: discord.Guild, values: set[str], actor: discord.abc.User | None = None) -> None:
    from stoney_verify.guild_config import invalidate_guild_config, upsert_guild_config
    await upsert_guild_config(int(guild.id), {KEY: sorted(values), "modlog_enabled": bool(values), "modlog_updated_by_id": str(getattr(actor, "id", "") or "")})
    invalidate_guild_config(int(guild.id))


async def _require(interaction: discord.Interaction) -> bool:
    try:
        from stoney_verify.commands_ext import public_setup_solid as solid
        return bool(await solid._require_setup_permission(interaction))
    except Exception:
        return False


async def _tracking_embed(guild: discord.Guild) -> discord.Embed:
    cfg = await _load_cfg(guild)
    enabled = _saved(cfg)
    channel_text = "⚠️ No saved modlog channel"
    try:
        from stoney_verify.commands_ext import public_modlog_group as group
        channel = group._modlog_channel(guild, cfg)
        if isinstance(channel, discord.TextChannel):
            channel_text = channel.mention
    except Exception:
        pass
    on_lines: list[str] = []
    off_lines: list[str] = []
    detail_lines: list[str] = []
    for key, emoji, label in CATEGORIES:
        line = f"{emoji} {label}"
        (on_lines if key in enabled else off_lines).append(line)
        if key in enabled:
            detail_lines.append(f"{emoji} **{label}** — {DETAILS.get(key, 'Tracked event family.')}")
    embed = discord.Embed(
        title="🧾 Modlog Tracking Center",
        description="Select exactly what Dank Shield tracks. Buttons update this card instantly.",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Saved channel", value=channel_text, inline=False)
    embed.add_field(name=f"Tracked now ({len(on_lines)}/{len(CATEGORIES)})", value="\n".join(on_lines) or "None", inline=False)
    embed.add_field(name="Ignored now", value="\n".join(off_lines) or "None", inline=False)
    embed.add_field(name="What those toggles mean", value="\n".join(detail_lines[:8])[:1024] or "Nothing is currently enabled.", inline=False)
    return embed


class TrackButton(discord.ui.Button):
    def __init__(self, key: str, emoji: str, label: str, enabled: bool, row: int) -> None:
        short = label.split(":", 1)[0]
        super().__init__(label=f"{short}: {'ON' if enabled else 'OFF'}", emoji=emoji, style=discord.ButtonStyle.success if enabled else discord.ButtonStyle.secondary, custom_id=f"dank_modlog_track:{key}", row=row)
        self.key = key

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        cfg = await _load_cfg(guild)
        enabled = _saved(cfg)
        if self.key in enabled:
            enabled.remove(self.key)
        else:
            enabled.add(self.key)
        await _save_cfg(guild, enabled, interaction.user)
        await interaction.response.edit_message(embed=await _tracking_embed(guild), view=ModlogTrackingView(guild, enabled))


class ModlogTrackingView(discord.ui.View):
    def __init__(self, guild: discord.Guild, enabled: set[str] | None = None) -> None:
        super().__init__(timeout=900)
        enabled = set(enabled or DEFAULT_ON)
        for index, (key, emoji, label) in enumerate(CATEGORIES):
            self.add_item(TrackButton(key, emoji, label, key in enabled, row=min(3, index // 3)))

    @discord.ui.button(label="All On", emoji="✅", style=discord.ButtonStyle.success, row=4)
    async def all_on(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        guild = interaction.guild
        if guild is None or not await _require(interaction):
            return
        enabled = set(DEFAULT_ON)
        await _save_cfg(guild, enabled, interaction.user)
        await interaction.response.edit_message(embed=await _tracking_embed(guild), view=ModlogTrackingView(guild, enabled))

    @discord.ui.button(label="All Off", emoji="⏸️", style=discord.ButtonStyle.danger, row=4)
    async def all_off(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        guild = interaction.guild
        if guild is None or not await _require(interaction):
            return
        enabled: set[str] = set()
        await _save_cfg(guild, enabled, interaction.user)
        await interaction.response.edit_message(embed=await _tracking_embed(guild), view=ModlogTrackingView(guild, enabled))

    @discord.ui.button(label="Health", emoji="🩺", style=discord.ButtonStyle.primary, row=4)
    async def health(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require(interaction):
            return
        from stoney_verify.commands_ext import public_modlog_group as modlog
        await modlog.open_modlog_health(interaction)

    @discord.ui.button(label="Send Test", emoji="📨", style=discord.ButtonStyle.primary, row=4)
    async def test(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require(interaction):
            return
        from stoney_verify.commands_ext import public_modlog_group as modlog
        await modlog.send_modlog_test(interaction)


async def _open_modlog_tracking(interaction: discord.Interaction) -> None:
    if not await _require(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
    await interaction.response.edit_message(embed=await _tracking_embed(guild), view=ModlogTrackingView(guild, _saved(await _load_cfg(guild))))


def _patch_feature_button() -> bool:
    global _ORIGINAL_FEATURE_INIT
    try:
        from stoney_verify.startup_guards import setup_smart_home_menu_guard as smart
        if _ORIGINAL_FEATURE_INIT is None:
            _ORIGINAL_FEATURE_INIT = smart.FeatureCentersView.__init__

        def patched_init(self: Any) -> None:
            _ORIGINAL_FEATURE_INIT(self)
            for child in list(getattr(self, "children", []) or []):
                if str(getattr(child, "custom_id", "") or "") != "dank_setup_features:modlog":
                    continue
                child.label = "Modlog Tracking"
                child.style = discord.ButtonStyle.primary

                async def cb(interaction: discord.Interaction) -> None:
                    await _open_modlog_tracking(interaction)

                child.callback = cb
                break

        smart.FeatureCentersView.__init__ = patched_init
        return True
    except Exception:
        return False


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        _patch_feature_button()
        return True
    ok = _patch_feature_button()
    _PATCHED = True
    print("✅ modlog_center_tracking_guard active; Modlog Center button opens explicit tracking toggles" if ok else "⚠️ modlog_center_tracking_guard loaded but button patch was delayed")
    return ok


apply()

__all__ = ["apply", "_open_modlog_tracking"]