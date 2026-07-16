from __future__ import annotations

"""Product-grade feature health scoreboard for /dank setup.

This guard wraps the existing setup health embed instead of registering a new
command. It adds a compact Ticket Tool-style scoreboard so server owners can see
which selected features are ready, skipped, or need exact setup work.
"""

import asyncio
from dataclasses import dataclass
from time import monotonic
from typing import Any, Mapping

import discord

_DB_CACHE_TTL_SECONDS = 45.0
_DB_TABLE_CACHE: dict[str, tuple[float, bool]] = {}
_TICKET_MENU_CACHE: dict[int, tuple[float, list[dict[str, Any]], str]] = {}


@dataclass(frozen=True)
class FeatureHealth:
    name: str
    emoji: str
    status: str
    summary: str
    fixes: tuple[str, ...] = ()
    action: str = ""

    @property
    def icon(self) -> str:
        if self.status == "ready":
            return "✅"
        if self.status == "skipped":
            return "⬜"
        if self.status == "warning":
            return "⚠️"
        return "🚫"

    @property
    def line(self) -> str:
        return f"{self.icon} {self.emoji} **{self.name}:** {self.summary}"


def _log(message: str) -> None:
    try:
        print(f"🧭 setup_feature_health_scoreboard {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ setup_feature_health_scoreboard {message}")
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


def _cfg_get(cfg: Any, key: str, default: Any = None) -> Any:
    try:
        if hasattr(cfg, "get"):
            value = cfg.get(key)
            if value is not None:
                return value
    except Exception:
        pass
    try:
        value = getattr(cfg, key, None)
        if value is not None:
            return value
    except Exception:
        pass
    try:
        if hasattr(cfg, "get"):
            for bucket in ("settings", "config", "metadata", "meta"):
                nested = cfg.get(bucket)
                if isinstance(nested, Mapping) and nested.get(key) is not None:
                    return nested.get(key)
    except Exception:
        pass
    return default


def _cfg_first(cfg: Any, *keys: str) -> Any:
    for key in keys:
        value = _cfg_get(cfg, key, None)
        if value is None or isinstance(value, bool):
            continue
        text = str(value).strip()
        if text:
            return value
    return None


def _text_channel(guild: discord.Guild, value: Any) -> discord.TextChannel | None:
    cid = _safe_int(value, 0)
    if cid <= 0:
        return None
    try:
        ch = guild.get_channel(cid)
        return ch if isinstance(ch, discord.TextChannel) else None
    except Exception:
        return None


def _category(guild: discord.Guild, value: Any) -> discord.CategoryChannel | None:
    cid = _safe_int(value, 0)
    if cid <= 0:
        return None
    try:
        ch = guild.get_channel(cid)
        return ch if isinstance(ch, discord.CategoryChannel) else None
    except Exception:
        return None


def _voice_types() -> tuple[type, ...]:
    items: list[type] = [discord.VoiceChannel]
    stage = getattr(discord, "StageChannel", None)
    if stage is not None:
        items.append(stage)
    return tuple(items)


def _voice_channel(guild: discord.Guild, value: Any) -> Any | None:
    cid = _safe_int(value, 0)
    if cid <= 0:
        return None
    try:
        ch = guild.get_channel(cid)
        return ch if isinstance(ch, _voice_types()) else None
    except Exception:
        return None


def _role(guild: discord.Guild, value: Any) -> discord.Role | None:
    rid = _safe_int(value, 0)
    if rid <= 0:
        return None
    try:
        return guild.get_role(rid)
    except Exception:
        return None


def _bot_member(guild: discord.Guild) -> discord.Member | None:
    try:
        me = getattr(guild, "me", None)
        return me if isinstance(me, discord.Member) else None
    except Exception:
        return None


def _can_manage_role(guild: discord.Guild, role: discord.Role | None) -> bool:
    if role is None:
        return False
    me = _bot_member(guild)
    if me is None:
        return False
    try:
        return bool(me.guild_permissions.manage_roles and int(me.top_role.position) > int(role.position))
    except Exception:
        return False


