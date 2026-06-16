from __future__ import annotations

"""Owned setup service for optional join and leave messages."""

from typing import Any, Mapping, Optional

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


def _clean_channel_name(value: Any) -> str:
    return str(value or "").lower().replace("_", "-").replace(" ", "-")


def _channel_by_name(guild: discord.Guild, *tokens: str) -> Optional[discord.TextChannel]:
    wanted = tuple(_clean_channel_name(token) for token in tokens if str(token or "").strip())
    if not wanted:
        return None
    for channel in list(getattr(guild, "text_channels", []) or []):
        if not isinstance(channel, discord.TextChannel):
            continue
        name = _clean_channel_name(getattr(channel, "name", ""))
        if any(token in name for token in wanted):
            return channel
    return None


def _channel_mention(guild: discord.Guild, cfg: Any, *, keys: tuple[str, ...], names: tuple[str, ...]) -> str:
    for key in keys:
        channel = guild.get_channel(_cfg_int(cfg, key, 0))
        if isinstance(channel, discord.TextChannel):
            return channel.mention
    channel = _channel_by_name(guild, *names)
    return channel.mention if isinstance(channel, discord.TextChannel) else "not set"


def _age_text(dt: Any) -> str:
    try:
        if dt is None:
            return "unknown"
        now = discord.utils.utcnow()
        if getattr(dt, "tzinfo", None) is None:
            dt = dt.replace(tzinfo=now.tzinfo)
        days = max(0, int((now - dt).total_seconds()) // 86400)
        if days >= 365:
            years = days // 365
            months = (days % 365) // 30
            return f"{years}y {months}mo" if months else f"{years}y"
        if days >= 30:
            months = days // 30
            rem = days % 30
            return f"{months}mo {rem}d" if rem else f"{months}mo"
        if days >= 1:
            return f"{days}d"
        return "today"
    except Exception:
        return "unknown"


def _discord_time(dt: Any) -> str:
    try:
        if dt is None:
            return "unknown"
        unix = int(dt.timestamp())
        return f"<t:{unix}:F> (<t:{unix}:R>)"
    except Exception:
        return "unknown"


def _server_profile(guild: discord.Guild) -> str:
    parts = [str(getattr(guild, "name", "") or "")]
    try:
        parts.extend(str(c.name or "") for c in getattr(guild, "categories", []) or [])
        parts.extend(str(c.name or "") for c in getattr(guild, "text_channels", []) or [])
    except Exception:
        pass

    haystack = " ".join(parts).lower()

    if any(word in haystack for word in ("game", "gaming", "clips", "lobby", "ranked", "xbox", "playstation", "cod", "minecraft")):
        return "gaming"
    if any(word in haystack for word in ("support", "ticket", "help", "docs", "faq")):
        return "support"
    if any(word in haystack for word in ("class", "study", "school", "course", "learn", "lesson")):
        return "education"
    if any(word in haystack for word in ("shop", "store", "client", "business", "orders", "sales")):
        return "business"
    if any(word in haystack for word in ("stream", "creator", "youtube", "twitch", "media", "art")):
        return "creator"
    return "community"


def _random_line_preview(guild: discord.Guild) -> str:
    profile = _server_profile(guild)
    lines = {
        "gaming": "Welcome in — get verified, find your channels, and enjoy the games.",
        "support": "Welcome in — check the getting-started info and open a ticket if you need help.",
        "education": "Welcome in — start with the rules, then check the learning channels.",
        "business": "Welcome — please review the rules and start-here information before posting.",
        "creator": "Welcome in — check the rules, introduce yourself, and explore the creator channels.",
        "community": "Welcome in — start with the rules, verify if needed, and enjoy the community.",
    }
    return lines.get(profile, lines["community"])


def _preview_invite_values() -> dict[str, str]:
    return {
        "invite_code": "real join only",
        "invite_link": "real join only",
        "invite_source": "real join only",
        "invite_channel": "real join only",
        "invite_owner": "real join only",
        "invite_inviter": "real join only",
        "invite_owner_id": "real join only",
        "invite_inviter_id": "real join only",
    }


def _format(text: str, member: discord.Member, *, cfg: Any | None = None) -> str:
    guild = member.guild
    pairs = {
        "server_name": str(getattr(guild, "name", "this server") or "this server"),
        "member": member.mention,
        "user": member.mention,
        "username": str(member),
        "display_name": str(getattr(member, "display_name", "") or member),
        "member_count": str(getattr(guild, "member_count", "") or ""),
        "account_age": _age_text(getattr(member, "created_at", None)),
        "joined_at": _discord_time(getattr(member, "joined_at", None)),
        "rules_channel": _channel_mention(guild, cfg, keys=("rules_channel_id", "rules_id"), names=("rules",)) if cfg is not None else "not set",
        "verify_channel": _channel_mention(guild, cfg, keys=("verify_channel_id", "verification_channel_id", "verify_id"), names=("verification", "verify")) if cfg is not None else "not set",
        "support_channel": _channel_mention(guild, cfg, keys=("support_channel_id", "ticket_channel_id", "tickets_channel_id", "support_id"), names=("support", "ticket", "help")) if cfg is not None else "not set",
        "random_welcome_line": _random_line_preview(guild),
    }
    pairs.update(_preview_invite_values())
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



def _role_from_cfg(guild: discord.Guild, cfg: Any, *keys: str) -> Optional[discord.Role]:
    for key in keys:
        try:
            rid = _cfg_int(cfg, key, 0)
            role = guild.get_role(int(rid or 0)) if int(rid or 0) > 0 else None
            if isinstance(role, discord.Role):
                return role
        except Exception:
            continue
    return None


def _join_audience_status(guild: discord.Guild, cfg: Any, channel: Optional[discord.TextChannel]) -> str:
    """Explain whether new/unverified members can see the join welcome.

    Users do not need Read Message History for old staff logs. They need the join
    welcome posted in a channel they can actually view.
    """

    if not isinstance(channel, discord.TextChannel):
        return "⚠️ Pick a public welcome/verification channel"

    try:
        unverified = _role_from_cfg(
            guild,
            cfg,
            "unverified_role_id",
            "verify_unverified_role_id",
            "verification_unverified_role_id",
            "member_unverified_role_id",
        )
        if isinstance(unverified, discord.Role):
            perms = channel.permissions_for(unverified)
            if perms.view_channel:
                return f"✅ New members can see this via {unverified.mention}"

        everyone = getattr(guild, "default_role", None)
        if isinstance(everyone, discord.Role) and channel.permissions_for(everyone).view_channel:
            return "✅ New members can see this via @everyone"

        return "⚠️ Staff/private channel — new members may not see this welcome"
    except Exception:
        return "⚠️ Could not verify new-member visibility"


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
        title=_format(title, member, cfg=cfg)[:256],
        description=_format(body, member, cfg=cfg)[:4000],
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
        title="👋 Welcome & Join/Leave Center",
        description=(
            "Separate the **member-facing join welcome** from the **private staff join/leave log**.\n"
            "Do not give Unverified message history in staff logs; pick a public welcome or verification channel for join welcomes."
        ),
        color=discord.Color.green() if (join_enabled or leave_enabled) else discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )

    embed.add_field(
        name="Member-Facing Join Welcome",
        value=(
            f"**Status:** {'✅ ON' if join_enabled else '⚪ OFF'}\n"
            f"**Channel:** {join_channel.mention if isinstance(join_channel, discord.TextChannel) else 'not set'}\n"
            f"**Bot access:** {_post_status(join_channel)}\n"
            f"**New-member visibility:** {_join_audience_status(guild, cfg, join_channel)}"
        ),
        inline=False,
    )
    embed.add_field(
        name="Private Staff Leave Log",
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
    extra = ""
    if kind != "leave":
        visibility = _join_audience_status(guild, await __import__("stoney_verify.guild_config", fromlist=["get_guild_config"]).get_guild_config(int(guild.id), refresh=True), channel)
        if visibility.startswith("⚠️"):
            extra = f"\n{visibility}"
    await _refresh_center(interaction, last_action=f"✅ {label} messages are ON and will post in {channel.mention}.{extra}")


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


def _preset_payload(profile: str) -> dict[str, str]:
    profile = str(profile or "community").strip().lower()

    presets = {
        "community": {
            "join_title": "Welcome to {server_name}, {display_name}! 👋",
            "join_body": "{random_welcome_line}\n\nStart here:\n• Read {rules_channel}\n• Verify in {verify_channel} if required\n• Need help? Go to {support_channel}\n\nAccount age: {account_age}\nInvite: {invite_source}",
            "leave_title": "{display_name} left",
            "leave_body": "{display_name} left {server_name}. Member count: {member_count}.",
        },
        "gaming": {
            "join_title": "Welcome to {server_name}, {display_name}! 🎮",
            "join_body": "{random_welcome_line}\n\nQuick start:\n• Read {rules_channel}\n• Verify in {verify_channel}\n• Need help? Use {support_channel}\n\nInvite: {invite_owner} • {invite_link}",
            "leave_title": "{display_name} left the lobby",
            "leave_body": "{display_name} left {server_name}. Member count: {member_count}.",
        },
        "support": {
            "join_title": "Welcome to {server_name}, {display_name}",
            "join_body": "Welcome in, {member}.\n\nPlease review {rules_channel}, then use {support_channel} if you need help.\n\nJoined: {joined_at}\nInvite: {invite_source}",
            "leave_title": "{display_name} left",
            "leave_body": "{display_name} left {server_name}. Member count: {member_count}.",
        },
        "creator": {
            "join_title": "Welcome, {display_name}! ✨",
            "join_body": "Welcome to {server_name}, {member}.\n\nCheck {rules_channel}, introduce yourself when ready, and use {support_channel} if you need help.\n\n{random_welcome_line}",
            "leave_title": "{display_name} left",
            "leave_body": "{display_name} left {server_name}. Member count: {member_count}.",
        },
        "business": {
            "join_title": "Welcome to {server_name}, {display_name}",
            "join_body": "Welcome, {member}.\n\nPlease review {rules_channel} and use {support_channel} if you need assistance.\n\nJoined: {joined_at}",
            "leave_title": "{display_name} left",
            "leave_body": "{display_name} left {server_name}. Member count: {member_count}.",
        },
        "education": {
            "join_title": "Welcome to {server_name}, {display_name}",
            "join_body": "Welcome, {member}.\n\nStart with {rules_channel}, then check the learning channels. Use {support_channel} if you need help.\n\n{random_welcome_line}",
            "leave_title": "{display_name} left",
            "leave_body": "{display_name} left {server_name}. Member count: {member_count}.",
        },
    }

    return dict(presets.get(profile) or presets["community"])


def _preset_options(guild: discord.Guild) -> list[discord.SelectOption]:
    recommended = _server_profile(guild)
    labels = [
        ("recommended", f"Recommended: {recommended.title()}", "Based on server/category/channel names."),
        ("community", "General Community", "Neutral default for most servers."),
        ("gaming", "Gaming / Lobby", "Friendly gaming community tone."),
        ("support", "Support Server", "Clear support/helpdesk tone."),
        ("creator", "Creator / Media", "Creator community tone."),
        ("business", "Business / Client", "Professional and simple."),
        ("education", "Education / Study", "Learning-focused community."),
    ]
    return [
        discord.SelectOption(label=label[:100], value=value, description=description[:100])
        for value, label, description in labels
    ]


async def _apply_template_preset(interaction: discord.Interaction, preset: str) -> None:
    from stoney_verify.commands_ext.public_setup_group import _require_setup_permission, _upsert_config
    from stoney_verify.guild_config import invalidate_guild_config

    if not await _require_setup_permission(interaction):
        return

    guild = interaction.guild
    if guild is None:
        return await _send_ephemeral(interaction, "This must be used inside a server.")

    selected = str(preset or "community").strip().lower()
    actual = _server_profile(guild) if selected == "recommended" else selected
    payload = _preset_payload(actual)

    await _ack_update(interaction)

    patch = {
        "welcome_join_title": payload["join_title"],
        "welcome_join_body": payload["join_body"],
        "welcome_leave_title": payload["leave_title"],
        "welcome_leave_body": payload["leave_body"],
        "welcome_events_updated_by_id": str(int(interaction.user.id)),
        "welcome_template_preset": actual,
    }

    await _upsert_config(int(guild.id), patch)
    invalidate_guild_config(int(guild.id))
    await _refresh_center(interaction, last_action=f"✅ Applied **{actual.title()}** welcome template preset. Channels and ON/OFF settings were not changed.")


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


class WelcomePresetSelect(discord.ui.Select):
    def __init__(self, *, guild: discord.Guild) -> None:
        super().__init__(
            placeholder="Choose a safe starter template...",
            min_values=1,
            max_values=1,
            options=_preset_options(guild),
            custom_id="dank_setup_welcome_events:preset_select",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        value = (self.values or ["community"])[0]
        await _apply_template_preset(interaction, value)


class WelcomePresetPickerView(discord.ui.View):
    def __init__(self, *, guild: discord.Guild) -> None:
        super().__init__(timeout=180)
        self.add_item(WelcomePresetSelect(guild=guild))


class WelcomeTemplatePresetsButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Template Presets",
            emoji="🧩",
            style=discord.ButtonStyle.secondary,
            custom_id="dank_setup_welcome_events:template_presets",
            row=3,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return await _send_ephemeral(interaction, "This must be used inside a server.")

        recommended = _server_profile(guild).title()
        await interaction.response.send_message(
            content=(
                f"🧩 Pick a safe starter template. Recommended for this server: **{recommended}**.\n"
                "This only changes the join/leave text. It does not change channels or ON/OFF toggles."
            ),
            view=WelcomePresetPickerView(guild=guild),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class WelcomePlaceholderHelpButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Placeholder Help",
            emoji="❔",
            style=discord.ButtonStyle.secondary,
            custom_id="dank_setup_welcome_events:placeholder_help",
            row=3,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="Welcome Placeholder Help",
            description=(
                "Use these inside Join/Leave titles and body text.\n"
                "Invite placeholders are exact only on real joins when Discord invite tracking is available."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Member",
            value=(
                "`{member}` pings the member\n"
                "`{username}` full username\n"
                "`{display_name}` server nickname\n"
                "`{account_age}` Discord account age\n"
                "`{joined_at}` join timestamp"
            ),
            inline=False,
        )
        embed.add_field(
            name="Server Channels",
            value=(
                "`{rules_channel}` rules channel\n"
                "`{verify_channel}` verification channel\n"
                "`{support_channel}` support/ticket channel\n"
                "`{server_name}` server name\n"
                "`{member_count}` member count"
            ),
            inline=False,
        )
        embed.add_field(
            name="Invite Attribution",
            value=(
                "`{invite_code}` invite code\n"
                "`{invite_link}` invite URL\n"
                "`{invite_owner}` invite creator when known\n"
                "`{invite_channel}` channel the invite was created for\n"
                "`{invite_source}` source + confidence when known"
            ),
            inline=False,
        )
        embed.add_field(
            name="Smart Starter Line",
            value="`{random_welcome_line}` uses a neutral line picked from safe server categories like community, gaming, support, creator, business, or education.",
            inline=False,
        )
        await _send_ephemeral(interaction, embed=embed, allowed_mentions=discord.AllowedMentions.none())


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
        self.add_item(WelcomeTemplatePresetsButton())
        self.add_item(WelcomePlaceholderHelpButton())

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
