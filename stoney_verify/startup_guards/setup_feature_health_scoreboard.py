from __future__ import annotations

"""Product-grade feature health scoreboard for /dank setup.

This guard wraps the existing setup health embed instead of registering a new
command. It adds a compact Ticket Tool-style scoreboard so server owners can see
which selected features are ready, skipped, or need exact setup work.
"""

from dataclasses import dataclass
from typing import Any, Mapping

import discord

_PATCHED = False


@dataclass(frozen=True)
class FeatureHealth:
    name: str
    emoji: str
    status: str
    summary: str
    fixes: tuple[str, ...] = ()

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


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        if value is None:
            return bool(default)
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on", "enabled"}:
            return True
        if text in {"0", "false", "no", "n", "off", "disabled"}:
            return False
        return bool(default)
    except Exception:
        return bool(default)


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


def _voice_channel(guild: discord.Guild, value: Any) -> discord.abc.GuildChannel | None:
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


def _can_use_channel(guild: discord.Guild, channel: discord.abc.GuildChannel | None, *, manage: bool = False) -> bool:
    me = _bot_member(guild)
    if me is None or channel is None:
        return False
    try:
        perms = channel.permissions_for(me)
        base = bool(getattr(perms, "view_channel", False))
        if isinstance(channel, discord.TextChannel):
            base = base and bool(getattr(perms, "send_messages", False)) and bool(getattr(perms, "embed_links", False))
        if manage:
            base = base and bool(getattr(perms, "manage_channels", False))
        return base
    except Exception:
        return False


def _db_table_readable(table: str) -> bool:
    try:
        from stoney_verify.globals import get_supabase

        sb = get_supabase()
        if sb is None:
            return False
        sb.table(table).select("*").limit(1).execute()
        return True
    except Exception:
        return False


async def _db_table_readable_async(table: str) -> bool:
    try:
        import asyncio

        return bool(await asyncio.to_thread(_db_table_readable, table))
    except Exception:
        return False


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
    cat = _category(guild, _cfg_get(cfg, "ticket_category_id"))
    transcripts = _text_channel(guild, _cfg_get(cfg, "transcripts_channel_id"))
    blockers: list[str] = []
    warnings: list[str] = []
    if cat is None:
        blockers.append("Choose or create a ticket category.")
    elif not _can_use_channel(guild, cat, manage=True):
        blockers.append("Bot needs View Channel + Manage Channels on the ticket category.")
    if transcripts is None:
        warnings.append("Select a transcripts channel for clean close/delete records.")
    elif not _can_use_channel(guild, transcripts):
        blockers.append("Bot cannot send embeds in the transcripts channel.")
    if blockers:
        return FeatureHealth("Tickets", "🎫", "blocker", "Needs setup before ticket testing.", tuple(blockers[:3]))
    if warnings:
        return FeatureHealth("Tickets", "🎫", "warning", "Usable, but transcript setup is incomplete.", tuple(warnings[:3]))
    return FeatureHealth("Tickets", "🎫", "ready", f"Category `{cat.name}` and transcripts `{transcripts.name}` are ready.")


def _verification_score(guild: discord.Guild, cfg: Any, enabled: bool) -> FeatureHealth:
    if not enabled:
        return FeatureHealth("Verification", "✅", "skipped", "Skipped by selected services.")
    verify_ch = _text_channel(guild, _cfg_get(cfg, "verify_channel_id"))
    unverified = _role(guild, _cfg_get(cfg, "unverified_role_id"))
    verified = _role(guild, _cfg_get(cfg, "verified_role_id"))
    blockers: list[str] = []
    if verify_ch is None:
        blockers.append("Select or create a verification channel.")
    elif not _can_use_channel(guild, verify_ch):
        blockers.append("Bot cannot send embeds in the verification channel.")
    if unverified is None:
        blockers.append("Select or create the Unverified role.")
    elif not _can_manage_role(guild, unverified):
        blockers.append("Bot role must be above the Unverified role.")
    if verified is None:
        blockers.append("Select or create the Verified role.")
    elif not _can_manage_role(guild, verified):
        blockers.append("Bot role must be above the Verified role.")
    if blockers:
        return FeatureHealth("Verification", "✅", "blocker", "Needs channel/role setup before approvals.", tuple(blockers[:4]))
    return FeatureHealth("Verification", "✅", "ready", f"Channel `{verify_ch.name}` and roles are ready.")


