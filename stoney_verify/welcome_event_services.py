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


async def _ack_update(interaction: discord.Interaction) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.defer()
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
        if isinstance(raw, bool):
            return raw
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


def _post_status(channel: Optional[discord.TextChannel]) -> str:
    if not isinstance(channel, discord.TextChannel):
        return "⚠️ Pick a channel"
    missing = _can_post(channel, channel.guild.me if isinstance(channel.guild.me, discord.Member) else None)
    if missing:
        return "⚠️ Missing: " + ", ".join(missing)
    return "✅ Ready"


def _kind_label(kind: str) -> str:
    return "Leave" if kind == "leave" else "Join"


def _join_enabled(cfg: Any) -> bool:
    return _cfg_bool(cfg, "welcome_join_enabled", False) or _cfg_bool(cfg, "join_welcome_enabled", False)


def _leave_enabled(cfg: Any) -> bool:
    return _cfg_bool(cfg, "welcome_leave_enabled", False) or _cfg_bool(cfg, "goodbye_enabled", False) or _cfg_bool(cfg, "leave_message_enabled", False)


def _join_channel(guild: discord.Guild, cfg: Any) -> Optional[discord.TextChannel]:
    channel = guild.get_channel(_cfg_int(cfg, "join_welcome_channel_id") or _cfg_int(cfg, "welcome_channel_id"))
    return channel if isinstance(channel, discord.TextChannel) else None


def _leave_channel(guild: discord.Guild, cfg: Any) -> Optional[discord.TextChannel]:
    channel = guild.get_channel(_cfg_int(cfg, "goodbye_channel_id") or _cfg_int(cfg, "leave_channel_id") or _cfg_int(cfg, "welcome_channel_id"))
    return channel if isinstance(channel, discord.TextChannel) else None


def _preview_line(cfg: Any, *, kind: str) -> str:
    title, body = _templates(cfg, kind=kind)
    title = str(title or "").replace("\n", " ").strip()
    body = str(body or "").replace("\n", " ").strip()
    text = f"**{title[:80]}**\n{body[:180]}"
    return text[:300]


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
    embed.set_footer(text="Preview only • no public message was sent")
    return embed


def _channel_options(guild: discord.Guild, *, active_id: int = 0, kind: str) -> list[discord.SelectOption]:
    rows: list[tuple[int, int, discord.SelectOption]] = []
    me = guild.me if isinstance(guild.me, discord.Member) else None
    kind_tokens = ("join", "welcome", "start", "lobby") if kind == "join" else ("leave", "goodbye", "log", "join-leave")

    for channel in list(getattr(guild, "text_channels", []) or []):
        try:
            missing = _can_post(channel, me)
            if me is not None and not channel.permissions_for(me).view_channel:
                continue

            score = 0
            name_l = str(channel.name or "").lower()
            if int(channel.id) == int(active_id):
                score += 1000
            if any(token in name_l for token in kind_tokens):
                score += 80
            if any(token in name_l for token in ("staff", "command", "cmd", "log", "welcome", "general")):
                score += 20
            if not missing:
                score += 20

            status = "ready" if not missing else "missing " + ", ".join(missing[:2])
            description = (("ACTIVE • " if int(channel.id) == int(active_id) else "") + status)[:100]
            rows.append(
                (
                    score,
                    int(getattr(channel, "position", 0) or 0),
                    discord.SelectOption(
                        label=f"#{channel.name}"[:100],
                        value=str(int(channel.id)),
                        description=description,
                    ),
                )
            )
        except Exception:
            continue

    rows.sort(key=lambda item: (-item[0], item[1]))
    options = [option for _score, _pos, option in rows[:25]]
    if not options:
        options = [
            discord.SelectOption(
                label="No usable text channels",
                value="0",
                description="Give Dank Shield access to a private staff/welcome channel.",
            )
        ]
    return options


