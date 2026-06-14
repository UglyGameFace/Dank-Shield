from __future__ import annotations

"""Owned setup permission repair service.

This is the product-level entrypoint used by /dank setup → Safety & Repair.
It extends the older guard implementation with exact-name discovery and clearer
fix boundaries, without blindly overwriting unrelated server channels.
"""

from typing import Any, Iterable, Mapping, Optional
import re
import unicodedata

import discord


_PUBLIC_CATEGORY_ALIASES: dict[str, set[str]] = {
    "Start/public category": {"start", "starthere", "welcome", "onboarding", "newmembers", "startcategory"},
}
_STAFF_CATEGORY_ALIASES: dict[str, set[str]] = {
    "Active tickets category": {"tickets", "ticket", "supporttickets", "activetickets", "opentickets"},
    "Ticket archive category": {"ticketarchive", "ticketarchives", "archivedtickets", "closedtickets", "closedticket"},
    "Staff tools category": {"staff", "stafftools", "staffarea", "management", "admin", "moderation"},
}
_PUBLIC_TEXT_ALIASES: dict[str, set[str]] = {
    "Welcome channel": {"welcome", "start", "starthere", "startwelcome"},
    "Rules channel": {"rules", "rule", "serverrules"},
    "Announcements channel": {"announcements", "announcement", "news"},
    "Verification start channel": {"verify", "verification", "getverified", "startverify"},
    "Ticket panel channel": {"support", "ticketpanel", "tickets", "help", "openaticket"},
}
_STAFF_TEXT_ALIASES: dict[str, set[str]] = {
    "VC verification queue channel": {"vcverifyqueue", "vcqueue", "vcrequests", "voiceverifyqueue"},
    "Transcripts channel": {"transcripts", "tickettranscripts", "transcript"},
    "Modlog channel": {"modlog", "modlogs", "modlogchannel", "auditlog", "auditlogs"},
    "Raid/security log channel": {"raidlog", "raidlogs", "securitylog", "securitylogs"},
    "Join/leave log channel": {"joinlog", "joinlogs", "joinleave", "joinleavelog", "joinsleaves"},
    "Force-verify log channel": {"forceverifylog", "forcedverifylog"},
    "Bot status channel": {"status", "botstatus", "health", "uptime"},
}
_VOICE_ALIASES: dict[str, set[str]] = {
    "Voice verification channel": {"voiceverify", "vcverify", "verificationvoice", "verifyvoice"},
}


def _norm_name(value: Any) -> str:
    raw = unicodedata.normalize("NFKC", str(value or "")).lower()
    raw = re.sub(r"[^a-z0-9]+", "", raw)
    return raw.strip()


def _all_channels(guild: discord.Guild, classes: tuple[type, ...]) -> list[Any]:
    items: list[Any] = []
    for channel in list(getattr(guild, "channels", []) or []):
        try:
            if isinstance(channel, classes):
                items.append(channel)
        except Exception:
            continue
    return items


def _find_exact_named(guild: discord.Guild, classes: tuple[type, ...], aliases: set[str], *, notes: list[str], label: str) -> Optional[Any]:
    wanted = {_norm_name(item) for item in aliases if _norm_name(item)}
    matches: list[Any] = []
    for channel in _all_channels(guild, classes):
        if _norm_name(getattr(channel, "name", "")) in wanted:
            matches.append(channel)
    if len(matches) == 1:
        notes.append(f"Auto-detected unsaved {label}: {legacy_label(matches[0])}.")
        return matches[0]
    if len(matches) > 1:
        notes.append(f"Skipped auto-detect for {label}: multiple exact-name matches. Save the intended channel in Core Setup → Use Existing Roles/Channels.")
    return None


def legacy_label(channel: Any) -> str:
    try:
        from stoney_verify.startup_guards import setup_permission_repair_guard as legacy
        return legacy._channel_label(channel)
    except Exception:
        mention = getattr(channel, "mention", None)
        return str(mention or getattr(channel, "name", "unknown"))


