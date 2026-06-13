from __future__ import annotations

"""Guided permission repair for /dank setup.

This aligns saved Dank Shield setup channels/categories with the permissions the
setup system expects. It is intentionally scoped: it repairs saved setup items
and ticket/archive children, preserves per-user ticket overwrites, and does not
blindly overwrite unrelated server channels.
"""

import asyncio
from dataclasses import dataclass
from typing import Any, Optional

import discord

_PATCHED = False
_ORIGINAL_BUILD_MAIN: Any = None
_GUILD_LOCKS: dict[int, asyncio.Lock] = {}


@dataclass(frozen=True)
class RepairTarget:
    channel: discord.abc.GuildChannel
    label: str
    overwrites: dict[Any, discord.PermissionOverwrite]


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _target_label(target: Any) -> str:
    try:
        mention = getattr(target, "mention", None)
        if mention:
            return str(mention)
        name = getattr(target, "name", None)
        if name:
            return f"`{name}`"
    except Exception:
        pass
    return "`unknown`"


def _channel_label(channel: Any) -> str:
    try:
        mention = getattr(channel, "mention", None)
        if mention:
            return str(mention)
        name = getattr(channel, "name", None)
        if name:
            return f"`{name}`"
    except Exception:
        pass
    return "`unknown channel`"


def _overwrite_changed(current: discord.PermissionOverwrite, expected: discord.PermissionOverwrite) -> bool:
    try:
        return current.pair() != expected.pair()
    except Exception:
        return True


def _bot_member(guild: discord.Guild) -> Optional[discord.Member]:
    try:
        return guild.me
    except Exception:
        return None


def _role_from_config(guild: discord.Guild, cfg: Any, *attrs: str) -> Optional[discord.Role]:
    for attr in attrs:
        role = guild.get_role(_safe_int(getattr(cfg, attr, 0), 0)) if cfg is not None else None
        if isinstance(role, discord.Role):
            return role
    return None


def _channel_from_config(guild: discord.Guild, cfg: Any, cls: type, *attrs: str) -> Any:
    for attr in attrs:
        channel = guild.get_channel(_safe_int(getattr(cfg, attr, 0), 0)) if cfg is not None else None
        if isinstance(channel, cls):
            return channel
    return None


async def _resolve_control_role(guild: discord.Guild, cfg: Any) -> Optional[discord.Role]:
    role = _role_from_config(guild, cfg, "server_control_role_id", "control_role_id", "perm_role_id")
    if role:
        return role
    try:
        from stoney_verify.commands_ext.public_access_control import configured_control_role_ids_for_guild

        for rid in sorted(configured_control_role_ids_for_guild(guild.id)):
            role = guild.get_role(int(rid))
            if isinstance(role, discord.Role):
                return role
    except Exception:
        pass
    return None


def _public_readonly_overwrites(
    guild: discord.Guild,
    *,
    staff_role: Optional[discord.Role],
    control_role: Optional[discord.Role],
    unverified_role: Optional[discord.Role],
    verified_role: Optional[discord.Role],
    resident_role: Optional[discord.Role],
) -> dict[Any, discord.PermissionOverwrite]:
    ow: dict[Any, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True),
    }
    me = _bot_member(guild)
    if me:
        ow[me] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            embed_links=True,
            attach_files=True,
            manage_messages=True,
        )
    for role in (unverified_role, verified_role, resident_role):
        if role and not role.is_default():
            ow[role] = discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True)
    for role in (staff_role, control_role):
        if role and not role.is_default():
            ow[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                embed_links=True,
                attach_files=True,
                manage_messages=True,
            )
    return ow


def _staff_private_overwrites(
    guild: discord.Guild,
    *,
    staff_role: Optional[discord.Role],
    control_role: Optional[discord.Role],
    unverified_role: Optional[discord.Role],
    verified_role: Optional[discord.Role],
    resident_role: Optional[discord.Role],
) -> dict[Any, discord.PermissionOverwrite]:
    ow: dict[Any, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
    }
    me = _bot_member(guild)
    if me:
        ow[me] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            embed_links=True,
            attach_files=True,
            manage_channels=True,
            manage_messages=True,
            manage_threads=True,
            send_messages_in_threads=True,
        )
    for role in (staff_role, control_role):
        if role and not role.is_default():
            ow[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                embed_links=True,
                attach_files=True,
                manage_messages=True,
                manage_threads=True,
                send_messages_in_threads=True,
            )
    for role in (unverified_role, verified_role, resident_role):
        if role and not role.is_default() and role not in {staff_role, control_role}:
            ow[role] = discord.PermissionOverwrite(view_channel=False)
    return ow