def _build_center_embed(guild: discord.Guild, cfg: Any, *, last_action: str | None = None) -> discord.Embed:
    join_enabled = _join_enabled(cfg)
    leave_enabled = _leave_enabled(cfg)
    join_channel = _join_channel(guild, cfg)
    leave_channel = _leave_channel(guild, cfg)

    embed = discord.Embed(
        title="👋 Welcome Center",
        description=(
            "Set optional messages for people joining and leaving.\n"
            "Pick a channel first, then use the ON/OFF buttons."
        ),
        color=discord.Color.green() if (join_enabled or leave_enabled) else discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )

    embed.add_field(
        name="Join Messages",
        value=(
            f"**Status:** {'✅ ON' if join_enabled else '⚪ OFF'}\n"
            f"**Channel:** {join_channel.mention if isinstance(join_channel, discord.TextChannel) else 'not set'}\n"
            f"**Bot access:** {_post_status(join_channel)}"
        ),
        inline=False,
    )
    embed.add_field(
        name="Leave Messages",
        value=(
            f"**Status:** {'✅ ON' if leave_enabled else '⚪ OFF'}\n"
            f"**Channel:** {leave_channel.mention if isinstance(leave_channel, discord.TextChannel) else 'not set'}\n"
            f"**Bot access:** {_post_status(leave_channel)}"
        ),
        inline=False,
    )

    embed.add_field(
        name="Current Message Text",
        value=(
            f"**Join:**\n{_preview_line(cfg, kind='join')}\n\n"
            f"**Leave:**\n{_preview_line(cfg, kind='leave')}"
        ),
        inline=False,
    )

    embed.add_field(
        name="Placeholders",
        value="`{member}` ping • `{account_age}` account age • `{joined_at}` join time • `{rules_channel}` `{verify_channel}` `{support_channel}` • `{random_welcome_line}` • `{invite_code}` `{invite_link}` `{invite_owner}` `{invite_channel}`",
        inline=False,
    )

    if last_action:
        embed.add_field(name="Last action", value=last_action[:1024], inline=False)

    embed.set_footer(text="/dank setup • Welcome Center • changes update this panel live")
    return embed


async def _refresh_center(interaction: discord.Interaction, *, last_action: str | None = None) -> None:
    from stoney_verify.guild_config import get_guild_config

    guild = interaction.guild
    if guild is None:
        return await _send_ephemeral(interaction, "This must be used inside a server.")

    cfg = await get_guild_config(int(guild.id), refresh=True)
    embed = _build_center_embed(guild, cfg, last_action=last_action)
    view = WelcomeEventsCenterView(guild=guild, cfg=cfg)

    try:
        if interaction.response.is_done():
            await interaction.edit_original_response(content=None, embed=embed, view=view)
        else:
            await interaction.response.edit_message(content=None, embed=embed, view=view)
        return
    except Exception:
        pass

    await _send_ephemeral(interaction, embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none())