def _voice_score(guild: discord.Guild, cfg: Any, enabled: bool) -> FeatureHealth:
    if not enabled:
        return FeatureHealth("Voice Verify", "🎙️", "skipped", "Skipped by selected services.")
    voice = _voice_channel(guild, _cfg_get(cfg, "vc_verify_channel_id"))
    queue = _text_channel(guild, _cfg_get(cfg, "vc_verify_queue_channel_id"))
    staff = _role(guild, _cfg_get(cfg, "vc_staff_role_id") or _cfg_get(cfg, "staff_role_id"))
    blockers: list[str] = []
    warnings: list[str] = []
    if voice is None:
        blockers.append("Select or create the voice verification channel.")
    elif not _can_use_channel(guild, voice, manage=True):
        blockers.append("Bot needs View Channel + Manage Channels on the voice verify channel.")
    if queue is None:
        warnings.append("Select a VC queue/staff request channel.")
    elif not _can_use_channel(guild, queue):
        blockers.append("Bot cannot send embeds in the VC queue channel.")
    if staff is None:
        warnings.append("Select a VC staff role so staff buttons are clear.")
    if blockers:
        return FeatureHealth("Voice Verify", "🎙️", "blocker", "Needs voice channel permission setup.", tuple(blockers[:3]))
    if warnings:
        return FeatureHealth("Voice Verify", "🎙️", "warning", "Voice access can work, but queue/staff config is incomplete.", tuple(warnings[:3]))
    return FeatureHealth("Voice Verify", "🎙️", "ready", f"Voice `{getattr(voice, 'name', 'configured')}` and queue `{queue.name}` are ready.")


def _logs_score(guild: discord.Guild, cfg: Any, enabled: bool) -> FeatureHealth:
    if not enabled:
        return FeatureHealth("Logs/Moderation", "🧾", "skipped", "Skipped by selected services.")
    modlog = _text_channel(guild, _cfg_get(cfg, "modlog_channel_id"))
    blockers: list[str] = []
    warnings: list[str] = []
    if modlog is None:
        blockers.append("Select or create a modlog channel.")
    elif not _can_use_channel(guild, modlog):
        blockers.append("Bot cannot send embeds in the modlog channel.")
    me = _bot_member(guild)
    if me is not None:
        perms = me.guild_permissions
        if not bool(getattr(perms, "view_audit_log", False)):
            warnings.append("View Audit Log improves staff attribution.")
        if not bool(getattr(perms, "moderate_members", False)):
            warnings.append("Moderate Members is needed for timeout-based moderation.")
    if blockers:
        return FeatureHealth("Logs/Moderation", "🧾", "blocker", "Logging channel is not usable.", tuple(blockers[:3]))
    if warnings:
        return FeatureHealth("Logs/Moderation", "🧾", "warning", "Logging works, but moderation attribution/actions are limited.", tuple(warnings[:3]))
    return FeatureHealth("Logs/Moderation", "🧾", "ready", f"Modlog `{modlog.name}` and key permissions are ready.")


def _spam_score(spam_state: Any, enabled: bool) -> FeatureHealth:
    if not enabled:
        return FeatureHealth("SpamGuard", "🛡️", "skipped", "Skipped by selected services.")
    active = bool(getattr(spam_state, "guard_active", False))
    persisted = bool(getattr(spam_state, "persisted", False))
    label = str(getattr(spam_state, "persistence_label", "unknown") or "unknown")
    if not active:
        return FeatureHealth("SpamGuard", "🛡️", "blocker", "Service selected, but actual guard is off.", ("Open Services → SpamGuard Setup → Enable Actual Guard.",))
    if not persisted:
        return FeatureHealth("SpamGuard", "🛡️", "warning", f"Active, but saving is `{label}`.", ("Create/fix guild_security_settings and save SpamGuard again.",))
    return FeatureHealth("SpamGuard", "🛡️", "ready", f"Actual guard is active and `{label}`.")


async def _automation_score(guild: discord.Guild, enabled: bool) -> FeatureHealth:
    if not enabled:
        return FeatureHealth("Automation", "🤖", "skipped", "Skipped because Tickets are not selected.")
    settings_ok = await _db_table_readable_async("ticket_automation_settings")
    state_ok = await _db_table_readable_async("ticket_automation_state")
    if settings_ok and state_ok:
        return FeatureHealth("Automation", "🤖", "ready", "SLA/reminder/auto-close tables are readable.")
    return FeatureHealth(
        "Automation",
        "🤖",
        "warning",
        "Ticket automation will fall back or stay limited until DB tables exist.",
        ("Run supabase/migrations/20260611_ticket_automation_tables.sql.",),
    )


