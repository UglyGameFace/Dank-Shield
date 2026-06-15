from __future__ import annotations

"""Guided permission repair for /dank setup.

This aligns saved Dank Shield setup channels/categories with the same canonical
permission policy used by Setup Health. It is intentionally scoped: it repairs
saved setup items and ticket/archive/staff-tool children, preserves per-user
ticket overwrites, and does not blindly overwrite unrelated server channels.
"""

from dataclasses import dataclass
from typing import Any, Mapping, Optional

import discord

_PATCHED = False
_ORIGINAL_BUILD_MAIN: Any = None


@dataclass(frozen=True)
class RepairTarget:
    channel: Any
    label: str
    overwrites: dict[Any, discord.PermissionOverwrite]


@dataclass(frozen=True)
class TargetSpec:
    label: str
    classes: Any
    attrs: tuple[str, ...]
    critical: bool = False


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        if not text or text.lower() in {"none", "null"}:
            return int(default)
        return int(text)
    except Exception:
        return int(default)


def _cfg_value(cfg: Any, attr: str, default: Any = 0) -> Any:
    try:
        value = getattr(cfg, attr, None)
        if value is not None:
            return value
    except Exception:
        pass
    try:
        if hasattr(cfg, "get"):
            value = cfg.get(attr)
            if value is not None:
                return value
    except Exception:
        pass
    for bucket in ("settings", "config", "metadata", "meta"):
        try:
            nested = getattr(cfg, bucket, None)
            if isinstance(nested, Mapping) and nested.get(attr) is not None:
                return nested.get(attr)
        except Exception:
            pass
        try:
            if hasattr(cfg, "get"):
                nested = cfg.get(bucket)
                if isinstance(nested, Mapping) and nested.get(attr) is not None:
                    return nested.get(attr)
        except Exception:
            pass
    return default


def _cfg_int(cfg: Any, *attrs: str) -> int:
    for attr in attrs:
        value = _safe_int(_cfg_value(cfg, attr, 0), 0)
        if value > 0:
            return value
    return 0


def _target_label(target: Any) -> str:
    try:
        from stoney_verify.services.setup_permission_policy import role_label
        return f"`{role_label(target)}`"
    except Exception:
        pass
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
        from stoney_verify.services.setup_permission_policy import bot_member
        return bot_member(guild)
    except Exception:
        try:
            return guild.me
        except Exception:
            return None


def _role_from_config(guild: discord.Guild, cfg: Any, *attrs: str) -> Optional[discord.Role]:
    role_id = _cfg_int(cfg, *attrs) if cfg is not None else 0
    role = guild.get_role(role_id) if role_id > 0 else None
    return role if isinstance(role, discord.Role) else None


def _configured_id(cfg: Any, *attrs: str) -> tuple[int, str]:
    for attr in attrs:
        value = _safe_int(_cfg_value(cfg, attr, 0), 0)
        if value > 0:
            return value, attr
    return 0, attrs[0] if attrs else "unknown"


def _channel_from_config(guild: discord.Guild, cfg: Any, cls: Any, *attrs: str) -> Any:
    channel_id, _attr = _configured_id(cfg, *attrs)
    channel = guild.get_channel(channel_id) if channel_id > 0 else None
    if channel is None:
        return None
    classes = cls if isinstance(cls, tuple) else (cls,)
    try:
        if isinstance(channel, classes):
            return channel
    except Exception:
        pass
    return None


def _voice_channel_classes() -> tuple[type, ...]:
    classes: list[type] = [discord.VoiceChannel]
    stage = getattr(discord, "StageChannel", None)
    if isinstance(stage, type):
        classes.append(stage)
    return tuple(classes)


