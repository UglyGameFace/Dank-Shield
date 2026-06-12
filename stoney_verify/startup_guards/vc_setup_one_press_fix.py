from __future__ import annotations

"""Add a one-press VC setup fixer to the public setup screens.

The health check now correctly catches VC verification permission problems. This
small setup-only guard adds a safe button beside the setup navigation controls so
server owners can fix the common VC permission mistakes without digging through
Discord channel settings by hand.

What the button fixes:
- locks the configured VC verification voice channel from @everyone
- prevents the saved waiting/unverified role from connecting without staff
- gives Stoney the channel-level access it needs on the VC channel
- gives configured staff/control roles access to the VC channel
- gives Stoney + staff access to the saved VC queue/status text channel

It never deletes channels, roles, tickets, or messages.
"""

from typing import Any, Iterable, Optional

import discord

_PATCHED = False
CUSTOM_ID = "stoney_solid:fix_vc_permissions"


# ---------------------------------------------------------------------------
# local helpers
# ---------------------------------------------------------------------------


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _cfg_value(cfg: Any, *names: str) -> Any:
    if cfg is None:
        return None
    for name in names:
        try:
            if hasattr(cfg, "get"):
                value = cfg.get(name)  # type: ignore[attr-defined]
                if value not in (None, "", 0, "0"):
                    return value
        except Exception:
            pass
        try:
            value = getattr(cfg, name, None)
            if value not in (None, "", 0, "0"):
                return value
        except Exception:
            pass
    return None


def _cfg_int(cfg: Any, *names: str) -> int:
    return _safe_int(_cfg_value(cfg, *names), 0)


def _unique_ints(values: Iterable[Any]) -> list[int]:
    seen: set[int] = set()
    out: list[int] = []
    for value in values:
        item = _safe_int(value, 0)
        if item <= 0 or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _voice_types() -> tuple[type, ...]:
    items: list[type] = [discord.VoiceChannel]
    stage_type = getattr(discord, "StageChannel", None)
    if stage_type is not None:
        items.append(stage_type)
    return tuple(items)


def _is_voice_like(channel: Any) -> bool:
    return isinstance(channel, _voice_types())


def _channel_name(channel: Any) -> str:
    try:
        mention = getattr(channel, "mention", None)
        if mention:
            return str(mention)
    except Exception:
        pass
    try:
        return f"#{getattr(channel, 'name', 'unknown')}"
    except Exception:
        return "channel"


def _role_name(role: Any) -> str:
    try:
        mention = getattr(role, "mention", None)
        if mention:
            return str(mention)
    except Exception:
        pass
    try:
        return f"@{getattr(role, 'name', 'role')}"
    except Exception:
        return "role"


def _bot_member(guild: discord.Guild) -> Optional[discord.Member]:
    try:
        if isinstance(guild.me, discord.Member):
            return guild.me
    except Exception:
        pass
    try:
        state = getattr(guild, "_state", None)
        user = getattr(state, "user", None)
        user_id = _safe_int(getattr(user, "id", 0), 0)
        member = guild.get_member(user_id) if user_id else None
        return member if isinstance(member, discord.Member) else None
    except Exception:
        return None


def _configured_vc_channel_id(cfg: Any) -> int:
    return _cfg_int(cfg, "vc_verify_channel_id", "vc_verify_vc_id", "voice_verify_channel_id")


def _configured_waiting_role_id(cfg: Any) -> int:
    return _cfg_int(cfg, "unverified_role_id", "waiting_role_id", "pending_role_id")


def _configured_staff_role_ids(cfg: Any) -> list[int]:
    return _unique_ints(
        [
            _cfg_int(cfg, "staff_role_id"),
            _cfg_int(cfg, "ticket_staff_role_id"),
            _cfg_int(cfg, "support_role_id"),
            _cfg_int(cfg, "vc_staff_role_id"),
            _cfg_int(cfg, "server_control_role_id"),
            _cfg_int(cfg, "control_role_id"),
            _cfg_int(cfg, "perm_role_id"),
            _cfg_int(cfg, "bot_manager_role_id"),
        ]
    )


def _configured_vc_queue_text_ids(cfg: Any) -> list[int]:
    primary = _unique_ints(
        [
            _cfg_int(cfg, "vc_verify_queue_channel_id"),
            _cfg_int(cfg, "vc_queue_channel_id"),
            _cfg_int(cfg, "vc_verify_requests_channel_id"),
            _cfg_int(cfg, "vc_requests_channel_id"),
            _cfg_int(cfg, "vc_status_channel_id"),
            _cfg_int(cfg, "vc_verify_status_channel_id"),
        ]
    )
    if primary:
        return primary

    # Fallback targets are only touched when the owner has no dedicated queue
    # channel configured. This keeps the button helpful without exposing logs to
    # @everyone or changing unrelated channels first.
    return _unique_ints(
        [
            _cfg_int(cfg, "modlog_channel_id", "mod_log_channel_id", "raidlog_channel_id"),
            _cfg_int(cfg, "transcripts_channel_id", "transcript_channel_id"),
        ]
    )