def _can_use_channel(
    guild: discord.Guild,
    channel: Any | None,
    *,
    manage: bool = False,
    need_files: bool = False,
) -> bool:
    me = _bot_member(guild)
    if me is None or channel is None:
        return False
    try:
        perms = channel.permissions_for(me)
        base = bool(getattr(perms, "view_channel", False))
        if isinstance(channel, discord.TextChannel):
            base = (
                base
                and bool(getattr(perms, "send_messages", False))
                and bool(getattr(perms, "read_message_history", False))
                and bool(getattr(perms, "embed_links", False))
            )
            if need_files:
                base = base and bool(getattr(perms, "attach_files", False))
        if manage:
            base = base and bool(getattr(perms, "manage_channels", False))
        return base
    except Exception:
        return False

def _verified_role_voice_access(
    guild: discord.Guild,
    cfg: Any,
    voice: Any | None,
) -> tuple[bool, str]:
    """Check approved-member access to Voice Verify."""

    verified = _role(
        guild,
        _cfg_first(
            cfg,
            "verified_role_id",
            "member_role_id",
            "approved_role_id",
        ),
    )

    if verified is None:
        return (
            False,
            "Select or create the approved-member role.",
        )

    if voice is None:
        return (
            False,
            "Select or create the Voice Verify channel.",
        )

    try:
        permissions = voice.permissions_for(verified)
    except Exception as exc:
        return (
            False,
            (
                "Could not inspect approved-member Voice "
                "Verify access: "
                f"{type(exc).__name__}."
            ),
        )

    missing = [
        label
        for attribute, label in (
            ("view_channel", "View Channel"),
            ("connect", "Connect"),
            ("speak", "Speak"),
        )
        if not bool(
            getattr(
                permissions,
                attribute,
                False,
            )
        )
    ]

    if missing:
        return (
            False,
            (
                f"Approved role {verified.mention} needs "
                + ", ".join(missing)
                + f" in {voice.mention}."
            ),
        )

    return (
        True,
        (
            f"Approved role {verified.mention} can View, "
            f"Connect, and Speak in {voice.mention}."
        ),
    )


def _db_table_readable_sync(table: str) -> bool:
    try:
        from stoney_verify.globals import get_supabase

        sb = get_supabase()
        if sb is None:
            return False
        sb.table(table).select("*").limit(1).execute()
        return True
    except Exception:
        return False


async def _db_table_readable(table: str) -> bool:
    now = monotonic()
    cached = _DB_TABLE_CACHE.get(str(table))
    if cached and now - cached[0] <= _DB_CACHE_TTL_SECONDS:
        return bool(cached[1])
    ok = bool(await asyncio.to_thread(_db_table_readable_sync, str(table)))
    _DB_TABLE_CACHE[str(table)] = (now, ok)
    return ok


def _ticket_menu_rows_sync(guild_id: int) -> tuple[list[dict[str, Any]], str]:
    try:
        from stoney_verify.globals import get_supabase

        sb = get_supabase()
        if sb is None:
            return [], "Supabase is unavailable, so ticket menu options cannot be checked."
        res = sb.table("ticket_categories").select("*").eq("guild_id", str(int(guild_id))).execute()
        rows_raw = getattr(res, "data", None) or []
        rows = [dict(row) for row in rows_raw if isinstance(row, Mapping)]
        return rows, ""
    except Exception as e:
        return [], f"Could not read ticket_categories: {type(e).__name__}: {str(e)[:220]}"


async def _ticket_menu_rows(guild_id: int) -> tuple[list[dict[str, Any]], str]:
    now = monotonic()
    cached = _TICKET_MENU_CACHE.get(int(guild_id))
    if cached and now - cached[0] <= _DB_CACHE_TTL_SECONDS:
        return list(cached[1]), str(cached[2])
    rows, error = await asyncio.to_thread(_ticket_menu_rows_sync, int(guild_id))
    _TICKET_MENU_CACHE[int(guild_id)] = (now, list(rows), str(error))
    return rows, error


async def _load_config(guild: discord.Guild) -> Any:
    try:
        from stoney_verify.guild_config import discover_runtime_guild_config

        return await discover_runtime_guild_config(guild)
    except Exception:
        try:
            from stoney_verify.guild_config import get_guild_config

            return await get_guild_config(int(guild.id), refresh=True)
        except Exception:
            return {}


async def _load_service_state(guild: discord.Guild) -> Any:
    try:
        from stoney_verify.startup_guards.setup_service_modes import load_service_state

        return await load_service_state(int(guild.id))
    except Exception:
        class Fallback:
            tickets = True
            verification = False
            voice = False
            spamguard = False
            moderation = False
        return Fallback()