def _bot_blockers(guild: discord.Guild) -> list[str]:
    from stoney_verify.startup_guards import setup_permission_repair_guard as legacy

    blockers: list[str] = []
    me = legacy._bot_member(guild)
    if me is None:
        return ["Bot member could not be resolved in this guild."]
    perms = me.guild_permissions
    if not perms.manage_channels:
        blockers.append("Bot is missing **Manage Channels**. I cannot repair channel/category overwrites without it.")
    if not perms.view_channel:
        blockers.append("Bot is missing baseline **View Channels** access from its roles.")
    if not (perms.manage_roles or perms.administrator):
        blockers.append("Bot does not have **Manage Roles**. Channel overwrites may still work, but role creation/role-order repairs will fail elsewhere.")
    if not (perms.view_audit_log or perms.administrator):
        blockers.append("Bot is missing **View Audit Log**. Permission repair can run, but setup/member safety scans lose audit-log fallback accuracy.")
    return blockers


async def _build_expanded_targets(guild: discord.Guild) -> tuple[list[Any], list[str]]:
    from stoney_verify.guild_config import get_guild_config
    from stoney_verify.startup_guards import setup_permission_repair_guard as legacy

    targets, notes = await legacy._build_targets(guild)
    cfg = await get_guild_config(guild.id, refresh=True)
    seen = {int(getattr(item.channel, "id", 0) or 0) for item in targets}

    staff_role = legacy._role_from_config(guild, cfg, "staff_role_id", "ticket_staff_role_id", "support_role_id", "vc_staff_role_id")
    control_role = await legacy._resolve_control_role(guild, cfg)
    unverified_role = legacy._role_from_config(guild, cfg, "unverified_role_id", "pending_role_id", "waiting_role_id")
    verified_role = legacy._role_from_config(guild, cfg, "verified_role_id", "approved_role_id")
    resident_role = legacy._role_from_config(guild, cfg, "resident_role_id", "member_role_id", "verified_role_id", "approved_role_id")

    public_ow = legacy._public_readonly_overwrites(
        guild,
        staff_role=staff_role,
        control_role=control_role,
        unverified_role=unverified_role,
        verified_role=verified_role,
        resident_role=resident_role,
    )
    staff_ow = legacy._staff_private_overwrites(
        guild,
        staff_role=staff_role,
        control_role=control_role,
        unverified_role=unverified_role,
        verified_role=verified_role,
        resident_role=resident_role,
    )
    voice_ow = legacy._voice_verify_overwrites(
        guild,
        staff_role=staff_role,
        control_role=control_role,
        unverified_role=unverified_role,
        verified_role=verified_role,
        resident_role=resident_role,
    )

    for label, aliases in _PUBLIC_CATEGORY_ALIASES.items():
        channel = _find_exact_named(guild, (discord.CategoryChannel,), aliases, notes=notes, label=label)
        legacy._add_target(targets, seen, channel, label, public_ow)
    for label, aliases in _STAFF_CATEGORY_ALIASES.items():
        channel = _find_exact_named(guild, (discord.CategoryChannel,), aliases, notes=notes, label=label)
        legacy._add_target(targets, seen, channel, label, staff_ow)
    for label, aliases in _PUBLIC_TEXT_ALIASES.items():
        channel = _find_exact_named(guild, (discord.TextChannel,), aliases, notes=notes, label=label)
        legacy._add_target(targets, seen, channel, label, public_ow)
    for label, aliases in _STAFF_TEXT_ALIASES.items():
        channel = _find_exact_named(guild, (discord.TextChannel,), aliases, notes=notes, label=label)
        legacy._add_target(targets, seen, channel, label, staff_ow)
    for label, aliases in _VOICE_ALIASES.items():
        channel = _find_exact_named(guild, legacy._voice_channel_classes(), aliases, notes=notes, label=label)
        legacy._add_target(targets, seen, channel, label, voice_ow)

    # If exact-name category discovery found ticket/archive/staff categories, repair children too.
    for item in list(targets):
        if not isinstance(item.channel, discord.CategoryChannel):
            continue
        if item.label in {"Active tickets category", "Ticket archive category", "Staff tools category"}:
            for child in list(getattr(item.channel, "channels", []) or []):
                legacy._add_target(targets, seen, child, f"{item.label} child channel", item.overwrites)

    if not targets:
        notes.append("No saved or exact-name setup channels/categories were found. Run Core Setup first, or use existing-server mapping to save the intended roles/channels.")
    return targets, notes


