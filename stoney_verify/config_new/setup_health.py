from __future__ import annotations

"""
Per-guild setup health diagnostics.

This module answers the question every public-server admin will ask:
"What is missing or misconfigured for this server?"

It intentionally does not mutate Discord or Supabase. It reads the active guild
config resolver and validates only the services enabled for that guild.

Service-aware behavior matters: a server using only tickets should not be marked
critical for missing ID verification roles/channels, and a server using only
verification should not be forced to configure ticket archive categories.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import discord

from .guild_config import (
    get_guild_config,
    resolve_configured_category,
    resolve_configured_role,
    resolve_configured_text_channel,
)


@dataclass(frozen=True)
class SetupCheck:
    key: str
    label: str
    ok: bool
    severity: str
    detail: str
    value: Optional[str] = None
    service: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "ok": self.ok,
            "severity": self.severity,
            "detail": self.detail,
            "value": self.value,
            "service": self.service,
        }


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return default
        return int(str(value).strip())
    except Exception:
        return default


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        raw = str(value or "").strip().lower()
        if raw in {"1", "true", "yes", "y", "on"}:
            return True
        if raw in {"0", "false", "no", "n", "off"}:
            return False
        return default
    except Exception:
        return default


def _channel_value(ch: Optional[discord.abc.GuildChannel]) -> Optional[str]:
    if ch is None:
        return None
    try:
        return f"#{ch.name} ({ch.id})"
    except Exception:
        return str(getattr(ch, "id", "unknown"))


def _role_value(role: Optional[discord.Role]) -> Optional[str]:
    if role is None:
        return None
    try:
        return f"@{role.name} ({role.id})"
    except Exception:
        return str(getattr(role, "id", "unknown"))


def _check_bool(
    key: str,
    label: str,
    ok: bool,
    *,
    severity: str,
    detail_ok: str,
    detail_bad: str,
    value: Optional[str] = None,
    service: Optional[str] = None,
) -> SetupCheck:
    return SetupCheck(
        key=key,
        label=label,
        ok=bool(ok),
        severity="ok" if ok else severity,
        detail=detail_ok if ok else detail_bad,
        value=value,
        service=service,
    )


def _service_flags(raw: Dict[str, Any]) -> Dict[str, bool]:
    """
    Default to tickets-only for newly provisioned public guilds.

    Existing owner/backfilled guilds can enable all services explicitly.
    """
    return {
        "tickets": _safe_bool(raw.get("tickets_enabled"), True),
        "verification": _safe_bool(raw.get("verification_enabled"), False),
        "voice_verification": _safe_bool(raw.get("voice_verification_enabled"), False),
        "moderation": _safe_bool(raw.get("moderation_enabled"), False),
    }


def _enabled_service_label(services: Dict[str, bool]) -> str:
    labels = []
    if services.get("tickets"):
        labels.append("tickets")
    if services.get("verification"):
        labels.append("ID verification")
    if services.get("voice_verification"):
        labels.append("voice verification")
    if services.get("moderation"):
        labels.append("moderation/modlog")
    return ", ".join(labels) if labels else "none"


async def build_guild_setup_health(guild: discord.Guild) -> Dict[str, Any]:
    checks: List[SetupCheck] = []

    cfg = await get_guild_config(int(guild.id))
    services = _service_flags(cfg.raw if cfg else {})

    tickets_enabled = services.get("tickets", True)
    verification_enabled = services.get("verification", False)
    voice_verification_enabled = services.get("voice_verification", False)
    moderation_enabled = services.get("moderation", False)
    any_role_service_enabled = verification_enabled or voice_verification_enabled

    me = guild.me
    if me is None:
        try:
            me = guild.get_member(guild._state.self_id)  # type: ignore[attr-defined]
        except Exception:
            me = None

    guild_perms = getattr(me, "guild_permissions", None)

    # Universal baseline: every mode needs the bot to see/send/read enough to operate.
    checks.append(
        _check_bool(
            "guild_config_row",
            "Guild Config Row",
            bool(cfg and cfg.guild_id == int(guild.id)),
            severity="critical",
            detail_ok=f"Config loaded from {cfg.source}.",
            detail_bad="No guild_configs row/fallback config could be loaded.",
            value=cfg.source,
            service="core",
        )
    )

    checks.append(
        _check_bool(
            "services_selected",
            "Enabled Services",
            any(services.values()),
            severity="warning",
            detail_ok=f"Enabled: {_enabled_service_label(services)}.",
            detail_bad="No services are enabled. Enable tickets, verification, voice verification, or moderation.",
            value=_enabled_service_label(services),
            service="core",
        )
    )

    setup_completed = bool(cfg.raw.get("setup_completed")) if cfg else False
    checks.append(
        _check_bool(
            "setup_completed",
            "Setup Completed Flag",
            setup_completed,
            severity="warning",
            detail_ok="This server is marked as setup complete.",
            detail_bad="This server is provisioned but setup is incomplete for the selected services.",
            value=str(setup_completed),
            service="core",
        )
    )

    # Permissions are checked only when the enabled service needs them.
    if tickets_enabled:
        checks.append(
            _check_bool(
                "perm_manage_channels",
                "Manage Channels",
                bool(getattr(guild_perms, "manage_channels", False)),
                severity="critical",
                detail_ok="Bot can create/move/delete ticket channels.",
                detail_bad="Tickets need Manage Channels for ticket creation, archive moves, and deletes.",
                service="tickets",
            )
        )

    if any_role_service_enabled:
        checks.append(
            _check_bool(
                "perm_manage_roles",
                "Manage Roles",
                bool(getattr(guild_perms, "manage_roles", False)),
                severity="critical",
                detail_ok="Bot can apply verification/member roles.",
                detail_bad="Verification services need Manage Roles for approvals and role cleanup.",
                service="verification",
            )
        )

    if moderation_enabled:
        checks.append(
            _check_bool(
                "perm_view_audit_log",
                "View Audit Log",
                bool(getattr(guild_perms, "view_audit_log", False)),
                severity="warning",
                detail_ok="Bot can attribute moderation/resource changes more accurately.",
                detail_bad="Moderation/modlog attribution may be incomplete without View Audit Log.",
                service="moderation",
            )
        )
        checks.append(
            _check_bool(
                "perm_moderate_members",
                "Moderate Members",
                bool(getattr(guild_perms, "moderate_members", False)),
                severity="warning",
                detail_ok="Bot can timeout members when moderation features need it.",
                detail_bad="Timeout/moderation features may be limited without Moderate Members.",
                service="moderation",
            )
        )

    # Tickets service.
    if tickets_enabled:
        transcripts = await resolve_configured_text_channel(
            guild,
            "transcripts_channel_id",
            "transcript_channel_id",
            fallback_names=("transcripts", "ticket-transcripts", "support-transcripts"),
            fallback_contains=("transcript", "ticket-log", "tickets-log"),
            label="setup_health_transcripts",
        )
        checks.append(
            _check_bool(
                "transcripts_channel",
                "Transcripts Channel",
                transcripts is not None,
                severity="warning",
                detail_ok="Transcript channel resolves inside this guild.",
                detail_bad="No same-guild transcript channel is configured/resolved. Ticket deletes may skip transcript posting.",
                value=_channel_value(transcripts),
                service="tickets",
            )
        )

        active_category = await resolve_configured_category(
            guild,
            "ticket_category_id",
            fallback_names=("tickets", "active tickets", "support tickets"),
            fallback_contains=("ticket", "support"),
            label="setup_health_ticket_category",
        )
        checks.append(
            _check_bool(
                "ticket_category",
                "Active Ticket Category",
                active_category is not None,
                severity="critical",
                detail_ok="Active ticket category resolves inside this guild.",
                detail_bad="No active ticket category is configured/resolved. Ticket creation may fail.",
                value=_channel_value(active_category),
                service="tickets",
            )
        )

        archive_category = await resolve_configured_category(
            guild,
            "ticket_archive_category_id",
            "archived_ticket_category_id",
            "archive_ticket_category_id",
            fallback_names=("archived tickets", "ticket archive", "closed tickets"),
            fallback_contains=("archive", "closed tickets"),
            label="setup_health_archive_category",
        )
        checks.append(
            _check_bool(
                "ticket_archive_category",
                "Archive Ticket Category",
                archive_category is not None,
                severity="warning",
                detail_ok="Archive ticket category resolves inside this guild.",
                detail_bad="No archive category is configured/resolved. Closed tickets may stay in place.",
                value=_channel_value(archive_category),
                service="tickets",
            )
        )

        staff_role = await resolve_configured_role(guild, "staff_role_id", "vc_staff_role_id", label="setup_health_staff_role")
        checks.append(
            _check_bool(
                "staff_role",
                "Staff Role",
                staff_role is not None,
                severity="warning",
                detail_ok="Staff role resolves inside this guild.",
                detail_bad="No same-guild staff role is configured/resolved. Ticket staff panel access may be limited.",
                value=_role_value(staff_role),
                service="tickets",
            )
        )

    # ID verification service.
    verify_channel = None
    unverified_role = None
    verified_role = None
    if verification_enabled:
        verify_channel = await resolve_configured_text_channel(
            guild,
            "verify_channel_id",
            fallback_names=("verify", "verification", "start-here", "rules"),
            fallback_contains=("verify", "verification", "start"),
            label="setup_health_verify_channel",
        )
        checks.append(
            _check_bool(
                "verify_channel",
                "Verify Channel",
                verify_channel is not None,
                severity="warning",
                detail_ok="Verify/start channel resolves inside this guild.",
                detail_bad="No verify/start channel is configured/resolved.",
                value=_channel_value(verify_channel),
                service="verification",
            )
        )

        unverified_role = await resolve_configured_role(guild, "unverified_role_id", label="setup_health_unverified_role")
        checks.append(
            _check_bool(
                "unverified_role",
                "Unverified Role",
                unverified_role is not None,
                severity="warning",
                detail_ok="Unverified role resolves inside this guild.",
                detail_bad="No same-guild Unverified role is configured/resolved.",
                value=_role_value(unverified_role),
                service="verification",
            )
        )

        verified_role = await resolve_configured_role(guild, "verified_role_id", label="setup_health_verified_role")
        checks.append(
            _check_bool(
                "verified_role",
                "Verified Role",
                verified_role is not None,
                severity="critical",
                detail_ok="Verified role resolves inside this guild.",
                detail_bad="No same-guild Verified role is configured/resolved. Verification approvals may fail.",
                value=_role_value(verified_role),
                service="verification",
            )
        )

    # Voice verification service.
    if voice_verification_enabled:
        vc_channel = await resolve_configured_text_channel(
            guild,
            "vc_verify_channel_id",
            fallback_names=("voice-verify", "vc-verify", "verify"),
            fallback_contains=("voice", "vc-verify", "verify"),
            label="setup_health_vc_verify_channel",
        )
        checks.append(
            _check_bool(
                "vc_verify_channel",
                "Voice Verify Channel",
                vc_channel is not None,
                severity="warning",
                detail_ok="Voice verification channel resolves inside this guild.",
                detail_bad="No same-guild voice verification channel is configured/resolved.",
                value=_channel_value(vc_channel),
                service="voice_verification",
            )
        )

        vc_queue = await resolve_configured_text_channel(
            guild,
            "vc_verify_queue_channel_id",
            fallback_names=("voice-verify-queue", "vc-verify-queue", "verify-queue"),
            fallback_contains=("queue", "vc", "voice"),
            label="setup_health_vc_queue_channel",
        )
        checks.append(
            _check_bool(
                "vc_verify_queue_channel",
                "Voice Verify Queue Channel",
                vc_queue is not None,
                severity="warning",
                detail_ok="Voice verification queue channel resolves inside this guild.",
                detail_bad="No same-guild voice verification queue channel is configured/resolved.",
                value=_channel_value(vc_queue),
                service="voice_verification",
            )
        )

        if verified_role is None:
            verified_role = await resolve_configured_role(guild, "verified_role_id", label="setup_health_vc_verified_role")
        checks.append(
            _check_bool(
                "vc_verified_role",
                "Verified Role",
                verified_role is not None,
                severity="critical",
                detail_ok="Verified role resolves inside this guild for voice verification approvals.",
                detail_bad="Voice verification needs a same-guild Verified role.",
                value=_role_value(verified_role),
                service="voice_verification",
            )
        )

    # Moderation/modlog service.
    if moderation_enabled:
        modlog = await resolve_configured_text_channel(
            guild,
            "modlog_channel_id",
            fallback_names=("mod-log", "modlog", "moderation-log", "staff-log"),
            fallback_contains=("modlog", "mod-log", "moderation"),
            label="setup_health_modlog",
        )
        checks.append(
            _check_bool(
                "modlog_channel",
                "Modlog Channel",
                modlog is not None,
                severity="warning",
                detail_ok="Modlog channel resolves inside this guild.",
                detail_bad="No same-guild modlog channel is configured/resolved.",
                value=_channel_value(modlog),
                service="moderation",
            )
        )

    # Role hierarchy only for services that use roles.
    bot_top_role = getattr(me, "top_role", None) if me else None
    if bot_top_role and verified_role and any_role_service_enabled:
        hierarchy_ok = bot_top_role > verified_role
        checks.append(
            _check_bool(
                "hierarchy_verified_role",
                "Role Hierarchy > Verified",
                hierarchy_ok,
                severity="critical",
                detail_ok="Bot role is above the Verified role.",
                detail_bad="Move the bot role above Verified or approvals will fail.",
                value=f"bot={getattr(bot_top_role, 'position', '?')} verified={getattr(verified_role, 'position', '?')}",
                service="verification",
            )
        )
    if bot_top_role and unverified_role and verification_enabled:
        hierarchy_ok = bot_top_role > unverified_role
        checks.append(
            _check_bool(
                "hierarchy_unverified_role",
                "Role Hierarchy > Unverified",
                hierarchy_ok,
                severity="critical",
                detail_ok="Bot role is above the Unverified role.",
                detail_bad="Move the bot role above Unverified or role cleanup will fail.",
                value=f"bot={getattr(bot_top_role, 'position', '?')} unverified={getattr(unverified_role, 'position', '?')}",
                service="verification",
            )
        )

    critical = [c for c in checks if not c.ok and c.severity == "critical"]
    warnings = [c for c in checks if not c.ok and c.severity == "warning"]
    ok_items = [c for c in checks if c.ok]

    if critical:
        status = "critical"
    elif warnings:
        status = "warning"
    else:
        status = "ok"

    return {
        "guild_id": str(int(guild.id)),
        "guild_name": guild.name,
        "status": status,
        "ok": status == "ok",
        "summary": {
            "ok": len(ok_items),
            "warnings": len(warnings),
            "critical": len(critical),
            "total": len(checks),
        },
        "config_source": cfg.source,
        "setup_completed": setup_completed,
        "services": services,
        "enabled_services_label": _enabled_service_label(services),
        "checks": [c.to_dict() for c in checks],
    }


def build_setup_health_embed(report: Dict[str, Any]) -> discord.Embed:
    status = _safe_str(report.get("status"), "unknown")
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}

    color = discord.Color.green()
    title_icon = "✅"
    if status == "critical":
        color = discord.Color.red()
        title_icon = "🚨"
    elif status == "warning":
        color = discord.Color.orange()
        title_icon = "⚠️"

    embed = discord.Embed(
        title=f"{title_icon} Stoney Verify Setup Health",
        description=(
            f"Guild: `{_safe_str(report.get('guild_name'))}` (`{_safe_str(report.get('guild_id'))}`)\n"
            f"Status: **{status.upper()}**\n"
            f"Enabled services: `{_safe_str(report.get('enabled_services_label'), 'none')}`\n"
            f"Config source: `{_safe_str(report.get('config_source'))}`\n"
            f"Setup completed: `{bool(report.get('setup_completed'))}`"
        ),
        color=color,
    )

    embed.add_field(
        name="Summary",
        value=(
            f"✅ OK: `{summary.get('ok', 0)}`\n"
            f"⚠️ Warnings: `{summary.get('warnings', 0)}`\n"
            f"🚨 Critical: `{summary.get('critical', 0)}`"
        ),
        inline=True,
    )

    bad_checks = [c for c in report.get("checks", []) if isinstance(c, dict) and not c.get("ok")]
    good_checks = [c for c in report.get("checks", []) if isinstance(c, dict) and c.get("ok")]

    if bad_checks:
        lines = []
        for check in bad_checks[:12]:
            sev = "🚨" if check.get("severity") == "critical" else "⚠️"
            svc = _safe_str(check.get("service"), "core")
            lines.append(f"{sev} **{_safe_str(check.get('label'))}** `[{svc}]` — {_safe_str(check.get('detail'))}")
        if len(bad_checks) > 12:
            lines.append(f"…and {len(bad_checks) - 12} more issue(s).")
        embed.add_field(name="Needs Attention", value="\n".join(lines)[:1024], inline=False)

    if good_checks:
        lines = []
        for check in good_checks[:10]:
            value = _safe_str(check.get("value"))
            svc = _safe_str(check.get("service"), "core")
            suffix = f" → `{value}`" if value else ""
            lines.append(f"✅ {check.get('label')} `[{svc}]`{suffix}")
        if len(good_checks) > 10:
            lines.append(f"…and {len(good_checks) - 10} more OK check(s).")
        embed.add_field(name="Working", value="\n".join(lines)[:1024], inline=False)

    embed.set_footer(text="Stoney Verify • setup-health • service-aware")
    return embed


__all__ = ["build_guild_setup_health", "build_setup_health_embed", "SetupCheck"]
