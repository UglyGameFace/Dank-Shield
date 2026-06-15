from __future__ import annotations

"""Owned setup service for optional join and leave messages."""

from typing import Any, Optional

import discord

JOIN_TITLE = "Welcome, {username}!"
JOIN_BODY = "Welcome to **{server_name}**, {member}! Head to the start-here channels to get settled."
LEAVE_TITLE = "{username} left"
LEAVE_BODY = "{username} left **{server_name}**. Member count: {member_count}."


async def _send_ephemeral(interaction: discord.Interaction, content: str = "", **kwargs: Any) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(content, ephemeral=True, **kwargs)
        else:
            await interaction.followup.send(content, ephemeral=True, **kwargs)
    except Exception:
        pass


async def _defer(interaction: discord.Interaction) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
    except Exception:
        pass


def _cfg_value(cfg: Any, key: str, default: Any = None) -> Any:
    try:
        value = getattr(cfg, key, None)
        if value is not None:
            return value
    except Exception:
        pass
    try:
        if hasattr(cfg, "get"):
            value = cfg.get(key)
            if value is not None:
                return value
    except Exception:
        pass
    return default


def _cfg_bool(cfg: Any, key: str, default: bool = False) -> bool:
    try:
        raw = _cfg_value(cfg, key, None)
        if raw is None:
            return bool(default)
        return str(raw).strip().lower() in {"1", "true", "yes", "y", "on", "enabled"}
    except Exception:
        return bool(default)


def _cfg_int(cfg: Any, key: str, default: int = 0) -> int:
    try:
        raw = _cfg_value(cfg, key, None)
        if raw is None or isinstance(raw, bool):
            return int(default)
        text = str(raw).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _cfg_str(cfg: Any, key: str, default: str) -> str:
    try:
        text = str(_cfg_value(cfg, key, "") or "").strip()
        return text if text else default
    except Exception:
        return default


def _templates(cfg: Any, *, kind: str) -> tuple[str, str]:
    if kind == "leave":
        return _cfg_str(cfg, "welcome_leave_title", LEAVE_TITLE), _cfg_str(cfg, "welcome_leave_body", LEAVE_BODY)
    return _cfg_str(cfg, "welcome_join_title", JOIN_TITLE), _cfg_str(cfg, "welcome_join_body", JOIN_BODY)


def _format(text: str, member: discord.Member) -> str:
    guild = member.guild
    pairs = {
        "server_name": str(getattr(guild, "name", "this server") or "this server"),
        "member": member.mention,
        "username": str(member),
        "display_name": str(getattr(member, "display_name", "") or member),
        "member_count": str(getattr(guild, "member_count", "") or ""),
    }
    out = str(text or "")
    for key, value in pairs.items():
        out = out.replace("{" + key + "}", value)
    return out


def _can_post(channel: discord.TextChannel, me: Optional[discord.Member]) -> list[str]:
    if me is None:
        return ["Bot member unavailable"]
    perms = channel.permissions_for(me)
    checks = {
        "View Channel": perms.view_channel,
        "Send Messages": perms.send_messages,
        "Embed Links": perms.embed_links,
        "Read Message History": perms.read_message_history,
    }
    return [name for name, ok in checks.items() if not ok]


def _preview_embed(guild: discord.Guild, member: discord.Member, *, kind: str, cfg: Any | None = None) -> discord.Embed:
    title, body = _templates(cfg, kind=kind) if cfg is not None else ((LEAVE_TITLE, LEAVE_BODY) if kind == "leave" else (JOIN_TITLE, JOIN_BODY))
    embed = discord.Embed(
        title=_format(title, member)[:256],
        description=_format(body, member)[:4000],
        color=discord.Color.dark_grey() if kind == "leave" else discord.Color.green(),
        timestamp=discord.utils.utcnow(),
    )
    try:
        embed.set_thumbnail(url=member.display_avatar.url)
    except Exception:
        pass
    embed.set_footer(text="Preview • dank_shield:welcome_event:v1")
    return embed