def _voice_verify_overwrites(
    guild: discord.Guild,
    *,
    staff_role: Optional[discord.Role],
    control_role: Optional[discord.Role],
    unverified_role: Optional[discord.Role],
    verified_role: Optional[discord.Role],
    resident_role: Optional[discord.Role],
) -> dict[Any, discord.PermissionOverwrite]:
    ow: dict[Any, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=True, connect=True, speak=False),
    }
    me = _bot_member(guild)
    if me:
        ow[me] = discord.PermissionOverwrite(view_channel=True, connect=True, speak=True, move_members=True, manage_channels=True)
    if unverified_role and not unverified_role.is_default():
        ow[unverified_role] = discord.PermissionOverwrite(view_channel=True, connect=True, speak=False)
    for role in (verified_role, resident_role):
        if role and not role.is_default():
            ow[role] = discord.PermissionOverwrite(view_channel=True, connect=False, speak=False)
    for role in (staff_role, control_role):
        if role and not role.is_default():
            ow[role] = discord.PermissionOverwrite(view_channel=True, connect=True, speak=True, move_members=True)
    return ow


def _add_target(targets: list[RepairTarget], seen: set[int], channel: Any, label: str, overwrites: dict[Any, discord.PermissionOverwrite]) -> None:
    if not isinstance(channel, discord.abc.GuildChannel):
        return
    cid = int(getattr(channel, "id", 0) or 0)
    if cid <= 0 or cid in seen:
        return
    seen.add(cid)
    targets.append(RepairTarget(channel=channel, label=label, overwrites=overwrites))


async def _build_targets(guild: discord.Guild) -> tuple[list[RepairTarget], list[str]]:
    from stoney_verify.guild_config import get_guild_config

    notes: list[str] = []
    cfg = await get_guild_config(guild.id, refresh=True)

    staff_role = _role_from_config(guild, cfg, "staff_role_id", "vc_staff_role_id")
    control_role = await _resolve_control_role(guild, cfg)
    unverified_role = _role_from_config(guild, cfg, "unverified_role_id")
    verified_role = _role_from_config(guild, cfg, "verified_role_id")
    resident_role = _role_from_config(guild, cfg, "resident_role_id", "member_role_id")

    if not staff_role:
        notes.append("No saved staff role found. Staff-private repairs will still lock @everyone and bot access, but staff access may be incomplete.")
    if not control_role:
        notes.append("No saved server-control role found. Control-role overwrites were skipped.")

    public_ow = _public_readonly_overwrites(
        guild,
        staff_role=staff_role,
        control_role=control_role,
        unverified_role=unverified_role,
        verified_role=verified_role,
        resident_role=resident_role,
    )
    staff_ow = _staff_private_overwrites(
        guild,
        staff_role=staff_role,
        control_role=control_role,
        unverified_role=unverified_role,
        verified_role=verified_role,
        resident_role=resident_role,
    )
    voice_ow = _voice_verify_overwrites(
        guild,
        staff_role=staff_role,
        control_role=control_role,
        unverified_role=unverified_role,
        verified_role=verified_role,
        resident_role=resident_role,
    )

    targets: list[RepairTarget] = []
    seen: set[int] = set()

    start_category = _channel_from_config(guild, cfg, discord.CategoryChannel, "start_category_id", "welcome_category_id")
    ticket_category = _channel_from_config(guild, cfg, discord.CategoryChannel, "ticket_category_id")
    archive_category = _channel_from_config(guild, cfg, discord.CategoryChannel, "ticket_archive_category_id")
    management_category = _channel_from_config(guild, cfg, discord.CategoryChannel, "management_category_id", "staff_tools_category_id")

    _add_target(targets, seen, start_category, "Start/public category", public_ow)
    _add_target(targets, seen, ticket_category, "Active tickets category", staff_ow)
    _add_target(targets, seen, archive_category, "Ticket archive category", staff_ow)
    _add_target(targets, seen, management_category, "Staff tools category", staff_ow)

    for channel, label in (
        (_channel_from_config(guild, cfg, discord.TextChannel, "welcome_channel_id"), "Welcome channel"),
        (_channel_from_config(guild, cfg, discord.TextChannel, "verify_channel_id"), "Verification start channel"),
        (_channel_from_config(guild, cfg, discord.TextChannel, "ticket_panel_channel_id", "support_channel_id"), "Ticket panel channel"),
    ):
        _add_target(targets, seen, channel, label, public_ow)

    for channel, label in (
        (_channel_from_config(guild, cfg, discord.TextChannel, "vc_verify_queue_channel_id"), "VC verification queue channel"),
        (_channel_from_config(guild, cfg, discord.TextChannel, "transcripts_channel_id"), "Transcripts channel"),
        (_channel_from_config(guild, cfg, discord.TextChannel, "modlog_channel_id"), "Modlog channel"),
        (_channel_from_config(guild, cfg, discord.TextChannel, "raidlog_channel_id"), "Raid/security log channel"),
        (_channel_from_config(guild, cfg, discord.TextChannel, "join_log_channel_id"), "Join/leave log channel"),
        (_channel_from_config(guild, cfg, discord.TextChannel, "force_verify_log_channel_id"), "Force-verify log channel"),
        (_channel_from_config(guild, cfg, discord.TextChannel, "status_channel_id", "bot_status_channel_id", "uptime_channel_id", "health_channel_id"), "Bot status channel"),
    ):
        _add_target(targets, seen, channel, label, staff_ow)

    vc_channel = _channel_from_config(guild, cfg, discord.VoiceChannel, "vc_verify_channel_id")
    _add_target(targets, seen, vc_channel, "Voice verification channel", voice_ow)

    for category, label, ow in (
        (ticket_category, "Active ticket child channel", staff_ow),
        (archive_category, "Archived ticket child channel", staff_ow),
        (management_category, "Staff tools child channel", staff_ow),
    ):
        if isinstance(category, discord.CategoryChannel):
            for child in list(getattr(category, "channels", []) or []):
                _add_target(targets, seen, child, label, ow)

    return targets, notes