async def _load_spam_state(guild: discord.Guild, service_state: Any) -> Any:
    try:
        from stoney_verify.startup_guards.setup_service_modes import _load_spam_actual_state

        return await _load_spam_actual_state(int(guild.id), service_state)
    except Exception:
        class Fallback:
            service_selected = bool(getattr(service_state, "spamguard", False))
            guard_active = False
            persisted = False
            persistence_label = "unknown"
        return Fallback()


def _ticket_score(guild: discord.Guild, cfg: Any, enabled: bool) -> FeatureHealth:
    if not enabled:
        return FeatureHealth("Tickets", "🎫", "skipped", "Skipped by selected services.")

    active_cat = _category(guild, _cfg_first(cfg, "ticket_category_id", "active_ticket_category_id", "open_ticket_category_id"))
    archive_cat = _category(guild, _cfg_first(cfg, "ticket_archive_category_id", "archive_category_id", "closed_ticket_category_id"))
    panel_channel = _text_channel(
        guild,
        _cfg_first(
            cfg,
            "ticket_panel_channel_id",
            "support_channel_id",
            "ticket_support_channel_id",
            "public_ticket_panel_channel_id",
            "panel_channel_id",
        ),
    )
    transcripts = _text_channel(guild, _cfg_first(cfg, "transcripts_channel_id", "transcript_channel_id"))
    staff_role = _role(guild, _cfg_first(cfg, "staff_role_id", "ticket_staff_role_id", "support_role_id"))

    blockers: list[str] = []
    warnings: list[str] = []

    if active_cat is None:
        blockers.append("Choose or create the active ticket category.")
    elif not _can_use_channel(guild, active_cat, manage=True):
        blockers.append("Bot needs View Channels + Manage Channels on the active ticket category.")

    if archive_cat is None:
        blockers.append("Choose or create the ticket archive category.")
    elif not _can_use_channel(guild, archive_cat, manage=True):
        blockers.append("Bot needs View Channels + Manage Channels on the ticket archive category.")

    if panel_channel is None:
        blockers.append("Choose the public ticket panel/support channel.")
    elif not _can_use_channel(guild, panel_channel):
        blockers.append("Bot cannot send/read embeds in the ticket panel/support channel.")

    if transcripts is None:
        blockers.append("Choose the transcripts channel.")
    elif not _can_use_channel(guild, transcripts, need_files=True):
        blockers.append("Bot needs send/read/embed/attach permissions in the transcripts channel.")

    if staff_role is None:
        blockers.append("Choose the ticket staff role.")
    elif staff_role.is_default():
        blockers.append("Ticket staff role cannot be @everyone.")

    if active_cat is not None and archive_cat is not None:
        try:
            if int(active_cat.id) == int(archive_cat.id):
                blockers.append("Active and archive ticket categories must be separate.")
        except Exception:
            pass

    if panel_channel is not None and active_cat is not None:
        try:
            parent = getattr(panel_channel, "category", None)
            if parent is not None and int(parent.id) == int(active_cat.id):
                warnings.append("Ticket panel/support channel is inside the active ticket category; public support panels should usually live in START HERE/public channels.")
        except Exception:
            pass

    if blockers:
        return FeatureHealth(
            "Tickets",
            "🎫",
            "blocker",
            "Needs real ticket setup pieces before ticket testing.",
            tuple(blockers[:5]),
            "Open /dank setup → Existing Server → Tickets/Panel/Logs.",
        )
    if warnings:
        return FeatureHealth(
            "Tickets",
            "🎫",
            "warning",
            "Ticket setup is usable, but layout should be cleaned up.",
            tuple(warnings[:3]),
            "Review ticket panel placement and rerun scoreboard.",
        )
    return FeatureHealth(
        "Tickets",
        "🎫",
        "ready",
        f"Panel `{panel_channel.name}`, active `{active_cat.name}`, archive `{archive_cat.name}`, staff `{staff_role.name}`, and transcripts `{transcripts.name}` are ready.",
    )