async def _save_event_channel(interaction: discord.Interaction, channel: discord.TextChannel, *, kind: str) -> None:
    from stoney_verify.commands_ext.public_setup_group import _require_setup_permission, _upsert_config
    from stoney_verify.guild_config import invalidate_guild_config

    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await _send_ephemeral(interaction, "This must be used inside a server.")
    await _defer(interaction)
    missing = _can_post(channel, guild.me if isinstance(guild.me, discord.Member) else None)
    if missing:
        return await interaction.followup.send(f"I cannot post in {channel.mention}. Missing: {', '.join(missing)}.", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    if kind == "leave":
        patch = {"goodbye_channel_id": str(channel.id), "leave_channel_id": str(channel.id), "welcome_leave_enabled": True, "goodbye_enabled": True, "leave_message_enabled": True}
        label = "Leave"
    else:
        patch = {"join_welcome_channel_id": str(channel.id), "welcome_join_enabled": True, "join_welcome_enabled": True}
        label = "Join"
    patch["welcome_events_updated_by_id"] = str(int(interaction.user.id))
    await _upsert_config(int(guild.id), patch)
    invalidate_guild_config(int(guild.id))
    await interaction.followup.send(f"{label} messages enabled in {channel.mention}.", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


async def _set_enabled(interaction: discord.Interaction, *, join: Optional[bool] = None, leave: Optional[bool] = None) -> None:
    from stoney_verify.commands_ext.public_setup_group import _require_setup_permission, _upsert_config
    from stoney_verify.guild_config import invalidate_guild_config

    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await _send_ephemeral(interaction, "This must be used inside a server.")
    await _defer(interaction)
    patch: dict[str, Any] = {"welcome_events_updated_by_id": str(int(interaction.user.id))}
    if join is not None:
        patch.update({"welcome_join_enabled": bool(join), "join_welcome_enabled": bool(join)})
    if leave is not None:
        patch.update({"welcome_leave_enabled": bool(leave), "goodbye_enabled": bool(leave), "leave_message_enabled": bool(leave)})
    await _upsert_config(int(guild.id), patch)
    invalidate_guild_config(int(guild.id))
    await interaction.followup.send("Join/leave settings updated.", ephemeral=True)


async def _save_template(interaction: discord.Interaction, *, kind: str, title: str, body: str) -> None:
    from stoney_verify.commands_ext.public_setup_group import _require_setup_permission, _upsert_config
    from stoney_verify.guild_config import invalidate_guild_config

    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    if guild is None or member is None:
        return await _send_ephemeral(interaction, "This must be used inside a server.")
    clean_title = str(title or "").strip()[:256]
    clean_body = str(body or "").strip()[:1800]
    if not clean_title or not clean_body:
        return await _send_ephemeral(interaction, "Title and body are required.")
    await _defer(interaction)
    if kind == "leave":
        patch = {"welcome_leave_title": clean_title, "welcome_leave_body": clean_body}
        label = "Leave"
    else:
        patch = {"welcome_join_title": clean_title, "welcome_join_body": clean_body}
        label = "Join"
    patch["welcome_events_updated_by_id"] = str(int(interaction.user.id))
    await _upsert_config(int(guild.id), patch)
    invalidate_guild_config(int(guild.id))
    await interaction.followup.send(f"{label} template saved. Preview below.", embed=_preview_embed(guild, member, kind=kind, cfg=patch), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


class WelcomeEventTemplateModal(discord.ui.Modal):
    def __init__(self, *, kind: str, current_title: str, current_body: str) -> None:
        self.kind = kind
        label = "Leave" if kind == "leave" else "Join"
        super().__init__(title=f"Edit {label} Message")
        self.title_input = discord.ui.TextInput(label=f"{label} title", default=current_title[:256], required=True, max_length=256)
        self.body_input = discord.ui.TextInput(label=f"{label} body", default=current_body[:1800], style=discord.TextStyle.paragraph, required=True, max_length=1800)
        self.add_item(self.title_input)
        self.add_item(self.body_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _save_template(interaction, kind=self.kind, title=str(self.title_input.value), body=str(self.body_input.value))


class JoinLeaveChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, *, kind: str) -> None:
        self.kind = kind
        label = "leave" if kind == "leave" else "join"
        super().__init__(placeholder=f"Choose channel for {label} messages...", min_values=1, max_values=1, channel_types=[discord.ChannelType.text], custom_id=f"dank_setup_welcome_events:{kind}:channel")

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        channel = self.values[0] if self.values else None
        if not isinstance(channel, discord.TextChannel):
            return await _send_ephemeral(interaction, "Pick a normal text channel.")
        await _save_event_channel(interaction, channel, kind=self.kind)


class WelcomeEventsCenterView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)
        self.add_item(JoinLeaveChannelSelect(kind="join"))
        self.add_item(JoinLeaveChannelSelect(kind="leave"))

    async def _open_modal(self, interaction: discord.Interaction, *, kind: str) -> None:
        from stoney_verify.commands_ext.public_setup_group import _require_setup_permission
        from stoney_verify.guild_config import get_guild_config

        if not await _require_setup_permission(interaction):
            return
        if interaction.guild is None:
            return await _send_ephemeral(interaction, "This must be used inside a server.")
        cfg = await get_guild_config(int(interaction.guild.id), refresh=True)
        title, body = _templates(cfg, kind=kind)
        await interaction.response.send_modal(WelcomeEventTemplateModal(kind=kind, current_title=title, current_body=body))

    @discord.ui.button(label="Edit Join", emoji="✏️", style=discord.ButtonStyle.primary, custom_id="dank_setup_welcome_events:edit_join", row=2)
    async def edit_join(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._open_modal(interaction, kind="join")

    @discord.ui.button(label="Edit Leave", emoji="✏️", style=discord.ButtonStyle.primary, custom_id="dank_setup_welcome_events:edit_leave", row=2)
    async def edit_leave(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._open_modal(interaction, kind="leave")

    @discord.ui.button(label="Preview Join", emoji="👁️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_welcome_events:preview_join", row=3)
    async def preview_join(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from stoney_verify.guild_config import get_guild_config
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if interaction.guild is None or member is None:
            return await _send_ephemeral(interaction, "This must be used inside a server.")
        cfg = await get_guild_config(int(interaction.guild.id), refresh=True)
        await _send_ephemeral(interaction, "Join preview only.", embed=_preview_embed(interaction.guild, member, kind="join", cfg=cfg), allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label="Preview Leave", emoji="👁️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_welcome_events:preview_leave", row=3)
    async def preview_leave(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from stoney_verify.guild_config import get_guild_config
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if interaction.guild is None or member is None:
            return await _send_ephemeral(interaction, "This must be used inside a server.")
        cfg = await get_guild_config(int(interaction.guild.id), refresh=True)
        await _send_ephemeral(interaction, "Leave preview only.", embed=_preview_embed(interaction.guild, member, kind="leave", cfg=cfg), allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label="Disable Join", emoji="🔕", style=discord.ButtonStyle.secondary, custom_id="dank_setup_welcome_events:disable_join", row=4)
    async def disable_join(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _set_enabled(interaction, join=False)

    @discord.ui.button(label="Disable Leave", emoji="🔕", style=discord.ButtonStyle.secondary, custom_id="dank_setup_welcome_events:disable_leave", row=4)
    async def disable_leave(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _set_enabled(interaction, leave=False)

    @discord.ui.button(label="Disable Both", emoji="⛔", style=discord.ButtonStyle.danger, custom_id="dank_setup_welcome_events:disable_both", row=4)
    async def disable_both(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _set_enabled(interaction, join=False, leave=False)


async def open_welcome_events_center(interaction: discord.Interaction) -> None:
    from stoney_verify.commands_ext.public_setup_group import _require_setup_permission
    from stoney_verify.guild_config import get_guild_config

    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await _send_ephemeral(interaction, "This must be used inside a server.")
    cfg = await get_guild_config(int(guild.id), refresh=True)
    join_enabled = _cfg_bool(cfg, "welcome_join_enabled", False) or _cfg_bool(cfg, "join_welcome_enabled", False)
    leave_enabled = _cfg_bool(cfg, "welcome_leave_enabled", False) or _cfg_bool(cfg, "goodbye_enabled", False) or _cfg_bool(cfg, "leave_message_enabled", False)
    join_channel = guild.get_channel(_cfg_int(cfg, "join_welcome_channel_id") or _cfg_int(cfg, "welcome_channel_id"))
    leave_channel = guild.get_channel(_cfg_int(cfg, "goodbye_channel_id") or _cfg_int(cfg, "leave_channel_id") or _cfg_int(cfg, "welcome_channel_id"))
    join_title, join_body = _templates(cfg, kind="join")
    leave_title, leave_body = _templates(cfg, kind="leave")

    embed = discord.Embed(title="Join / Leave Messages", description="Enable and edit optional join and leave announcements for this server from setup.", color=discord.Color.blurple(), timestamp=discord.utils.utcnow())
    embed.add_field(name="Join messages", value=f"{'Enabled' if join_enabled else 'Disabled'}\nChannel: {join_channel.mention if isinstance(join_channel, discord.TextChannel) else 'not set'}", inline=True)
    embed.add_field(name="Leave messages", value=f"{'Enabled' if leave_enabled else 'Disabled'}\nChannel: {leave_channel.mention if isinstance(leave_channel, discord.TextChannel) else 'not set'}", inline=True)
    embed.add_field(name="Current Join Template", value=f"**{join_title[:120]}**\n{join_body[:400]}", inline=False)
    embed.add_field(name="Current Leave Template", value=f"**{leave_title[:120]}**\n{leave_body[:400]}", inline=False)
    embed.add_field(name="Placeholders", value="`{server_name}` `{member}` `{username}` `{display_name}` `{member_count}`", inline=False)
    embed.add_field(name="How to use", value="Pick a channel to enable that message type. Use Edit Join/Edit Leave to customize text. The channel can be private as long as Dank Shield can post there.", inline=False)
    embed.set_footer(text="/dank setup • Feature Centers • Welcome Center • Join/Leave")
    await _send_ephemeral(interaction, embed=embed, view=WelcomeEventsCenterView(), allowed_mentions=discord.AllowedMentions.none())


__all__ = ["open_welcome_events_center", "WelcomeEventsCenterView", "JoinLeaveChannelSelect", "WelcomeEventTemplateModal"]