async def _resolve_control_role(guild: discord.Guild, cfg: Any) -> Optional[discord.Role]:
    role = _role_from_config(guild, cfg, "server_control_role_id", "control_role_id", "perm_role_id", "bot_manager_role_id")
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
        ow[me] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, embed_links=True, attach_files=True, manage_messages=True)
    for role in (unverified_role, verified_role, resident_role):
        if role and not role.is_default():
            ow[role] = discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True)
    for role in (staff_role, control_role):
        if role and not role.is_default():
            ow[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, embed_links=True, attach_files=True, manage_messages=True)
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
    ow: dict[Any, discord.PermissionOverwrite] = {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
    me = _bot_member(guild)
    if me:
        ow[me] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, embed_links=True, attach_files=True, manage_channels=True, manage_messages=True, manage_threads=True, send_messages_in_threads=True)
    for role in (staff_role, control_role):
        if role and not role.is_default():
            ow[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, embed_links=True, attach_files=True, manage_messages=True, manage_threads=True, send_messages_in_threads=True)
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
    from stoney_verify.services.setup_permission_policy import vc_verification_overwrites

    return vc_verification_overwrites(guild, staff_role=staff_role, control_role=control_role, unverified_role=unverified_role, verified_role=verified_role, resident_role=resident_role)


def _can_repair_channel(channel: Any) -> bool:
    try:
        return bool(channel is not None and callable(getattr(channel, "set_permissions", None)) and int(getattr(channel, "id", 0) or 0) > 0)
    except Exception:
        return False


def _channel_manage_missing(channel: Any, me: Optional[discord.Member]) -> bool:
    try:
        if not isinstance(me, discord.Member):
            return True
        perms = channel.permissions_for(me)
        return not bool(perms.manage_channels or perms.administrator)
    except Exception:
        return True


def _add_target(targets: list[RepairTarget], seen: set[int], channel: Any, label: str, overwrites: dict[Any, discord.PermissionOverwrite]) -> None:
    if not _can_repair_channel(channel):
        return
    cid = int(getattr(channel, "id", 0) or 0)
    if cid <= 0 or cid in seen:
        return
    seen.add(cid)
    targets.append(RepairTarget(channel=channel, label=label, overwrites=overwrites))


def _add_configured_target(
    *,
    guild: discord.Guild,
    cfg: Any,
    targets: list[RepairTarget],
    seen: set[int],
    missing_mappings: list[str],
    spec: TargetSpec,
    overwrites: dict[Any, discord.PermissionOverwrite],
) -> Any:
    configured_id, configured_attr = _configured_id(cfg, *spec.attrs)
    channel = _channel_from_config(guild, cfg, spec.classes, *spec.attrs)
    if channel is not None:
        _add_target(targets, seen, channel, spec.label, overwrites)
        return channel
    if configured_id > 0:
        missing_mappings.append(f"{spec.label}: saved `{configured_attr}` points to missing/wrong-type channel `{configured_id}`")
    elif spec.critical:
        missing_mappings.append(f"{spec.label}: no saved channel/category mapping found")
    return None