async def _preview_or_apply(guild: discord.Guild, *, apply: bool) -> dict[str, Any]:
    me = _bot_member(guild)
    if me is None:
        return {"ok": False, "error": "Bot member could not be resolved.", "changed": [], "unchanged": [], "notes": []}
    if not me.guild_permissions.manage_channels:
        return {"ok": False, "error": "Bot is missing Manage Channels. I need that to repair channel permissions.", "changed": [], "unchanged": [], "notes": []}

    targets, notes = await _build_targets(guild)
    changed: list[str] = []
    unchanged: list[str] = []
    failed: list[str] = []

    for item in targets:
        channel = item.channel
        channel_changes: list[str] = []
        for target, expected in item.overwrites.items():
            try:
                current = channel.overwrites_for(target)
            except Exception:
                current = discord.PermissionOverwrite()
            if not _overwrite_changed(current, expected):
                continue
            channel_changes.append(_target_label(target))
            if apply:
                try:
                    await channel.set_permissions(
                        target,
                        overwrite=expected,
                        reason="Dank Shield setup permission repair",
                    )
                except Exception as exc:
                    failed.append(f"{_channel_label(channel)} → {_target_label(target)}: {type(exc).__name__}")
        if channel_changes:
            changed.append(f"{_channel_label(channel)} — {', '.join(channel_changes[:6])}{'…' if len(channel_changes) > 6 else ''}")
        else:
            unchanged.append(_channel_label(channel))

    return {
        "ok": not failed,
        "error": "" if not failed else "Some permission overwrites could not be repaired.",
        "changed": changed,
        "unchanged": unchanged,
        "failed": failed,
        "notes": notes,
        "target_count": len(targets),
        "applied": bool(apply),
    }


def _line_list(lines: list[str], *, empty: str = "None", limit: int = 950) -> str:
    if not lines:
        return empty
    out: list[str] = []
    total = 0
    for line in lines:
        text = str(line)
        if total + len(text) + 1 > limit:
            out.append(f"…and {len(lines) - len(out)} more")
            break
        out.append(text)
        total += len(text) + 1
    return "\n".join(out) or empty


def _result_embed(result: dict[str, Any]) -> discord.Embed:
    applied = bool(result.get("applied"))
    ok = bool(result.get("ok"))
    embed = discord.Embed(
        title=("🛠️ Permission Repair Applied" if applied else "🛠️ Permission Repair Preview"),
        description=(
            "This uses saved `/dank setup` IDs as the source of truth. It repairs configured Dank Shield channels/categories and preserves per-user ticket overwrites."
        ),
        color=discord.Color.green() if ok else discord.Color.orange(),
    )
    if result.get("error"):
        embed.add_field(name="Issue", value=str(result.get("error"))[:1024], inline=False)
    embed.add_field(name="Targets Checked", value=f"`{int(result.get('target_count') or 0)}` setup channels/categories", inline=True)
    embed.add_field(name=("Repaired" if applied else "Would Repair"), value=_line_list(list(result.get("changed") or []), empty="Nothing needed repair."), inline=False)
    embed.add_field(name="Already Aligned", value=_line_list(list(result.get("unchanged") or []), empty="None reported."), inline=False)
    if result.get("failed"):
        embed.add_field(name="Failed", value=_line_list(list(result.get("failed") or []), empty="None"), inline=False)
    if result.get("notes"):
        embed.add_field(name="Notes", value=_line_list(list(result.get("notes") or []), empty="None"), inline=False)
    embed.set_footer(text="Safe scope: saved setup items, ticket/archive/staff-tool children, bot/staff/control/public baselines.")
    return embed