async def _save_event_channel(interaction: discord.Interaction, channel: discord.TextChannel, *, kind: str) -> None:
    from stoney_verify.commands_ext.public_setup_group import _require_setup_permission, _upsert_config
    from stoney_verify.guild_config import invalidate_guild_config

    if not await _require_setup_permission(interaction):
        return

    guild = interaction.guild
    if guild is None:
        return await _send_ephemeral(interaction, "This must be used inside a server.")

    await _ack_update(interaction)

    missing = _can_post(channel, guild.me if isinstance(guild.me, discord.Member) else None)
    if missing:
        return await _send_ephemeral(
            interaction,
            f"I cannot use {channel.mention} yet. Missing: {', '.join(missing)}.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    if kind == "leave":
        patch = {
            "goodbye_channel_id": str(channel.id),
            "leave_channel_id": str(channel.id),
            "welcome_leave_enabled": True,
            "goodbye_enabled": True,
            "leave_message_enabled": True,
        }
        label = "Leave"
    else:
        patch = {
            "join_welcome_channel_id": str(channel.id),
            "welcome_join_enabled": True,
            "join_welcome_enabled": True,
        }
        label = "Join"

    patch["welcome_events_updated_by_id"] = str(int(interaction.user.id))
    await _upsert_config(int(guild.id), patch)
    invalidate_guild_config(int(guild.id))
    await _refresh_center(interaction, last_action=f"✅ {label} messages are ON and will post in {channel.mention}.")


async def _set_enabled(interaction: discord.Interaction, *, join: Optional[bool] = None, leave: Optional[bool] = None) -> None:
    from stoney_verify.commands_ext.public_setup_group import _require_setup_permission, _upsert_config
    from stoney_verify.guild_config import get_guild_config, invalidate_guild_config

    if not await _require_setup_permission(interaction):
        return

    guild = interaction.guild
    if guild is None:
        return await _send_ephemeral(interaction, "This must be used inside a server.")

    cfg = await get_guild_config(int(guild.id), refresh=True)

    if join is True and not isinstance(_join_channel(guild, cfg), discord.TextChannel):
        return await _send_ephemeral(interaction, "Pick a **Join** channel first, then turn Join Messages ON.")
    if leave is True and not isinstance(_leave_channel(guild, cfg), discord.TextChannel):
        return await _send_ephemeral(interaction, "Pick a **Leave** channel first, then turn Leave Messages ON.")

    await _ack_update(interaction)

    patch: dict[str, Any] = {"welcome_events_updated_by_id": str(int(interaction.user.id))}
    actions: list[str] = []

    if join is not None:
        patch.update({"welcome_join_enabled": bool(join), "join_welcome_enabled": bool(join)})
        actions.append(f"Join Messages {'ON' if join else 'OFF'}")

    if leave is not None:
        patch.update({"welcome_leave_enabled": bool(leave), "goodbye_enabled": bool(leave), "leave_message_enabled": bool(leave)})
        actions.append(f"Leave Messages {'ON' if leave else 'OFF'}")

    await _upsert_config(int(guild.id), patch)
    invalidate_guild_config(int(guild.id))
    await _refresh_center(interaction, last_action="✅ " + " • ".join(actions))


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

    await _ack_update(interaction)

    if kind == "leave":
        patch = {"welcome_leave_title": clean_title, "welcome_leave_body": clean_body}
        label = "Leave"
    else:
        patch = {"welcome_join_title": clean_title, "welcome_join_body": clean_body}
        label = "Join"

    patch["welcome_events_updated_by_id"] = str(int(interaction.user.id))
    await _upsert_config(int(guild.id), patch)
    invalidate_guild_config(int(guild.id))

    await _send_ephemeral(
        interaction,
        f"✅ {label} message saved. Preview below.",
        embed=_preview_embed(guild, member, kind=kind, cfg=patch),
        allowed_mentions=discord.AllowedMentions.none(),
    )


class WelcomeEventTemplateModal(discord.ui.Modal):
    def __init__(self, *, kind: str, current_title: str, current_body: str) -> None:
        self.kind = kind
        label = _kind_label(kind)
        super().__init__(title=f"Edit {label} Message")
        self.title_input = discord.ui.TextInput(label=f"{label} title", default=current_title[:256], required=True, max_length=256)
        self.body_input = discord.ui.TextInput(label=f"{label} body", default=current_body[:1800], style=discord.TextStyle.paragraph, required=True, max_length=1800)
        self.add_item(self.title_input)
        self.add_item(self.body_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _save_template(interaction, kind=self.kind, title=str(self.title_input.value), body=str(self.body_input.value))


class WelcomeEventChannelSelect(discord.ui.Select):
    def __init__(self, *, guild: discord.Guild, cfg: Any, kind: str) -> None:
        self.kind = kind
        active = _leave_channel(guild, cfg) if kind == "leave" else _join_channel(guild, cfg)
        active_id = int(active.id) if isinstance(active, discord.TextChannel) else 0
        label = _kind_label(kind)
        super().__init__(
            placeholder=f"Choose {label} channel...",
            min_values=1,
            max_values=1,
            options=_channel_options(guild, active_id=active_id, kind=kind),
            custom_id=f"dank_setup_welcome_events:{kind}:channel",
            row=0 if kind == "join" else 1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        from stoney_verify.commands_ext.public_setup_group import _require_setup_permission

        if not await _require_setup_permission(interaction):
            return

        guild = interaction.guild
        if guild is None:
            return await _send_ephemeral(interaction, "This must be used inside a server.")

        channel_id = int((self.values or ["0"])[0] or 0)
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            try:
                fetched = await guild.fetch_channel(channel_id)
                channel = fetched if isinstance(fetched, discord.TextChannel) else None
            except Exception:
                channel = None

        if not isinstance(channel, discord.TextChannel):
            return await _send_ephemeral(interaction, "That channel is not available to Dank Shield. Pick a visible text channel.")

        await _save_event_channel(interaction, channel, kind=self.kind)


class WelcomeToggleButton(discord.ui.Button):
    def __init__(self, *, kind: str, enabled: bool, has_channel: bool) -> None:
        self.kind = kind
        self.enabled_state = bool(enabled)
        label = _kind_label(kind)
        if not has_channel:
            button_label = f"{label}: Pick Channel"
            style = discord.ButtonStyle.secondary
            emoji = "📍"
        elif enabled:
            button_label = f"{label}: ON"
            style = discord.ButtonStyle.success
            emoji = "✅"
        else:
            button_label = f"{label}: OFF"
            style = discord.ButtonStyle.secondary
            emoji = "⚪"

        super().__init__(
            label=button_label,
            emoji=emoji,
            style=style,
            custom_id=f"dank_setup_welcome_events:toggle_{kind}",
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        new_value = not self.enabled_state
        if self.kind == "leave":
            await _set_enabled(interaction, leave=new_value)
        else:
            await _set_enabled(interaction, join=new_value)


class WelcomeEditButton(discord.ui.Button):
    def __init__(self, *, kind: str) -> None:
        self.kind = kind
        super().__init__(
            label=f"Edit {_kind_label(kind)}",
            emoji="✏️",
            style=discord.ButtonStyle.primary,
            custom_id=f"dank_setup_welcome_events:edit_{kind}",
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        from stoney_verify.commands_ext.public_setup_group import _require_setup_permission
        from stoney_verify.guild_config import get_guild_config

        if not await _require_setup_permission(interaction):
            return
        if interaction.guild is None:
            return await _send_ephemeral(interaction, "This must be used inside a server.")

        cfg = await get_guild_config(int(interaction.guild.id), refresh=True)
        title, body = _templates(cfg, kind=self.kind)
        await interaction.response.send_modal(WelcomeEventTemplateModal(kind=self.kind, current_title=title, current_body=body))


class WelcomePreviewButton(discord.ui.Button):
    def __init__(self, *, kind: str) -> None:
        self.kind = kind
        super().__init__(
            label=f"Preview {_kind_label(kind)}",
            emoji="👁️",
            style=discord.ButtonStyle.secondary,
            custom_id=f"dank_setup_welcome_events:preview_{kind}",
            row=3,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        from stoney_verify.guild_config import get_guild_config

        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if interaction.guild is None or member is None:
            return await _send_ephemeral(interaction, "This must be used inside a server.")

        cfg = await get_guild_config(int(interaction.guild.id), refresh=True)
        await _send_ephemeral(
            interaction,
            f"{_kind_label(self.kind)} preview only. Nothing was posted publicly.",
            embed=_preview_embed(interaction.guild, member, kind=self.kind, cfg=cfg),
            allowed_mentions=discord.AllowedMentions.none(),
        )


class WelcomeDisableBothButton(discord.ui.Button):
    def __init__(self, *, disabled: bool) -> None:
        super().__init__(
            label="Turn Both OFF" if not disabled else "Both OFF",
            emoji="⏸️",
            style=discord.ButtonStyle.danger if not disabled else discord.ButtonStyle.secondary,
            custom_id="dank_setup_welcome_events:disable_both",
            row=4,
            disabled=disabled,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await _set_enabled(interaction, join=False, leave=False)


class WelcomeRefreshButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, custom_id="dank_setup_welcome_events:refresh", row=4)

    async def callback(self, interaction: discord.Interaction) -> None:
        await _ack_update(interaction)
        await _refresh_center(interaction, last_action="🔄 Refreshed.")


class WelcomeCloseButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Close", emoji="✖️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_welcome_events:close", row=4)

    async def callback(self, interaction: discord.Interaction) -> None:
        for child in self.view.children if self.view else []:
            try:
                child.disabled = True
            except Exception:
                pass
        try:
            await interaction.response.edit_message(content="Closed Welcome Center. Reopen it from `/dank setup`.", view=self.view)
        except Exception:
            await _send_ephemeral(interaction, "Closed Welcome Center.")


class WelcomeEventsCenterView(discord.ui.View):
    def __init__(self, *, guild: discord.Guild, cfg: Any) -> None:
        super().__init__(timeout=900)

        join_enabled = _join_enabled(cfg)
        leave_enabled = _leave_enabled(cfg)
        join_channel = _join_channel(guild, cfg)
        leave_channel = _leave_channel(guild, cfg)

        self.add_item(WelcomeEventChannelSelect(guild=guild, cfg=cfg, kind="join"))
        self.add_item(WelcomeEventChannelSelect(guild=guild, cfg=cfg, kind="leave"))

        self.add_item(WelcomeToggleButton(kind="join", enabled=join_enabled, has_channel=isinstance(join_channel, discord.TextChannel)))
        self.add_item(WelcomeToggleButton(kind="leave", enabled=leave_enabled, has_channel=isinstance(leave_channel, discord.TextChannel)))

        self.add_item(WelcomeEditButton(kind="join"))
        self.add_item(WelcomeEditButton(kind="leave"))

        self.add_item(WelcomePreviewButton(kind="join"))
        self.add_item(WelcomePreviewButton(kind="leave"))

        self.add_item(WelcomeDisableBothButton(disabled=not (join_enabled or leave_enabled)))
        self.add_item(WelcomeRefreshButton())
        self.add_item(WelcomeCloseButton())


async def open_welcome_events_center(interaction: discord.Interaction) -> None:
    from stoney_verify.commands_ext.public_setup_group import _require_setup_permission
    from stoney_verify.guild_config import get_guild_config

    if not await _require_setup_permission(interaction):
        return

    guild = interaction.guild
    if guild is None:
        return await _send_ephemeral(interaction, "This must be used inside a server.")

    cfg = await get_guild_config(int(guild.id), refresh=True)
    embed = _build_center_embed(guild, cfg)
    view = WelcomeEventsCenterView(guild=guild, cfg=cfg)
    await _send_ephemeral(interaction, embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none())


# Backwards-compatible export name for older guards/imports.
JoinLeaveChannelSelect = WelcomeEventChannelSelect

__all__ = [
    "open_welcome_events_center",
    "WelcomeEventsCenterView",
    "JoinLeaveChannelSelect",
    "WelcomeEventTemplateModal",
]