async def _build_targets(guild: discord.Guild) -> tuple[list[RepairTarget], list[str], list[str], list[str]]:
    from stoney_verify.guild_config import get_guild_config

    notes: list[str] = []
    missing_mappings: list[str] = []
    manual_actions: list[str] = []
    cfg = await get_guild_config(guild.id, refresh=True)

    staff_role = _role_from_config(guild, cfg, "staff_role_id", "ticket_staff_role_id", "support_role_id", "vc_staff_role_id")
    control_role = await _resolve_control_role(guild, cfg)
    unverified_role = _role_from_config(guild, cfg, "unverified_role_id", "pending_role_id", "waiting_role_id")
    verified_role = _role_from_config(guild, cfg, "verified_role_id", "approved_role_id")
    resident_role = _role_from_config(guild, cfg, "resident_role_id", "member_role_id", "verified_role_id", "approved_role_id")

    if not staff_role:
        manual_actions.append("Save a staff/support role. Staff-private repairs can lock @everyone and bot access, but staff access may be incomplete.")
    if not control_role:
        notes.append("No saved server-control role found. Control-role overwrites were skipped.")
    if not unverified_role:
        notes.append("No saved Unverified role found. Public read-only repairs still work through @everyone.")
    if verified_role and resident_role == verified_role:
        notes.append("Using Verified as the effective Member/Resident role for permission repair.")

    public_ow = _public_readonly_overwrites(guild, staff_role=staff_role, control_role=control_role, unverified_role=unverified_role, verified_role=verified_role, resident_role=resident_role)
    staff_ow = _staff_private_overwrites(guild, staff_role=staff_role, control_role=control_role, unverified_role=unverified_role, verified_role=verified_role, resident_role=resident_role)
    voice_ow = _voice_verify_overwrites(guild, staff_role=staff_role, control_role=control_role, unverified_role=unverified_role, verified_role=verified_role, resident_role=resident_role)

    targets: list[RepairTarget] = []
    seen: set[int] = set()

    start_category = _add_configured_target(guild=guild, cfg=cfg, targets=targets, seen=seen, missing_mappings=missing_mappings, spec=TargetSpec("Start/public category", discord.CategoryChannel, ("start_category_id", "welcome_category_id", "onboarding_category_id"), True), overwrites=public_ow)
    ticket_category = _add_configured_target(guild=guild, cfg=cfg, targets=targets, seen=seen, missing_mappings=missing_mappings, spec=TargetSpec("Active tickets category", discord.CategoryChannel, ("ticket_category_id", "active_ticket_category_id", "open_ticket_category_id"), True), overwrites=staff_ow)
    archive_category = _add_configured_target(guild=guild, cfg=cfg, targets=targets, seen=seen, missing_mappings=missing_mappings, spec=TargetSpec("Ticket archive category", discord.CategoryChannel, ("ticket_archive_category_id", "archive_category_id", "closed_ticket_category_id"), False), overwrites=staff_ow)
    management_category = _add_configured_target(guild=guild, cfg=cfg, targets=targets, seen=seen, missing_mappings=missing_mappings, spec=TargetSpec("Staff tools category", discord.CategoryChannel, ("management_category_id", "staff_tools_category_id"), False), overwrites=staff_ow)

    public_specs = (
        TargetSpec("Welcome/start channel", discord.TextChannel, ("welcome_channel_id", "start_channel_id", "start_here_channel_id"), True),
        TargetSpec("Rules channel", discord.TextChannel, ("rules_channel_id",), True),
        TargetSpec("Announcements channel", discord.TextChannel, ("announcements_channel_id", "announcement_channel_id")),
        TargetSpec("Verification start channel", discord.TextChannel, ("verify_channel_id", "verification_channel_id"), True),
        TargetSpec("Ticket panel/support channel", discord.TextChannel, ("ticket_panel_channel_id", "support_channel_id", "panel_channel_id"), True),
        TargetSpec("Pronouns/roles channel", discord.TextChannel, ("pronouns_channel_id", "self_roles_channel_id", "roles_channel_id")),
        TargetSpec("Goodbye/leave channel", discord.TextChannel, ("goodbye_channel_id", "leave_channel_id", "leave_message_channel_id")),
    )
    for spec in public_specs:
        _add_configured_target(guild=guild, cfg=cfg, targets=targets, seen=seen, missing_mappings=missing_mappings, spec=spec, overwrites=public_ow)

    staff_specs = (
        TargetSpec("VC verification queue channel", discord.TextChannel, ("vc_verify_queue_channel_id", "vc_queue_channel_id", "vc_request_channel_id", "vc_verify_requests_channel_id")),
        TargetSpec("Transcripts channel", discord.TextChannel, ("transcripts_channel_id", "transcript_channel_id")),
        TargetSpec("Modlog channel", discord.TextChannel, ("modlog_channel_id", "mod_log_channel_id")),
        TargetSpec("Raid/security log channel", discord.TextChannel, ("raidlog_channel_id", "raid_log_channel_id", "security_log_channel_id")),
        TargetSpec("Protection/automod log channel", discord.TextChannel, ("automod_log_channel_id", "spam_log_channel_id", "protection_log_channel_id")),
        TargetSpec("Join/leave log channel", discord.TextChannel, ("join_log_channel_id", "join_leave_log_channel_id", "joinlog_channel_id")),
        TargetSpec("Force-verify log channel", discord.TextChannel, ("force_verify_log_channel_id", "forced_verify_log_channel_id")),
        TargetSpec("Bot status channel", discord.TextChannel, ("status_channel_id", "bot_status_channel_id", "uptime_channel_id", "health_channel_id")),
    )
    for spec in staff_specs:
        _add_configured_target(guild=guild, cfg=cfg, targets=targets, seen=seen, missing_mappings=missing_mappings, spec=spec, overwrites=staff_ow)

    vc_channel = _add_configured_target(guild=guild, cfg=cfg, targets=targets, seen=seen, missing_mappings=missing_mappings, spec=TargetSpec("Voice verification channel", _voice_channel_classes(), ("vc_verify_channel_id", "voice_verify_channel_id")), overwrites=voice_ow)
    _ = vc_channel

    for category, label, ow in (
        (ticket_category, "Active ticket child channel", staff_ow),
        (archive_category, "Archived ticket child channel", staff_ow),
        (management_category, "Staff tools child channel", staff_ow),
    ):
        if isinstance(category, discord.CategoryChannel):
            for child in list(getattr(category, "channels", []) or []):
                _add_target(targets, seen, child, label, ow)

    if not targets:
        manual_actions.append("No saved setup channels/categories were found to repair. Run Core Setup → Create Missing Defaults or Use Existing Roles/Channels first.")

    return targets, notes, missing_mappings, manual_actions