async def _ticket_menu_score(guild: discord.Guild, enabled: bool) -> FeatureHealth:
    if not enabled:
        return FeatureHealth("Ticket Menu", "🗂️", "skipped", "Skipped because Tickets are not selected.")
    rows, error = await _ticket_menu_rows(int(guild.id))
    if error:
        return FeatureHealth(
            "Ticket Menu",
            "🗂️",
            "blocker",
            "Ticket menu options could not be checked.",
            (error, "Confirm the ticket_categories table exists and Supabase is reachable."),
            "Open /dank setup → Ticket Menu Options after fixing Supabase.",
        )
    if not rows:
        return FeatureHealth(
            "Ticket Menu",
            "🗂️",
            "blocker",
            "No ticket menu options exist yet.",
            ("Press Ticket Menu Options → Create Recommended.",),
            "Open /dank setup → Ticket Menu Options → Create Recommended.",
        )
    slugs = [str(row.get("slug") or "").strip().lower() for row in rows if str(row.get("slug") or "").strip()]
    duplicate_slugs = sorted({slug for slug in slugs if slugs.count(slug) > 1})
    has_default = any(bool(row.get("is_default")) for row in rows)
    warnings: list[str] = []
    blockers: list[str] = []
    if duplicate_slugs:
        blockers.append("Duplicate ticket menu slugs: " + ", ".join(f"`{x}`" for x in duplicate_slugs[:5]))
    if not has_default:
        warnings.append("Pick one default ticket option so unclear tickets route predictably.")
    if blockers:
        return FeatureHealth("Ticket Menu", "🗂️", "blocker", "Ticket menu routing needs cleanup.", tuple(blockers[:3]), "Open /dank setup → Ticket Menu Options.")
    if warnings:
        return FeatureHealth("Ticket Menu", "🗂️", "warning", f"{len(rows)} option(s) exist, but no default is selected.", tuple(warnings[:3]), "Open /dank setup → Ticket Menu Options → Pick default.")
    return FeatureHealth("Ticket Menu", "🗂️", "ready", f"{len(rows)} option(s) exist and a default route is selected.")


def _verification_score(guild: discord.Guild, cfg: Any, enabled: bool) -> FeatureHealth:
    if not enabled:
        return FeatureHealth("Verification", "✅", "skipped", "Skipped by selected services.")
    verify_ch = _text_channel(guild, _cfg_first(cfg, "verify_channel_id", "verification_channel_id"))
    unverified = _role(guild, _cfg_first(cfg, "unverified_role_id"))
    verified = _role(guild, _cfg_first(cfg, "verified_role_id"))
    resident = _role(guild, _cfg_first(cfg, "resident_role_id", "member_role_id"))
    blockers: list[str] = []
    warnings: list[str] = []
    if verify_ch is None:
        blockers.append("Select or create a verification channel.")
    elif not _can_use_channel(guild, verify_ch):
        blockers.append("Bot cannot send/read embeds in the verification channel.")
    if unverified is None:
        blockers.append("Select or create the Unverified role.")
    elif not _can_manage_role(guild, unverified):
        blockers.append("Bot role must be above the Unverified role.")
    if verified is None:
        blockers.append("Select or create the Verified role.")
    elif not _can_manage_role(guild, verified):
        blockers.append("Bot role must be above the Verified role.")
    if resident is not None and not _can_manage_role(guild, resident):
        warnings.append("Bot role should be above the Resident/member role if that role is granted during verification.")
    if blockers:
        return FeatureHealth("Verification", "✅", "blocker", "Needs channel/role setup before approvals.", tuple(blockers[:4]), "Open /dank setup → Existing Server → Verification Roles/Channels.")
    if warnings:
        return FeatureHealth("Verification", "✅", "warning", "Core verification works, but optional member role hierarchy needs cleanup.", tuple(warnings[:3]), "Move the bot role above verification roles.")
    return FeatureHealth("Verification", "✅", "ready", f"Channel `{verify_ch.name}` and verification roles are ready.")