async def _get_guild_config(guild: discord.Guild) -> Any:
    from stoney_verify.guild_config import get_guild_config

    return await get_guild_config(int(guild.id), refresh=True)


async def _defer(interaction: discord.Interaction) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(thinking=False)
    except Exception:
        pass


async def _send_problem(interaction: discord.Interaction, title: str, body: str) -> None:
    embed = discord.Embed(title=title, description=body[:3900], color=discord.Color.red())
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        pass


async def _set_overwrite(channel: discord.abc.GuildChannel, target: discord.abc.Snowflake, overwrite: discord.PermissionOverwrite, *, reason: str, changed: list[str], failed: list[str], label: str) -> None:
    try:
        await channel.set_permissions(target, overwrite=overwrite, reason=reason)
        changed.append(label)
    except Exception as e:
        failed.append(f"{label}: `{type(e).__name__}: {str(e)[:220]}`")


async def _run_vc_permission_fix(interaction: discord.Interaction) -> tuple[list[str], list[str]]:
    guild = interaction.guild
    if guild is None:
        return [], ["This button must be used inside a server."]

    cfg = await _get_guild_config(guild)
    bot_member = _bot_member(guild)
    if bot_member is None:
        return [], ["Stoney could not resolve itself as a member in this server."]

    vc_id = _configured_vc_channel_id(cfg)
    if vc_id <= 0:
        return [], ["No VC verification voice channel is saved. Press **Back to Setup** → **Use My Existing Server** → **Verification Channels** first."]

    vc_channel = guild.get_channel(vc_id)
    if vc_channel is None:
        try:
            vc_channel = await guild.fetch_channel(vc_id)
        except Exception:
            vc_channel = None
    if not _is_voice_like(vc_channel):
        return [], [f"Saved VC verification channel is missing or is not a voice/stage channel: `{vc_id}`."]

    changed: list[str] = []
    failed: list[str] = []
    reason = f"Dank Shield setup one-press VC permission repair by {interaction.user} ({interaction.user.id})"

    # Voice channel lock + access.
    await _set_overwrite(
        vc_channel,  # type: ignore[arg-type]
        guild.default_role,
        discord.PermissionOverwrite(view_channel=False, connect=False),
        reason=reason,
        changed=changed,
        failed=failed,
        label=f"Locked {_channel_name(vc_channel)} from @everyone",
    )

    await _set_overwrite(
        vc_channel,  # type: ignore[arg-type]
        bot_member,
        discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            speak=True,
            move_members=True,
            manage_channels=True,
        ),
        reason=reason,
        changed=changed,
        failed=failed,
        label=f"Allowed Stoney to control {_channel_name(vc_channel)}",
    )

    waiting_role_id = _configured_waiting_role_id(cfg)
    waiting_role = guild.get_role(waiting_role_id) if waiting_role_id > 0 else None
    if waiting_role is not None and not waiting_role.is_default():
        await _set_overwrite(
            vc_channel,  # type: ignore[arg-type]
            waiting_role,
            discord.PermissionOverwrite(view_channel=True, connect=False, speak=False, stream=False),
            reason=reason,
            changed=changed,
            failed=failed,
            label=f"Stopped {_role_name(waiting_role)} from connecting before staff approval",
        )

    staff_roles: list[discord.Role] = []
    for role_id in _configured_staff_role_ids(cfg):
        role = guild.get_role(role_id)
        if role is not None and not role.is_default() and role not in staff_roles:
            staff_roles.append(role)

    for role in staff_roles:
        await _set_overwrite(
            vc_channel,  # type: ignore[arg-type]
            role,
            discord.PermissionOverwrite(view_channel=True, connect=True, speak=True, move_members=True),
            reason=reason,
            changed=changed,
            failed=failed,
            label=f"Allowed staff/control role {_role_name(role)} into {_channel_name(vc_channel)}",
        )

    # Dedicated VC queue/status text channel access. This is where the staff VC
    # request panel posts, so Stoney and staff both need to see/send/read there.
    for channel_id in _configured_vc_queue_text_ids(cfg):
        text_channel = guild.get_channel(channel_id)
        if text_channel is None:
            try:
                text_channel = await guild.fetch_channel(channel_id)
            except Exception:
                text_channel = None
        if not isinstance(text_channel, discord.TextChannel):
            continue

        await _set_overwrite(
            text_channel,
            bot_member,
            discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                embed_links=True,
                attach_files=True,
            ),
            reason=reason,
            changed=changed,
            failed=failed,
            label=f"Allowed Stoney to post VC staff panels in {_channel_name(text_channel)}",
        )

        for role in staff_roles:
            await _set_overwrite(
                text_channel,
                role,
                discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
                reason=reason,
                changed=changed,
                failed=failed,
                label=f"Allowed staff/control role {_role_name(role)} to read VC panels in {_channel_name(text_channel)}",
            )

    return changed, failed