async def _open_permission_repair(interaction: discord.Interaction) -> None:
    from stoney_verify.commands_ext import public_setup_solid as solid

    if not await solid._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
    await solid._safe_defer_update(interaction)
    result = await _preview_or_apply(guild, apply=False)
    await solid._edit_or_followup(interaction, embed=_result_embed(result), view=PermissionRepairConfirmView())


async def _apply_permission_repair(interaction: discord.Interaction) -> None:
    from stoney_verify.commands_ext import public_setup_solid as solid

    if not await solid._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
    lock = _GUILD_LOCKS.setdefault(int(guild.id), asyncio.Lock())
    if lock.locked():
        return await interaction.response.send_message("⏳ Permission repair is already running for this server.", ephemeral=True)
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
    except Exception:
        pass
    async with lock:
        result = await _preview_or_apply(guild, apply=True)
    await interaction.followup.send(embed=_result_embed(result), view=PermissionRepairDoneView(), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


class PermissionRepairButton(discord.ui.Button):
    def __init__(self, *, row: int = 4) -> None:
        super().__init__(label="Fix Permissions", emoji="🛠️", style=discord.ButtonStyle.secondary, custom_id="stoney_setup:permission_repair", row=row)

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        await _open_permission_repair(interaction)


class PermissionRepairConfirmView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Apply Fixes", emoji="🛠️", style=discord.ButtonStyle.success, custom_id="stoney_setup_perm:apply", row=0)
    async def apply_fixes(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _apply_permission_repair(interaction)

    @discord.ui.button(label="Preview Again", emoji="🔍", style=discord.ButtonStyle.secondary, custom_id="stoney_setup_perm:preview", row=0)
    async def preview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_permission_repair(interaction)

    @discord.ui.button(label="Back to Setup", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="stoney_setup_perm:back", row=0)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from stoney_verify.commands_ext import public_setup_solid as solid

        if not await solid._require_setup_permission(interaction):
            return
        if interaction.guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await solid._safe_defer_update(interaction)
        embed, view = await solid._build_main_setup_payload(interaction.guild)
        await solid._edit_or_followup(interaction, embed=embed, view=view)


class PermissionRepairDoneView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Back to Setup", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="stoney_setup_perm_done:back", row=0)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from stoney_verify.commands_ext import public_setup_solid as solid

        if not await solid._require_setup_permission(interaction):
            return
        if interaction.guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await solid._safe_defer_update(interaction)
        embed, view = await solid._build_main_setup_payload(interaction.guild)
        await solid._edit_or_followup(interaction, embed=embed, view=view)


def _attach_button(view: Any) -> Any:
    try:
        children = list(getattr(view, "children", []) or [])
        if any(str(getattr(child, "custom_id", "") or "") == "stoney_setup:permission_repair" for child in children):
            return view
        if len(children) >= 25:
            return view
        view.add_item(PermissionRepairButton(row=4))
    except Exception:
        pass
    return view


async def _wrapped_build_main_setup_payload(guild: discord.Guild):
    embed, view = await _ORIGINAL_BUILD_MAIN(guild)
    try:
        embed.add_field(
            name="Permission Repair",
            value="Use **🛠️ Fix Permissions** if someone changed channel overwrites after setup.",
            inline=False,
        )
    except Exception:
        pass
    return embed, _attach_button(view)


def apply() -> bool:
    global _PATCHED, _ORIGINAL_BUILD_MAIN
    if _PATCHED:
        return True
    try:
        from stoney_verify.commands_ext import public_setup_solid as solid

        original = getattr(solid, "_build_main_setup_payload", None)
        if not callable(original) or getattr(original, "_setup_permission_repair_wrapped", False):
            return False
        _ORIGINAL_BUILD_MAIN = original
        setattr(_wrapped_build_main_setup_payload, "_setup_permission_repair_wrapped", True)
        solid._build_main_setup_payload = _wrapped_build_main_setup_payload
        _PATCHED = True
        print("🛠️ setup_permission_repair_guard active; /dank setup can preview/apply saved-channel permission repairs")
        return True
    except Exception as exc:
        print(f"⚠️ setup_permission_repair_guard failed: {exc!r}")
        return False


apply()

__all__ = ["apply", "PermissionRepairButton", "PermissionRepairConfirmView"]