def _voice_score(guild: discord.Guild, cfg: Any, enabled: bool) -> FeatureHealth:
    if not enabled:
        return FeatureHealth("Voice Verify", "🎙️", "skipped", "Skipped by selected services.")
    voice = _voice_channel(guild, _cfg_first(cfg, "vc_verify_channel_id", "voice_verify_channel_id"))
    queue = _text_channel(
        guild,
        _cfg_first(
            cfg,
            "vc_verify_queue_channel_id",
            "vc_queue_channel_id",
            "vc_request_channel_id",
            "vc_verify_requests_channel_id",
            "voice_verify_requests_channel_id",
        ),
    )
    staff = _role(guild, _cfg_first(cfg, "vc_staff_role_id", "staff_role_id", "ticket_staff_role_id", "support_role_id"))
    blockers: list[str] = []
    warnings: list[str] = []
    if voice is None:
        blockers.append("Select or create the voice verification channel.")
    elif not _can_use_channel(guild, voice, manage=True):
        blockers.append("Bot needs View Channel + Manage Channels on the voice verify channel.")

    if voice is not None:
        member_access_ok, member_access_text = (
            _verified_role_voice_access(
                guild,
                cfg,
                voice,
            )
        )

        if not member_access_ok:
            blockers.append(member_access_text)
    if queue is None:
        blockers.append("Select a VC queue/staff request channel.")
    elif not _can_use_channel(guild, queue, need_files=True):
        blockers.append("Bot cannot send/read/embed/files in the VC queue channel.")
    if staff is None:
        warnings.append("Select a VC staff role so staff buttons and ticket claiming are clear.")
    if blockers:
        return FeatureHealth("Voice Verify", "🎙️", "blocker", "Needs voice channel and queue setup.", tuple(blockers[:4]), "Open /dank setup → Existing Server → Voice Verification.")
    if warnings:
        return FeatureHealth("Voice Verify", "🎙️", "warning", "Voice access works, but staff role config is incomplete.", tuple(warnings[:3]), "Select VC/ticket staff role.")
    return FeatureHealth("Voice Verify", "🎙️", "ready", f"Voice `{getattr(voice, 'name', 'configured')}` and queue `{queue.name}` are ready.")


def _logs_score(guild: discord.Guild, cfg: Any, enabled: bool) -> FeatureHealth:
    if not enabled:
        return FeatureHealth("Logs/Moderation", "🧾", "skipped", "Skipped by selected services.")
    modlog = _text_channel(guild, _cfg_first(cfg, "modlog_channel_id", "mod_log_channel_id"))
    blockers: list[str] = []
    warnings: list[str] = []
    if modlog is None:
        blockers.append("Select or create a modlog channel.")
    elif not _can_use_channel(guild, modlog):
        blockers.append("Bot cannot send/read embeds in the modlog channel.")
    me = _bot_member(guild)
    if me is None:
        warnings.append("Could not inspect bot member permissions yet.")
    else:
        perms = me.guild_permissions
        if not bool(getattr(perms, "view_audit_log", False)):
            warnings.append("View Audit Log improves staff attribution.")
        if not bool(getattr(perms, "moderate_members", False)):
            warnings.append("Moderate Members is needed for timeout-based moderation.")
    if blockers:
        return FeatureHealth("Logs/Moderation", "🧾", "blocker", "Logging channel is not usable.", tuple(blockers[:3]), "Open /dank setup → Existing Server → Logs.")
    if warnings:
        return FeatureHealth("Logs/Moderation", "🧾", "warning", "Logging works, but moderation attribution/actions are limited.", tuple(warnings[:3]), "Review bot permissions.")
    return FeatureHealth("Logs/Moderation", "🧾", "ready", f"Modlog `{modlog.name}` and key permissions are ready.")


def _spam_score(spam_state: Any, enabled: bool) -> FeatureHealth:
    if not enabled:
        return FeatureHealth("SpamGuard", "🛡️", "skipped", "Skipped by selected services.")
    active = bool(getattr(spam_state, "guard_active", False))
    persisted = bool(getattr(spam_state, "persisted", False))
    label = str(getattr(spam_state, "persistence_label", "unknown") or "unknown")
    if not active:
        return FeatureHealth("SpamGuard", "🛡️", "blocker", "Service selected, but actual guard is off.", ("Open Services → SpamGuard Setup → Enable Actual Guard.",), "Open Services → SpamGuard Setup.")
    if not persisted:
        return FeatureHealth("SpamGuard", "🛡️", "warning", f"Active, but saving is `{label}`.", ("Create/fix guild_security_settings and save SpamGuard again.",), "Fix guild_security_settings, then save SpamGuard again.")
    return FeatureHealth("SpamGuard", "🛡️", "ready", f"Actual guard is active and `{label}`.")