async def preview_or_apply(guild: discord.Guild, *, apply: bool) -> dict[str, Any]:
    from stoney_verify.startup_guards import setup_permission_repair_guard as legacy

    blockers = _bot_blockers(guild)
    hard_blockers = [item for item in blockers if "Manage Channels" in item or "could not be resolved" in item]
    if hard_blockers:
        return {"ok": False, "error": "Permission repair is blocked by missing bot prerequisites.", "changed": [], "unchanged": [], "failed": [], "notes": blockers, "target_count": 0, "applied": bool(apply)}

    targets, notes = await _build_expanded_targets(guild)
    if blockers:
        notes = blockers + notes
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
            if not legacy._overwrite_changed(current, expected):
                continue
            channel_changes.append(legacy._target_label(target))
            if apply:
                try:
                    await channel.set_permissions(target, overwrite=expected, reason="Dank Shield setup permission repair")
                except Exception as exc:
                    failed.append(f"{legacy._channel_label(channel)} -> {legacy._target_label(target)}: {type(exc).__name__}")
        if channel_changes:
            changed.append(f"{legacy._channel_label(channel)} — {', '.join(channel_changes[:6])}{'…' if len(channel_changes) > 6 else ''}")
        else:
            unchanged.append(legacy._channel_label(channel))

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


def result_embed(result: dict[str, Any]) -> discord.Embed:
    from stoney_verify.startup_guards import setup_permission_repair_guard as legacy

    embed = legacy._result_embed(result)
    try:
        embed.description = (
            "Truth-engine repair for configured and exact-name Dank Shield setup targets. "
            "It fixes channel/category overwrites, then tells you what still requires Discord-level action."
        )
        embed.add_field(
            name="Fix Boundary",
            value=(
                "✅ Can fix saved/exact-name setup channel/category overwrites.\n"
                "⚠️ Cannot move the bot role, grant missing bot permissions, or guess ambiguous duplicate channels.\n"
                "🧭 If a target is not saved or exact-name matched, map it in Core Setup first."
            ),
            inline=False,
        )
    except Exception:
        pass
    return embed


async def open_permission_repair(interaction: discord.Interaction) -> None:
    from stoney_verify.commands_ext import public_setup_solid as solid
    from stoney_verify.startup_guards.setup_permission_repair_guard import PermissionRepairConfirmView

    if not await solid._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
    await solid._safe_defer_update(interaction)
    result = await preview_or_apply(guild, apply=False)
    await solid._edit_or_followup(interaction, embed=result_embed(result), view=PermissionRepairConfirmView())


async def apply_permission_repair(interaction: discord.Interaction) -> None:
    from stoney_verify.commands_ext import public_setup_solid as solid
    from stoney_verify.operation_queue import run_interaction_exclusive
    from stoney_verify.startup_guards.setup_permission_repair_guard import PermissionRepairDoneView

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

    async def job() -> dict[str, Any]:
        return await preview_or_apply(guild, apply=True)

    result = await run_interaction_exclusive(
        interaction=interaction,
        operation_type="setup_permission_repair",
        action_label="Setup permission repair",
        factory=job,
        fingerprint={"guild_id": int(guild.id), "scope": "expanded_setup_permissions"},
        risk_level="moderate",
        concurrency_class="guild_config_write",
        concurrency_key="setup_permission_repair",
        timeout_seconds=180.0,
    )
    if result is None:
        return
    await interaction.followup.send(embed=result_embed(result), view=PermissionRepairDoneView(), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


__all__ = ["open_permission_repair", "apply_permission_repair", "preview_or_apply", "result_embed"]