async def _preview_or_apply(guild: discord.Guild, *, apply: bool) -> dict[str, Any]:
    me = _bot_member(guild)
    if me is None:
        return {"ok": False, "error": "Bot member could not be resolved.", "changed": [], "unchanged": [], "failed": [], "notes": [], "missing_mappings": [], "manual_actions": ["Reinvite or restart Dank Shield so Discord exposes the bot member."], "target_count": 0, "applied": bool(apply)}
    if not (me.guild_permissions.manage_channels or me.guild_permissions.administrator):
        return {"ok": False, "error": "Bot is missing Manage Channels.", "changed": [], "unchanged": [], "failed": [], "notes": [], "missing_mappings": [], "manual_actions": ["Give Dank Shield Manage Channels, then rerun Preview/Fix Permissions."], "target_count": 0, "applied": bool(apply)}

    targets, notes, missing_mappings, manual_actions = await _build_targets(guild)
    changed: list[str] = []
    unchanged: list[str] = []
    failed: list[str] = []

    for item in targets:
        channel = item.channel
        if _channel_manage_missing(channel, me):
            manual_actions.append(f"{_channel_label(channel)}: bot lacks Manage Channels in this channel/category.")
            continue
        pending_labels: list[str] = []
        fixed_labels: list[str] = []
        for target, expected in item.overwrites.items():
            try:
                current = channel.overwrites_for(target)
            except Exception:
                current = discord.PermissionOverwrite()
            if not _overwrite_changed(current, expected):
                continue
            pending_labels.append(_target_label(target))
            if apply:
                try:
                    await channel.set_permissions(target, overwrite=expected, reason="Dank Shield setup permission repair")
                    fixed_labels.append(_target_label(target))
                except discord.Forbidden:
                    failed.append(f"{_channel_label(channel)} -> {_target_label(target)}: Discord denied Manage Channels")
                except Exception as exc:
                    failed.append(f"{_channel_label(channel)} -> {_target_label(target)}: {type(exc).__name__}")
        labels = fixed_labels if apply else pending_labels
        if labels:
            changed.append(f"{_channel_label(channel)} — {', '.join(labels[:6])}{'…' if len(labels) > 6 else ''}")
        elif not pending_labels:
            unchanged.append(_channel_label(channel))

    if apply:
        try:
            from stoney_verify.guild_config import get_guild_config
            from stoney_verify.setup_engine import build_setup_health_report
            cfg = await get_guild_config(guild.id, refresh=True)
            report = build_setup_health_report(guild, cfg)
            remaining = [item for item in report.findings if getattr(item, "repairable", False)]
            if remaining:
                notes.insert(0, f"Post-repair scan: {len(remaining)} repairable Setup Health finding(s) still remain. First: {remaining[0].title} — {remaining[0].observed}")
            else:
                notes.insert(0, "Post-repair scan: no repairable Setup Health findings remain.")
        except Exception as exc:
            notes.insert(0, f"Post-repair scan could not run: {type(exc).__name__}.")

    has_blockers = bool(failed or manual_actions)
    return {
        "ok": not has_blockers,
        "error": "" if not failed else "Some permission overwrites could not be repaired automatically.",
        "changed": changed,
        "unchanged": unchanged,
        "failed": failed,
        "notes": notes,
        "missing_mappings": missing_mappings,
        "manual_actions": manual_actions,
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
            "This is the setup permission truth pass. It separates auto-fixable permission drift from missing mappings and Discord-side prerequisites. "
            "Scope stays safe: configured Dank Shield channels/categories plus ticket/archive/staff-tool children."
        ),
        color=discord.Color.green() if ok else discord.Color.orange(),
    )
    if result.get("error"):
        embed.add_field(name="Issue", value=str(result.get("error"))[:1024], inline=False)
    embed.add_field(name="Targets Checked", value=f"`{int(result.get('target_count') or 0)}` setup channels/categories", inline=True)
    embed.add_field(name=("Auto-Fixed" if applied else "Auto-Fixable"), value=_line_list(list(result.get("changed") or []), empty="Nothing needs permission repair."), inline=False)
    if result.get("manual_actions"):
        embed.add_field(name="Manual / Bot Prerequisites", value=_line_list(list(result.get("manual_actions") or []), empty="None"), inline=False)
    if result.get("missing_mappings"):
        embed.add_field(name="Missing or Broken Mappings", value=_line_list(list(result.get("missing_mappings") or []), empty="None"), inline=False)
    embed.add_field(name="Already Aligned", value=_line_list(list(result.get("unchanged") or []), empty="None reported."), inline=False)
    if result.get("failed"):
        embed.add_field(name="Failed While Applying", value=_line_list(list(result.get("failed") or []), empty="None"), inline=False)
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
    from stoney_verify.operation_queue import run_interaction_exclusive

    if not await solid._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
    except Exception:
        pass

    async def job():
        return await _preview_or_apply(guild, apply=True)

    result = await run_interaction_exclusive(
        interaction=interaction,
        operation_type="setup_permission_repair",
        action_label="Setup permission repair",
        factory=job,
        fingerprint={"guild_id": int(guild.id), "scope": "saved_setup_permissions"},
        risk_level="moderate",
        concurrency_class="guild_config_write",
        concurrency_key="setup_permission_repair",
        timeout_seconds=180.0,
    )
    if result is None:
        return
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
        embed.add_field(name="Permission Repair", value="Use **🛠️ Fix Permissions** if Setup Health flags channel/category drift or someone changed overwrites after setup.", inline=False)
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
        print("🛠️ setup_permission_repair_guard active; /dank setup can preview/apply canonical permission repairs with prerequisite diagnostics")
        return True
    except Exception as exc:
        print(f"⚠️ setup_permission_repair_guard failed: {exc!r}")
        return False


apply()

__all__ = ["apply", "PermissionRepairButton", "PermissionRepairConfirmView"]