async def _automation_score(enabled: bool) -> FeatureHealth:
    if not enabled:
        return FeatureHealth("Automation", "🤖", "skipped", "Skipped because Tickets are not selected.")
    settings_ok, state_ok = await asyncio.gather(
        _db_table_readable("ticket_automation_settings"),
        _db_table_readable("ticket_automation_state"),
    )
    if settings_ok and state_ok:
        return FeatureHealth("Automation", "🤖", "ready", "SLA/reminder/auto-close tables are readable.")
    return FeatureHealth(
        "Automation",
        "🤖",
        "warning",
        "Ticket automation will stay limited until DB tables exist.",
        ("Run supabase/migrations/20260611_ticket_automation_tables.sql.",),
        "Run the ticket automation migration.",
    )


async def _database_score() -> FeatureHealth:
    required = ["guild_configs", "tickets", "ticket_categories"]
    optional = ["member_activity_notices", "ticket_automation_settings", "ticket_automation_state"]
    results = await asyncio.gather(*[_db_table_readable(name) for name in required + optional])
    status = dict(zip(required + optional, results))
    missing_required = [name for name in required if not status.get(name)]
    missing_optional = [name for name in optional if not status.get(name)]
    if missing_required:
        return FeatureHealth("Database", "🧱", "blocker", "Required Supabase tables are missing/unreadable.", tuple(f"Fix `{name}` table." for name in missing_required[:3]), "Run core Supabase migrations.")
    if missing_optional:
        return FeatureHealth("Database", "🧱", "warning", "Core DB works; optional feature tables are missing.", tuple(f"Create `{name}` with the 20260611 migrations." for name in missing_optional[:3]), "Run optional 20260611 migrations.")
    return FeatureHealth("Database", "🧱", "ready", "Core and optional production tables are readable.")


async def build_feature_scoreboard(guild: discord.Guild) -> list[FeatureHealth]:
    cfg, state = await asyncio.gather(_load_config(guild), _load_service_state(guild))
    spam_state = await _load_spam_state(guild, state)
    tickets = bool(getattr(state, "tickets", True))
    verification = bool(getattr(state, "verification", False))
    voice = bool(getattr(state, "voice", False))
    spamguard = bool(getattr(state, "spamguard", False))
    moderation = bool(getattr(state, "moderation", spamguard))
    ticket_menu, automation, database = await asyncio.gather(
        _ticket_menu_score(guild, tickets),
        _automation_score(tickets),
        _database_score(),
    )
    return [
        _ticket_score(guild, cfg, tickets),
        ticket_menu,
        _verification_score(guild, cfg, verification),
        _voice_score(guild, cfg, voice),
        _spam_score(spam_state, spamguard),
        _logs_score(guild, cfg, moderation),
        automation,
        database,
    ]


def _scoreboard_value(scores: list[FeatureHealth]) -> str:
    text = "\n".join(score.line for score in scores)
    return text[:1024] if text else "No feature checks ran."


def _fixes_value(scores: list[FeatureHealth]) -> str:
    lines: list[str] = []
    for score in scores:
        if score.status in {"blocker", "warning"}:
            for fix in score.fixes:
                line = f"• **{score.name}:** {fix}"
                if line not in lines:
                    lines.append(line)
    if not lines:
        return "✅ No feature-level fixes needed. Test the selected flows."
    return "\n".join(lines[:7])[:1024]


def _actions_value(scores: list[FeatureHealth]) -> str:
    lines: list[str] = []
    for score in scores:
        if score.status in {"blocker", "warning"} and score.action:
            line = f"• **{score.name}:** {score.action}"
            if line not in lines:
                lines.append(line)
    if not lines:
        return "✅ Run real-flow tests: open/close ticket, verify user, send modlog event, and test SpamGuard privately."
    return "\n".join(lines[:6])[:1024]


def _next_step(scores: list[FeatureHealth]) -> str:
    blockers = [score for score in scores if score.status == "blocker"]
    warnings = [score for score in scores if score.status == "warning"]
    if blockers:
        names = ", ".join(score.name for score in blockers[:4])
        return f"Fix blockers first: **{names}**. Then rerun Health Check."
    if warnings:
        names = ", ".join(score.name for score in warnings[:4])
        return f"Usable enough to test, but clean warnings next: **{names}**."
    return "All selected services look ready. Test ticket open/close, verification, logs, and SpamGuard in a staff-only channel."


__all__ = ["FeatureHealth", "build_feature_scoreboard"]