async def _database_score() -> FeatureHealth:
    required = ["guild_configs", "tickets"]
    optional = ["member_activity_notices", "ticket_automation_settings", "ticket_automation_state"]
    required_ok = [name for name in required if await _db_table_readable_async(name)]
    optional_ok = [name for name in optional if await _db_table_readable_async(name)]
    if len(required_ok) < len(required):
        missing = [name for name in required if name not in required_ok]
        return FeatureHealth("Database", "🧱", "blocker", "Required Supabase tables are missing/unreadable.", tuple(f"Fix `{name}` table." for name in missing[:3]))
    if len(optional_ok) < len(optional):
        missing = [name for name in optional if name not in optional_ok]
        return FeatureHealth("Database", "🧱", "warning", "Core DB works; optional feature tables are missing.", tuple(f"Create `{name}` with the 20260611 migrations." for name in missing[:3]))
    return FeatureHealth("Database", "🧱", "ready", "Core and optional production tables are readable.")


async def build_feature_scoreboard(guild: discord.Guild) -> list[FeatureHealth]:
    cfg = await _load_config(guild)
    state = await _load_service_state(guild)
    spam_state = await _load_spam_state(guild, state)
    tickets = bool(getattr(state, "tickets", True))
    verification = bool(getattr(state, "verification", False))
    voice = bool(getattr(state, "voice", False))
    spamguard = bool(getattr(state, "spamguard", False))
    moderation = bool(getattr(state, "moderation", spamguard))
    return [
        _ticket_score(guild, cfg, tickets),
        _verification_score(guild, cfg, verification),
        _voice_score(guild, cfg, voice),
        _spam_score(spam_state, spamguard),
        _logs_score(guild, cfg, moderation),
        await _automation_score(guild, tickets),
        await _database_score(),
    ]


def _scoreboard_value(scores: list[FeatureHealth]) -> str:
    lines = [score.line for score in scores]
    text = "\n".join(lines)
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


def _wrap_setup_health() -> bool:
    try:
        from stoney_verify.commands_ext import public_setup_solid as solid
    except Exception as e:
        _warn(f"public_setup_solid unavailable: {e!r}")
        return False

    original = getattr(solid, "_build_health_embed", None)
    if not callable(original):
        _warn("public_setup_solid._build_health_embed missing")
        return False
    if getattr(original, "_feature_scoreboard_wrapped", False):
        return True

    async def wrapped_build_health_embed(guild: discord.Guild):
        embed = await original(guild)
        try:
            scores = await build_feature_scoreboard(guild)
            blockers = [s for s in scores if s.status == "blocker"]
            warnings = [s for s in scores if s.status == "warning"]
            ready = [s for s in scores if s.status == "ready"]
            skipped = [s for s in scores if s.status == "skipped"]
            embed.add_field(name="Feature Health Scoreboard", value=_scoreboard_value(scores), inline=False)
            embed.add_field(name="Feature Fixes", value=_fixes_value(scores), inline=False)
            embed.add_field(
                name="Product Readiness",
                value=(
                    f"Ready: **{len(ready)}** • Warnings: **{len(warnings)}** • Blockers: **{len(blockers)}** • Skipped: **{len(skipped)}**\n"
                    + _next_step(scores)
                )[:1024],
                inline=False,
            )
            if blockers:
                embed.color = discord.Color.red()
                embed.description = "🚫 **Fix the blockers first.** Feature scoreboard below shows exactly what is blocking setup."
            elif warnings:
                embed.color = discord.Color.orange()
                embed.description = "⚠️ **Usable, but not fully clean.** Feature scoreboard below shows remaining cleanup."
            else:
                embed.color = discord.Color.green()
                embed.description = "✅ **Selected services look ready to test.**"
        except Exception as e:
            try:
                embed.add_field(name="Feature Health Scoreboard", value=f"⚠️ Scoreboard check failed: `{type(e).__name__}`", inline=False)
            except Exception:
                pass
        return embed

    setattr(wrapped_build_health_embed, "_feature_scoreboard_wrapped", True)
    setattr(solid, "_build_health_embed", wrapped_build_health_embed)
    return True


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    ok = _wrap_setup_health()
    _PATCHED = True
    _log(f"active wrapped={ok}")
    return ok


apply()

__all__ = ["apply", "build_feature_scoreboard"]
