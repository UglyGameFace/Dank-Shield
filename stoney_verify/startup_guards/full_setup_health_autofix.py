from __future__ import annotations

"""Full setup health audit + one-press repair for public /stoney setup.

This guard makes the setup health check behave like a real preflight, not a
light checklist.

It checks:
- saved guild config shape and missing/deleted saved IDs
- required roles and bot role hierarchy
- required categories/channels and their concrete Discord types
- bot guild-level permissions
- bot/category/channel overwrites for tickets, logs, verification, and VC
- ticket menu category rows in Supabase
- open ticket channels that the bot can see
- VC runtime alignment so the Accept VC button resolves the same channel that
  Health Check reports as valid

It also adds a one-press repair button to the Health Check screen. The repair
button fixes permission overwrites the bot is allowed to change. If Discord
blocks a fix because of role hierarchy or missing Manage Channels, the next
Health Check reports that exact manual blocker.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import discord

_PATCHED = False


# ---------------------------------------------------------------------------
# simple helpers
# ---------------------------------------------------------------------------

def _log(message: str) -> None:
    try:
        print(f"✅ full_setup_health_autofix: {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ full_setup_health_autofix: {message}")
    except Exception:
        pass


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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _mention(obj: Any, fallback: str = "`Not set`") -> str:
    try:
        mention = getattr(obj, "mention", None)
        if mention:
            return str(mention)
    except Exception:
        pass
    try:
        name = getattr(obj, "name", None)
        if name:
            return f"`{name}`"
    except Exception:
        pass
    return fallback


def _dedupe(lines: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in lines:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _field(lines: Iterable[str], *, empty: str = "✅ None", limit: int = 1024) -> str:
    src = _dedupe(lines)
    if not src:
        return empty
    out: list[str] = []
    total = 0
    for line in src:
        projected = total + len(line) + 1
        if projected > limit:
            remaining = len(src) - len(out)
            if remaining > 0:
                tail = f"…and {remaining} more"
                if total + len(tail) + 1 <= limit:
                    out.append(tail)
            break
        out.append(line)
        total += len(line) + 1
    return "\n".join(out)[:limit] or empty


def _set_ow(ow: discord.PermissionOverwrite, **values: Optional[bool]) -> discord.PermissionOverwrite:
    for name, value in values.items():
        try:
            setattr(ow, name, value)
        except Exception:
            continue
    return ow


def _voice_types() -> tuple[type, ...]:
    types: list[type] = [discord.VoiceChannel]
    stage = getattr(discord, "StageChannel", None)
    if stage is not None:
        types.append(stage)
    return tuple(types)


def _is_voice_like(channel: Any) -> bool:
    return isinstance(channel, _voice_types())


def _is_text_like(channel: Any) -> bool:
    return isinstance(channel, discord.TextChannel)


def _is_category(channel: Any) -> bool:
    return isinstance(channel, discord.CategoryChannel)


def _bot_member(guild: discord.Guild) -> Optional[discord.Member]:
    try:
        return guild.me if isinstance(guild.me, discord.Member) else None
    except Exception:
        return None


def _cfg_sources(cfg: Any) -> list[Any]:
    sources: list[Any] = []
    if cfg is not None:
        sources.append(cfg)
    for key in ("settings", "config", "metadata", "meta"):
        try:
            value = cfg.get(key) if hasattr(cfg, "get") else getattr(cfg, key, None)
            if isinstance(value, dict):
                sources.append(value)
        except Exception:
            continue
    return sources


def _cfg_value(cfg: Any, *names: str) -> Any:
    for source in _cfg_sources(cfg):
        for name in names:
            try:
                if hasattr(source, "get"):
                    value = source.get(name)  # type: ignore[attr-defined]
                    if value not in (None, "", 0, "0"):
                        return value
            except Exception:
                pass
            try:
                value = getattr(source, name, None)
                if value not in (None, "", 0, "0"):
                    return value
            except Exception:
                pass
    return None


def _cfg_int(cfg: Any, *names: str) -> int:
    return _safe_int(_cfg_value(cfg, *names), 0)


def _channel(guild: discord.Guild, channel_id: int) -> Optional[discord.abc.GuildChannel]:
    try:
        if channel_id <= 0:
            return None
        return guild.get_channel(int(channel_id))
    except Exception:
        return None


def _role(guild: discord.Guild, role_id: int) -> Optional[discord.Role]:
    try:
        if role_id <= 0:
            return None
        role = guild.get_role(int(role_id))
        return role if isinstance(role, discord.Role) else None
    except Exception:
        return None


def _perms(channel: Any, target: Any) -> Optional[discord.Permissions]:
    try:
        if channel is None or target is None or not hasattr(channel, "permissions_for"):
            return None
        return channel.permissions_for(target)
    except Exception:
        return None


def _missing(perms: Optional[discord.Permissions], names: Iterable[str]) -> list[str]:
    if perms is None:
        return ["permission check failed"]
    out: list[str] = []
    for name in names:
        try:
            if not bool(getattr(perms, name, False)):
                out.append(name.replace("_", " ").title())
        except Exception:
            out.append(name.replace("_", " ").title())
    return out


def _can_manage_role(bot_member: Optional[discord.Member], role: Optional[discord.Role]) -> bool:
    try:
        if not isinstance(bot_member, discord.Member) or not isinstance(role, discord.Role):
            return False
        if bot_member.guild_permissions.administrator:
            return True
        return bool(bot_member.guild_permissions.manage_roles and bot_member.top_role > role)
    except Exception:
        return False


def _kind_name(channel: Any) -> str:
    if isinstance(channel, discord.CategoryChannel):
        return "category"
    if isinstance(channel, discord.TextChannel):
        return "text channel"
    if _is_voice_like(channel):
        return "voice/stage channel"
    if channel is None:
        return "missing"
    return type(channel).__name__


# ---------------------------------------------------------------------------
# config keys
# ---------------------------------------------------------------------------

ROLE_KEYS: dict[str, tuple[str, ...]] = {
    "Ticket staff role": ("staff_role_id", "ticket_staff_role_id", "support_role_id"),
    "Server-control role": ("server_control_role_id", "control_role_id", "perm_role_id", "bot_manager_role_id"),
    "Unverified/waiting role": ("unverified_role_id", "waiting_role_id"),
    "Verified role": ("verified_role_id",),
    "Resident/member role": ("resident_role_id", "member_role_id"),
}

CATEGORY_KEYS: dict[str, tuple[str, ...]] = {
    "Open ticket category": ("ticket_category_id", "open_ticket_category_id"),
    "Archive/closed ticket category": ("ticket_archive_category_id", "archive_ticket_category_id", "closed_ticket_category_id"),
}

TEXT_KEYS: dict[str, tuple[str, ...]] = {
    "Ticket panel/support channel": ("ticket_panel_channel_id", "support_channel_id"),
    "Verify channel": ("verify_channel_id", "verification_channel_id"),
    "VC queue/status channel": (
        "vc_verify_queue_channel_id",
        "vc_queue_channel_id",
        "vc_verify_requests_channel_id",
        "vc_requests_channel_id",
        "vc_status_channel_id",
        "vc_verify_status_channel_id",
    ),
    "Transcript channel": ("transcripts_channel_id", "transcript_channel_id"),
    "Mod/security log channel": ("modlog_channel_id", "mod_log_channel_id", "raidlog_channel_id"),
    "Join/exit log channel": ("join_log_channel_id", "join_leave_log_channel_id", "join_exit_log_channel_id"),
    "Bot status channel": ("status_channel_id", "bot_status_channel_id"),
}

VOICE_KEYS: dict[str, tuple[str, ...]] = {
    "VC verification channel": (
        "vc_verify_channel_id",
        "vc_verify_vc_id",
        "voice_verify_channel_id",
        "voice_verification_channel_id",
        "id_verify_vc_channel_id",
    ),
}


@dataclass
class AuditResult:
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    ok: list[str] = field(default_factory=list)
    fixable: list[str] = field(default_factory=list)
    manual: list[str] = field(default_factory=list)
    cfg: Any = None

    @property
    def ready(self) -> bool:
        return not self.blockers

    def add_blocker(self, text: str, *, fixable: bool = False, manual: bool = False) -> None:
        self.blockers.append(text)
        if fixable:
            self.fixable.append(text)
        if manual:
            self.manual.append(text)

    def add_warning(self, text: str, *, fixable: bool = False, manual: bool = False) -> None:
        self.warnings.append(text)
        if fixable:
            self.fixable.append(text)
        if manual:
            self.manual.append(text)


# ---------------------------------------------------------------------------
# loaders
# ---------------------------------------------------------------------------

async def _load_cfg(guild: discord.Guild) -> Any:
    try:
        from stoney_verify.guild_config import get_guild_config

        return await get_guild_config(int(guild.id), refresh=True)
    except Exception:
        return None


async def _ticket_category_count(guild: discord.Guild) -> tuple[int, str]:
    def _sync() -> tuple[int, str]:
        try:
            from stoney_verify.globals import get_supabase

            sb = get_supabase()
            if sb is None:
                return 0, "Supabase is not available."
            res = sb.table("ticket_categories").select("slug,name,is_default,intake_type").eq("guild_id", str(int(guild.id))).execute()
            rows = getattr(res, "data", None) or []
            if not isinstance(rows, list):
                return 0, "Could not read ticket menu options."
            return len(rows), ""
        except Exception as e:
            return 0, f"Could not read ticket menu options: {type(e).__name__}: {str(e)[:180]}"

    try:
        return await asyncio.to_thread(_sync)
    except Exception as e:
        return 0, f"Could not read ticket menu options: {type(e).__name__}: {str(e)[:180]}"


# ---------------------------------------------------------------------------
# audit checks
# ---------------------------------------------------------------------------

def _audit_guild_perms(guild: discord.Guild, result: AuditResult) -> None:
    me = _bot_member(guild)
    if me is None:
        result.add_blocker("Bot member is not available in this guild.", manual=True)
        return

    perms = getattr(me, "guild_permissions", None)
    if perms is None:
        result.add_blocker("Could not inspect bot server-level permissions.", manual=True)
        return

    required = [
        "manage_channels",
        "manage_roles",
        "view_audit_log",
        "manage_messages",
        "kick_members",
        "moderate_members",
    ]
    missing = _missing(perms, required)

    if missing and not getattr(perms, "administrator", False):
        result.add_blocker(
            f"Bot is missing server-level permissions: {', '.join(missing)}.",
            manual=True,
        )
    else:
        result.ok.append("Bot has required server-level management permissions.")


def _audit_roles(guild: discord.Guild, cfg: Any, result: AuditResult) -> dict[str, Optional[discord.Role]]:
    roles: dict[str, Optional[discord.Role]] = {}
    me = _bot_member(guild)

    for label, keys in ROLE_KEYS.items():
        role_id = _cfg_int(cfg, *keys)
        role = _role(guild, role_id)
        roles[label] = role

        if role_id <= 0:
            if label == "Server-control role":
                result.add_warning(f"{label} is not saved. Setup control will rely on administrators/manage-guild users.", manual=True)
            else:
                result.add_blocker(f"{label} is not saved in setup.", manual=True)
            continue

        if role is None:
            result.add_blocker(f"{label} is saved as `{role_id}`, but that role is missing/deleted.", manual=True)
            continue

        result.ok.append(f"{label} exists: {_mention(role)}.")

        if label in {"Unverified/waiting role", "Verified role", "Resident/member role"}:
            if not _can_manage_role(me, role):
                result.add_blocker(
                    f"Bot cannot manage {label} {_mention(role)}. Move Stoney's bot role above it and make sure Manage Roles is enabled.",
                    manual=True,
                )
            else:
                result.ok.append(f"Bot can manage {label}: {_mention(role)}.")

    return roles


def _audit_categories(guild: discord.Guild, cfg: Any, result: AuditResult, roles: dict[str, Optional[discord.Role]]) -> dict[str, Optional[discord.CategoryChannel]]:
    categories: dict[str, Optional[discord.CategoryChannel]] = {}
    me = _bot_member(guild)
    staff = roles.get("Ticket staff role")

    for label, keys in CATEGORY_KEYS.items():
        cid = _cfg_int(cfg, *keys)
        raw = _channel(guild, cid)
        cat = raw if isinstance(raw, discord.CategoryChannel) else None
        categories[label] = cat

        if cid <= 0:
            result.add_blocker(f"{label} is not saved in setup.", manual=True)
            continue
        if cat is None:
            result.add_blocker(f"{label} is saved as `{cid}`, but it is {_kind_name(raw)} instead of a category.", manual=True)
            continue

        bot_missing = _missing(_perms(cat, me), ["view_channel", "manage_channels"])
        if bot_missing:
            result.add_blocker(
                f"{label} {_mention(cat)} is missing bot permissions: {', '.join(bot_missing)}.",
                fixable=True,
            )
        else:
            result.ok.append(f"{label} is controllable by Stoney: {_mention(cat)}.")

        default_perms = _perms(cat, guild.default_role)
        try:
            if default_perms and default_perms.view_channel:
                result.add_warning(f"{label} {_mention(cat)} is visible to @everyone. Ticket categories should be private.", fixable=True)
            else:
                result.ok.append(f"{label} is private from @everyone.")
        except Exception:
            result.add_warning(f"Could not verify @everyone privacy on {label}.", manual=True)

        if isinstance(staff, discord.Role):
            staff_missing = _missing(_perms(cat, staff), ["view_channel", "send_messages", "read_message_history"])
            if staff_missing:
                result.add_warning(
                    f"Ticket staff role may not fully use {label} {_mention(cat)}: {', '.join(staff_missing)}.",
                    fixable=True,
                )
            else:
                result.ok.append(f"Ticket staff can use {label}: {_mention(cat)}.")

    return categories


def _audit_text_channels(guild: discord.Guild, cfg: Any, result: AuditResult, roles: dict[str, Optional[discord.Role]]) -> dict[str, Optional[discord.TextChannel]]:
    channels: dict[str, Optional[discord.TextChannel]] = {}
    me = _bot_member(guild)
    staff = roles.get("Ticket staff role")

    for label, keys in TEXT_KEYS.items():
        cid = _cfg_int(cfg, *keys)
        raw = _channel(guild, cid)
        channel = raw if isinstance(raw, discord.TextChannel) else None
        channels[label] = channel

        required = label in {
            "Ticket panel/support channel",
            "Verify channel",
            "Transcript channel",
            "Mod/security log channel",
            "VC queue/status channel",
        }

        if cid <= 0:
            if required:
                result.add_blocker(f"{label} is not saved in setup.", manual=True)
            else:
                result.add_warning(f"{label} is not saved. This feature may be incomplete.", manual=True)
            continue

        if channel is None:
            level = result.add_blocker if required else result.add_warning
            level(f"{label} is saved as `{cid}`, but it is {_kind_name(raw)} instead of a text channel.", manual=True)
            continue

        bot_required = ["view_channel", "send_messages", "read_message_history", "embed_links"]
        if label in {"Transcript channel", "VC queue/status channel"}:
            bot_required.append("attach_files")
        missing = _missing(_perms(channel, me), bot_required)
        if missing:
            result.add_blocker(
                f"{label} {_mention(channel)} is missing bot permissions: {', '.join(missing)}.",
                fixable=True,
            )
        else:
            result.ok.append(f"{label} is writable by Stoney: {_mention(channel)}.")

        if label in {"Transcript channel", "Mod/security log channel", "Join/exit log channel", "VC queue/status channel"}:
            default_perms = _perms(channel, guild.default_role)
            try:
                if default_perms and default_perms.view_channel:
                    result.add_warning(f"{label} {_mention(channel)} is visible to @everyone. Logs/queues should be staff-only.", fixable=True)
                else:
                    result.ok.append(f"{label} is private from @everyone.")
            except Exception:
                result.add_warning(f"Could not verify @everyone privacy for {label}.", manual=True)

        if label == "VC queue/status channel" and isinstance(staff, discord.Role):
            staff_missing = _missing(_perms(channel, staff), ["view_channel", "send_messages", "read_message_history"])
            if staff_missing:
                result.add_blocker(
                    f"Ticket staff cannot use VC queue/status channel {_mention(channel)}: {', '.join(staff_missing)}.",
                    fixable=True,
                )
            else:
                result.ok.append(f"Ticket staff can use VC queue/status channel: {_mention(channel)}.")

    return channels


def _audit_voice_channels(guild: discord.Guild, cfg: Any, result: AuditResult, roles: dict[str, Optional[discord.Role]]) -> dict[str, Optional[discord.abc.GuildChannel]]:
    channels: dict[str, Optional[discord.abc.GuildChannel]] = {}
    me = _bot_member(guild)
    staff = roles.get("Ticket staff role")
    control = roles.get("Server-control role")
    unverified = roles.get("Unverified/waiting role")

    for label, keys in VOICE_KEYS.items():
        cid = _cfg_int(cfg, *keys)
        raw = _channel(guild, cid)
        vc = raw if _is_voice_like(raw) else None
        channels[label] = vc

        if cid <= 0:
            result.add_blocker(f"{label} is not saved in setup. VC Verify must stay disabled until this is set.", manual=True)
            continue
        if vc is None:
            result.add_blocker(f"{label} is saved as `{cid}`, but it is {_kind_name(raw)} instead of a voice/stage channel.", manual=True)
            continue

        bot_missing = _missing(_perms(vc, me), ["view_channel", "connect", "manage_channels", "move_members"])
        if bot_missing:
            result.add_blocker(
                f"{label} {_mention(vc)} is missing bot permissions: {', '.join(bot_missing)}.",
                fixable=True,
            )
        else:
            result.ok.append(f"{label} can be controlled by Stoney: {_mention(vc)}.")

        everyone = _perms(vc, guild.default_role)
        try:
            if everyone and (everyone.view_channel or getattr(everyone, "connect", False)):
                result.add_blocker(
                    f"{label} {_mention(vc)} is not locked. @everyone can "
                    f"{'view' if everyone.view_channel else ''}"
                    f"{' and ' if everyone.view_channel and getattr(everyone, 'connect', False) else ''}"
                    f"{'connect' if getattr(everyone, 'connect', False) else ''}.",
                    fixable=True,
                )
            else:
                result.ok.append(f"{label} is locked from @everyone.")
        except Exception:
            result.add_warning(f"Could not inspect @everyone permissions on {label}.", manual=True)

        if isinstance(unverified, discord.Role):
            waiting = _perms(vc, unverified)
            try:
                if waiting and getattr(waiting, "connect", False):
                    result.add_blocker(
                        f"Unverified/waiting role {_mention(unverified)} can connect to {label} without staff approval.",
                        fixable=True,
                    )
                else:
                    result.ok.append("Unverified/waiting role cannot connect to VC verification without staff approval.")
            except Exception:
                result.add_warning(f"Could not inspect unverified role permissions on {label}.", manual=True)

        staff_ok = False
        for role in (staff, control):
            if not isinstance(role, discord.Role):
                continue
            perms = _perms(vc, role)
            try:
                if perms and perms.view_channel and getattr(perms, "connect", False):
                    staff_ok = True
                    break
            except Exception:
                continue

        if staff_ok:
            result.ok.append("At least one staff/control role can access the VC verification channel.")
        else:
            result.add_warning("No saved staff/control role can clearly access the VC verification channel.", fixable=True)

    return channels


def _audit_runtime_alignment(
    guild: discord.Guild,
    result: AuditResult,
    voice_channels: dict[str, Optional[discord.abc.GuildChannel]],
) -> None:
    expected = voice_channels.get("VC verification channel")
    if expected is None:
        return

    for module_name in (
        "stoney_verify.commands_ext.vc_flow",
        "stoney_verify.verification_new.voice_verify",
        "stoney_verify.vc_verify",
        "stoney_verify.interaction_handlers",
    ):
        try:
            module = __import__(module_name, fromlist=["_get_vc_channel"])
            fn = getattr(module, "_get_vc_channel", None)
            if not callable(fn):
                result.add_warning(f"Runtime module `{module_name}` has no `_get_vc_channel` resolver.", manual=True)
                continue
            resolved = fn(guild)
            if hasattr(resolved, "__await__"):
                result.add_warning(f"Runtime module `{module_name}` uses an async VC resolver in a sync path; guarded fallback will be used.", manual=True)
                continue
            if resolved is None:
                result.add_blocker(f"Runtime module `{module_name}` cannot resolve the saved VC verification channel.", fixable=True)
                continue
            if not _is_voice_like(resolved):
                result.add_blocker(f"Runtime module `{module_name}` resolved {_kind_name(resolved)} instead of voice/stage.", fixable=True)
                continue
            if int(getattr(resolved, "id", 0) or 0) != int(getattr(expected, "id", 0) or 0):
                result.add_blocker(
                    f"Runtime module `{module_name}` resolves {_mention(resolved)} but setup saved {_mention(expected)}.",
                    fixable=True,
                )
            else:
                result.ok.append(f"Runtime VC resolver matches saved VC channel in `{module_name}`.")
        except Exception as e:
            result.add_warning(f"Could not inspect runtime VC resolver `{module_name}`: {type(e).__name__}: {str(e)[:140]}", manual=True)


def _audit_existing_ticket_channels(
    guild: discord.Guild,
    result: AuditResult,
    roles: dict[str, Optional[discord.Role]],
) -> None:
    me = _bot_member(guild)
    staff = roles.get("Ticket staff role")
    checked = 0

    try:
        channels = list(getattr(guild, "text_channels", []) or [])
    except Exception:
        channels = []

    for channel in channels:
        try:
            name = str(getattr(channel, "name", "") or "").lower()
            topic = str(getattr(channel, "topic", "") or "").lower()
            looks_ticket = (
                name.startswith("ticket-")
                or name.startswith("closed-")
                or "ticket_number=" in topic
                or "category=verification_issue" in topic
                or "owner_id=" in topic
            )
            if not looks_ticket:
                continue

            checked += 1
            missing = _missing(_perms(channel, me), ["view_channel", "send_messages", "read_message_history", "embed_links", "manage_channels"])
            if missing:
                result.add_warning(
                    f"Existing ticket channel {_mention(channel)} is missing bot permissions: {', '.join(missing)}.",
                    fixable=True,
                )

            if isinstance(staff, discord.Role):
                staff_missing = _missing(_perms(channel, staff), ["view_channel", "send_messages", "read_message_history"])
                if staff_missing:
                    result.add_warning(
                        f"Ticket staff cannot fully use existing ticket {_mention(channel)}: {', '.join(staff_missing)}.",
                        fixable=True,
                    )
        except Exception:
            continue

    if checked:
        result.ok.append(f"Existing ticket channels scanned: `{checked}`.")
    else:
        result.ok.append("No visible open ticket channels needed repair.")


async def run_full_audit(guild: discord.Guild) -> AuditResult:
    result = AuditResult()

    cfg = await _load_cfg(guild)
    result.cfg = cfg
    if cfg is None:
        result.add_blocker("Saved setup could not be loaded from guild config.", manual=True)
        return result

    _audit_guild_perms(guild, result)
    roles = _audit_roles(guild, cfg, result)
    _audit_categories(guild, cfg, result, roles)
    _audit_text_channels(guild, cfg, result, roles)
    voice_channels = _audit_voice_channels(guild, cfg, result, roles)
    _audit_runtime_alignment(guild, result, voice_channels)
    _audit_existing_ticket_channels(guild, result, roles)

    count, cat_error = await _ticket_category_count(guild)
    if cat_error:
        result.add_blocker(cat_error, manual=True)
    elif count <= 0:
        result.add_warning("No ticket menu options are saved. Users may only get a generic ticket path.", manual=True)
    else:
        result.ok.append(f"Ticket menu options loaded: `{count}`.")

    result.blockers = _dedupe(result.blockers)
    result.warnings = _dedupe(result.warnings)
    result.ok = _dedupe(result.ok)
    result.fixable = _dedupe(result.fixable)
    result.manual = _dedupe(result.manual)
    return result


# ---------------------------------------------------------------------------
# one-press repair
# ---------------------------------------------------------------------------

async def _set_permissions_safe(
    channel: discord.abc.GuildChannel,
    target: discord.abc.Snowflake,
    overwrite: discord.PermissionOverwrite,
    *,
    reason: str,
    changed: list[str],
    failed: list[str],
    label: str,
) -> None:
    try:
        await channel.set_permissions(target, overwrite=overwrite, reason=reason)
        changed.append(label)
    except discord.Forbidden:
        failed.append(f"{label}: Discord denied the permission edit. Move Stoney's bot role higher and make sure it has Manage Channels.")
    except Exception as e:
        failed.append(f"{label}: {type(e).__name__}: {str(e)[:180]}")


async def _repair_category(
    category: Optional[discord.CategoryChannel],
    guild: discord.Guild,
    roles: dict[str, Optional[discord.Role]],
    *,
    changed: list[str],
    failed: list[str],
) -> None:
    if not isinstance(category, discord.CategoryChannel):
        return

    bot = _bot_member(guild)
    staff = roles.get("Ticket staff role")

    await _set_permissions_safe(
        category,
        guild.default_role,
        _set_ow(discord.PermissionOverwrite(), view_channel=False),
        reason="Stoney setup one-press repair: lock ticket category",
        changed=changed,
        failed=failed,
        label=f"Locked @everyone out of {_mention(category)}",
    )

    if isinstance(bot, discord.Member):
        await _set_permissions_safe(
            category,
            bot,
            _set_ow(
                discord.PermissionOverwrite(),
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                embed_links=True,
                attach_files=True,
                manage_messages=True,
                manage_channels=True,
            ),
            reason="Stoney setup one-press repair: bot ticket category permissions",
            changed=changed,
            failed=failed,
            label=f"Repaired Stoney permissions in {_mention(category)}",
        )

    if isinstance(staff, discord.Role):
        await _set_permissions_safe(
            category,
            staff,
            _set_ow(
                discord.PermissionOverwrite(),
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                embed_links=True,
                attach_files=True,
            ),
            reason="Stoney setup one-press repair: staff ticket category permissions",
            changed=changed,
            failed=failed,
            label=f"Repaired staff permissions in {_mention(category)}",
        )


async def _repair_text(
    channel: Optional[discord.TextChannel],
    guild: discord.Guild,
    roles: dict[str, Optional[discord.Role]],
    *,
    private: bool,
    staff_needed: bool,
    changed: list[str],
    failed: list[str],
) -> None:
    if not isinstance(channel, discord.TextChannel):
        return

    bot = _bot_member(guild)
    staff = roles.get("Ticket staff role")

    if private:
        await _set_permissions_safe(
            channel,
            guild.default_role,
            _set_ow(discord.PermissionOverwrite(), view_channel=False),
            reason="Stoney setup one-press repair: lock private text channel",
            changed=changed,
            failed=failed,
            label=f"Locked @everyone out of {_mention(channel)}",
        )

    if isinstance(bot, discord.Member):
        await _set_permissions_safe(
            channel,
            bot,
            _set_ow(
                discord.PermissionOverwrite(),
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                embed_links=True,
                attach_files=True,
                manage_messages=True,
                manage_channels=True,
            ),
            reason="Stoney setup one-press repair: bot text permissions",
            changed=changed,
            failed=failed,
            label=f"Repaired Stoney text permissions in {_mention(channel)}",
        )

    if staff_needed and isinstance(staff, discord.Role):
        await _set_permissions_safe(
            channel,
            staff,
            _set_ow(
                discord.PermissionOverwrite(),
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                embed_links=True,
                attach_files=True,
            ),
            reason="Stoney setup one-press repair: staff text permissions",
            changed=changed,
            failed=failed,
            label=f"Repaired staff text permissions in {_mention(channel)}",
        )


async def _repair_vc(
    channel: Optional[discord.abc.GuildChannel],
    guild: discord.Guild,
    roles: dict[str, Optional[discord.Role]],
    *,
    changed: list[str],
    failed: list[str],
) -> None:
    if not _is_voice_like(channel):
        return

    bot = _bot_member(guild)
    staff = roles.get("Ticket staff role")
    control = roles.get("Server-control role")
    unverified = roles.get("Unverified/waiting role")

    await _set_permissions_safe(
        channel,
        guild.default_role,
        _set_ow(discord.PermissionOverwrite(), view_channel=False, connect=False),
        reason="Stoney setup one-press repair: lock VC verify from everyone",
        changed=changed,
        failed=failed,
        label=f"Locked @everyone out of {_mention(channel)}",
    )

    if isinstance(unverified, discord.Role):
        await _set_permissions_safe(
            channel,
            unverified,
            _set_ow(discord.PermissionOverwrite(), view_channel=True, connect=False, speak=False),
            reason="Stoney setup one-press repair: unverified VC cannot connect before staff approval",
            changed=changed,
            failed=failed,
            label=f"Blocked unverified role from connecting to {_mention(channel)}",
        )

    if isinstance(bot, discord.Member):
        await _set_permissions_safe(
            channel,
            bot,
            _set_ow(
                discord.PermissionOverwrite(),
                view_channel=True,
                connect=True,
                speak=True,
                stream=True,
                use_voice_activation=True,
                manage_channels=True,
                move_members=True,
            ),
            reason="Stoney setup one-press repair: bot VC permissions",
            changed=changed,
            failed=failed,
            label=f"Repaired Stoney VC permissions in {_mention(channel)}",
        )

    for role in (staff, control):
        if not isinstance(role, discord.Role):
            continue
        await _set_permissions_safe(
            channel,
            role,
            _set_ow(
                discord.PermissionOverwrite(),
                view_channel=True,
                connect=True,
                speak=True,
                stream=True,
                use_voice_activation=True,
                move_members=True,
            ),
            reason="Stoney setup one-press repair: staff/control VC permissions",
            changed=changed,
            failed=failed,
            label=f"Repaired {_mention(role)} VC access in {_mention(channel)}",
        )


async def _repair_existing_tickets(
    guild: discord.Guild,
    roles: dict[str, Optional[discord.Role]],
    *,
    changed: list[str],
    failed: list[str],
) -> None:
    bot = _bot_member(guild)
    staff = roles.get("Ticket staff role")

    try:
        channels = list(getattr(guild, "text_channels", []) or [])
    except Exception:
        channels = []

    for channel in channels:
        try:
            name = str(getattr(channel, "name", "") or "").lower()
            topic = str(getattr(channel, "topic", "") or "").lower()
            if not (
                name.startswith("ticket-")
                or name.startswith("closed-")
                or "ticket_number=" in topic
                or "owner_id=" in topic
            ):
                continue

            if isinstance(bot, discord.Member):
                await _set_permissions_safe(
                    channel,
                    bot,
                    _set_ow(
                        discord.PermissionOverwrite(),
                        view_channel=True,
                        send_messages=True,
                        read_message_history=True,
                        embed_links=True,
                        attach_files=True,
                        manage_messages=True,
                        manage_channels=True,
                    ),
                    reason="Stoney setup one-press repair: existing ticket bot permissions",
                    changed=changed,
                    failed=failed,
                    label=f"Repaired Stoney permissions in existing ticket {_mention(channel)}",
                )

            if isinstance(staff, discord.Role):
                await _set_permissions_safe(
                    channel,
                    staff,
                    _set_ow(
                        discord.PermissionOverwrite(),
                        view_channel=True,
                        send_messages=True,
                        read_message_history=True,
                        embed_links=True,
                        attach_files=True,
                    ),
                    reason="Stoney setup one-press repair: existing ticket staff permissions",
                    changed=changed,
                    failed=failed,
                    label=f"Repaired staff permissions in existing ticket {_mention(channel)}",
                )
        except Exception as e:
            failed.append(f"Existing ticket repair skipped for {_mention(channel)}: {type(e).__name__}: {str(e)[:120]}")


async def run_one_press_repair(guild: discord.Guild) -> tuple[list[str], list[str]]:
    cfg = await _load_cfg(guild)
    changed: list[str] = []
    failed: list[str] = []

    if cfg is None:
        return [], ["Saved setup could not be loaded, so there is nothing safe to repair."]

    roles = {label: _role(guild, _cfg_int(cfg, *keys)) for label, keys in ROLE_KEYS.items()}

    for _label, keys in CATEGORY_KEYS.items():
        raw = _channel(guild, _cfg_int(cfg, *keys))
        await _repair_category(
            raw if isinstance(raw, discord.CategoryChannel) else None,
            guild,
            roles,
            changed=changed,
            failed=failed,
        )

    for label, keys in TEXT_KEYS.items():
        raw = _channel(guild, _cfg_int(cfg, *keys))
        text_channel = raw if isinstance(raw, discord.TextChannel) else None
        private = label in {"Transcript channel", "Mod/security log channel", "Join/exit log channel", "VC queue/status channel"}
        staff_needed = label in {"Transcript channel", "Mod/security log channel", "Join/exit log channel", "VC queue/status channel", "Bot status channel"}
        await _repair_text(
            text_channel,
            guild,
            roles,
            private=private,
            staff_needed=staff_needed,
            changed=changed,
            failed=failed,
        )

    for _label, keys in VOICE_KEYS.items():
        raw = _channel(guild, _cfg_int(cfg, *keys))
        await _repair_vc(
            raw if _is_voice_like(raw) else None,
            guild,
            roles,
            changed=changed,
            failed=failed,
        )

    await _repair_existing_tickets(guild, roles, changed=changed, failed=failed)

    try:
        from stoney_verify.guild_config import invalidate_guild_config

        invalidate_guild_config(int(guild.id))
    except Exception:
        pass

    return _dedupe(changed), _dedupe(failed)


# ---------------------------------------------------------------------------
# embed + view patching
# ---------------------------------------------------------------------------

async def build_full_health_embed(guild: discord.Guild) -> discord.Embed:
    result = await run_full_audit(guild)

    description = (
        "✅ **Ready to test.** Health Check now matched the runtime VC/ticket paths."
        if result.ready
        else "🚫 **Fix the blockers first.** Press **Fix Detected Issues** for everything Discord allows Stoney to repair."
    )

    embed = discord.Embed(
        title="🩺 Setup Health Check",
        description=description,
        color=discord.Color.green() if result.ready else discord.Color.red(),
        timestamp=_utc_now(),
    )
    embed.add_field(name="Blockers", value=_field(result.blockers, empty="✅ None"), inline=False)
    embed.add_field(name="Warnings", value=_field(result.warnings, empty="✅ None"), inline=False)
    embed.add_field(
        name="One-Press Fix",
        value=(
            "✅ No fixable permission problems detected."
            if not result.fixable
            else _field(
                [f"• {line}" for line in result.fixable],
                empty="✅ No fixable permission problems detected.",
                limit=900,
            )
            + "\n\nPress **Fix Detected Issues** below."
        )[:1024],
        inline=False,
    )
    if result.manual:
        embed.add_field(
            name="Manual Fixes Discord Will Not Let A Bot Guarantee",
            value=_field([f"• {line}" for line in result.manual], empty="✅ None"),
            inline=False,
        )
    embed.add_field(name="Passing Checks", value=_field(result.ok, empty="No passing checks yet."), inline=False)
    embed.add_field(
        name="What This Now Guarantees",
        value=(
            "Health Check checks the saved setup **and** the same runtime paths used by ticket creation, upload ID, and VC Accept. "
            "The fix button repairs channel/category/VC overwrites. If Discord blocks a repair, the next Health Check shows the exact manual blocker."
        )[:1024],
        inline=False,
    )
    embed.set_footer(text=f"Guild {guild.id} • /stoney setup • full preflight")
    return embed


def _make_health_view(public_setup_solid: Any) -> type:
    original_view = getattr(public_setup_solid, "SetupNavView")

    class FullHealthFixView(original_view):  # type: ignore[misc, valid-type]
        def __init__(self) -> None:
            super().__init__()

            fix_button = discord.ui.Button(
                label="Fix Detected Issues",
                emoji="🛠️",
                style=discord.ButtonStyle.success,
                custom_id="stoney_full_health:fix",
                row=3,
            )
            rerun_button = discord.ui.Button(
                label="Re-run Health Check",
                emoji="🔁",
                style=discord.ButtonStyle.primary,
                custom_id="stoney_full_health:rerun",
                row=3,
            )

            async def fix_callback(interaction: discord.Interaction) -> None:
                if not await public_setup_solid._require_setup_permission(interaction):
                    return
                guild = interaction.guild
                if guild is None:
                    return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)

                try:
                    if not interaction.response.is_done():
                        await interaction.response.defer(ephemeral=True, thinking=True)
                except Exception:
                    pass

                changed, failed = await run_one_press_repair(guild)
                embed = await build_full_health_embed(guild)

                summary = []
                if changed:
                    summary.append(f"✅ Repaired `{len(changed)}` permission/overwrite item(s).")
                else:
                    summary.append("ℹ️ No permission overwrites were changed.")
                if failed:
                    summary.append(f"⚠️ `{len(failed)}` item(s) still need manual attention.")
                    embed.add_field(
                        name="Repair Attempt Results",
                        value=_field([f"• {x}" for x in failed], empty="No failures."),
                        inline=False,
                    )
                else:
                    summary.append("✅ No repair failures returned by Discord.")

                embed.description = "\n".join(summary) + "\n\n" + (embed.description or "")

                try:
                    await interaction.edit_original_response(embed=embed, view=FullHealthFixView())
                except Exception:
                    try:
                        await interaction.followup.send(embed=embed, view=FullHealthFixView(), ephemeral=True)
                    except Exception:
                        pass

            async def rerun_callback(interaction: discord.Interaction) -> None:
                if not await public_setup_solid._require_setup_permission(interaction):
                    return
                guild = interaction.guild
                if guild is None:
                    return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)

                try:
                    if not interaction.response.is_done():
                        await interaction.response.defer(ephemeral=True, thinking=False)
                except Exception:
                    pass

                embed = await build_full_health_embed(guild)
                try:
                    await interaction.edit_original_response(embed=embed, view=FullHealthFixView())
                except Exception:
                    try:
                        await interaction.followup.send(embed=embed, view=FullHealthFixView(), ephemeral=True)
                    except Exception:
                        pass

            fix_button.callback = fix_callback  # type: ignore[assignment]
            rerun_button.callback = rerun_callback  # type: ignore[assignment]
            self.add_item(fix_button)
            self.add_item(rerun_button)

    FullHealthFixView.__name__ = "FullHealthFixView"
    return FullHealthFixView


def patch_full_setup_health_autofix() -> bool:
    global _PATCHED
    if _PATCHED:
        return True

    try:
        from stoney_verify.commands_ext import public_setup_solid
    except Exception as e:
        _warn(f"public_setup_solid import failed: {e!r}")
        return False

    try:
        public_setup_solid._build_health_embed = build_full_health_embed  # type: ignore[attr-defined]
        view_cls = _make_health_view(public_setup_solid)
        public_setup_solid.SetupNavView = view_cls  # type: ignore[attr-defined]
        public_setup_solid.BackToSetupView = view_cls  # type: ignore[attr-defined]
    except Exception as e:
        _warn(f"patch failed: {e!r}")
        return False

    _PATCHED = True
    _log("full health audit + one-press repair active")
    return True


patch_full_setup_health_autofix()


__all__ = [
    "patch_full_setup_health_autofix",
    "run_full_audit",
    "run_one_press_repair",
    "build_full_health_embed",
]