async def _handle_fix_button(interaction: discord.Interaction) -> None:
    try:
        from stoney_verify.commands_ext import public_setup_solid
        from stoney_verify.commands_ext.public_setup_group import _require_setup_permission
    except Exception as e:
        return await _send_problem(interaction, "❌ VC Fix Unavailable", f"Setup modules are not loaded yet: `{e!r}`")

    if not await _require_setup_permission(interaction):
        return

    await _defer(interaction)
    guild = interaction.guild
    if guild is None:
        return await _send_problem(interaction, "❌ VC Fix Failed", "This must be used inside a server.")

    changed, failed = await _run_vc_permission_fix(interaction)

    try:
        embed = await public_setup_solid._build_health_embed(guild)
    except Exception:
        embed = discord.Embed(title="🩺 Setup Health Check", color=discord.Color.blurple())

    if changed:
        embed.add_field(
            name="🔒 One-Press VC Fix Applied",
            value="\n".join(f"✅ {item}" for item in changed[:10])[:1024],
            inline=False,
        )
    if failed:
        embed.add_field(
            name="Still Needs Manual Permission Help",
            value=(
                "Stoney tried, but Discord denied one or more permission edits. Move the Stoney bot role above the roles/channels it manages, "
                "then press this button again.\n\n" + "\n".join(f"⚠️ {item}" for item in failed[:8])
            )[:1024],
            inline=False,
        )
    if not changed and not failed:
        embed.add_field(
            name="Nothing Changed",
            value="No VC permission target was found to edit. Check that VC verification and VC queue/status are saved in setup.",
            inline=False,
        )

    try:
        await interaction.edit_original_response(embed=embed, view=public_setup_solid.SetupNavView())
    except Exception:
        try:
            await interaction.followup.send(embed=embed, view=public_setup_solid.SetupNavView(), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            pass


# ---------------------------------------------------------------------------
# setup view patch
# ---------------------------------------------------------------------------


def patch_setup_nav_with_vc_fix_button() -> bool:
    global _PATCHED
    if _PATCHED:
        return True

    try:
        from stoney_verify.commands_ext import public_setup_solid
    except Exception as e:
        try:
            print(f"⚠️ vc_setup_one_press_fix: public_setup_solid import failed: {e!r}")
        except Exception:
            pass
        return False

    setup_nav_view = getattr(public_setup_solid, "SetupNavView", None)
    if setup_nav_view is None:
        return False

    original_init = getattr(setup_nav_view, "__init__", None)
    if not callable(original_init):
        return False
    if getattr(original_init, "_stoney_vc_fix_wrapped", False):
        _PATCHED = True
        return True

    def wrapped_init(self: discord.ui.View, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        try:
            for child in list(getattr(self, "children", []) or []):
                if getattr(child, "custom_id", None) == CUSTOM_ID:
                    return

            button = discord.ui.Button(
                label="Fix VC Permissions",
                emoji="🔒",
                style=discord.ButtonStyle.success,
                custom_id=CUSTOM_ID,
                row=3,
            )
            button.callback = _handle_fix_button  # type: ignore[method-assign]
            self.add_item(button)
        except Exception as e:
            try:
                print(f"⚠️ vc_setup_one_press_fix: failed adding setup button: {e!r}")
            except Exception:
                pass

    try:
        setattr(wrapped_init, "_stoney_vc_fix_wrapped", True)
    except Exception:
        pass

    setattr(setup_nav_view, "__init__", wrapped_init)
    try:
        setattr(public_setup_solid, "BackToSetupView", setup_nav_view)
    except Exception:
        pass

    _PATCHED = True
    try:
        print("✅ vc_setup_one_press_fix: setup health VC fix button active")
    except Exception:
        pass
    return True


patch_setup_nav_with_vc_fix_button()


__all__ = ["patch_setup_nav_with_vc_fix_button"]
