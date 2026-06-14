from __future__ import annotations

from typing import Any

import discord

from .loader import snapshot_from_config
from .models import FindingSeverity, HealthFinding, RecommendedAction, SetupHealthReport
from .policy import allowed_public_ids, private_or_staff_ids, saved_public_ids
from .scanner import all_channel_targets, bot_member, can_see, can_send, get_channel, get_role, is_voice, parent_id, permission, target_id, target_label

ENGINE_VERSION = "setup-engine-v1"


def finding(id: str, severity: FindingSeverity, title: str, observed: str, expected: str, *, targets: tuple[int, ...] = (), action: RecommendedAction = RecommendedAction.NONE, repairable: bool = False, plan: str = "", manual: tuple[str, ...] = ()) -> HealthFinding:
    return HealthFinding(id=id, severity=severity, title=title, observed=observed, expected=expected, affected_target_ids=targets, recommended_action=action, repairable=repairable, repair_plan_id=plan, manual_steps=manual)


def role_label(guild: discord.Guild, role_id: int) -> str:
    role = get_role(guild, role_id)
    return str(getattr(role, "mention", None) or getattr(role, "name", None) or f"missing role {role_id}")


def has_bot_basics(guild: discord.Guild) -> bool:
    me = bot_member(guild)
    perms = getattr(me, "guild_permissions", None)
    return bool(perms and perms.manage_channels and perms.manage_roles and perms.send_messages and perms.embed_links)


def bot_can_manage_role(guild: discord.Guild, role_id: int) -> tuple[bool, str]:
    role = get_role(guild, role_id)
    me = bot_member(guild)
    if role is None:
        return False, "role is missing"
    if me is None:
        return False, "bot member could not be resolved"
    perms = getattr(me, "guild_permissions", None)
    if not bool(perms and perms.manage_roles):
        return False, "bot is missing Manage Roles"
    try:
        if role >= me.top_role and guild.owner_id != me.id:
            return False, "role is above or equal to the bot top role"
    except Exception:
        return False, "role hierarchy could not be checked"
    return True, ""


def add_core(guild: discord.Guild, cfg: Any, findings: list[HealthFinding], ok: list[str]) -> None:
    if has_bot_basics(guild):
        ok.append("Bot has required server-level channel/role permissions.")
    else:
        findings.append(finding("bot.basic_permissions", FindingSeverity.BLOCKER, "Bot permissions", "Basic server permissions are missing.", "Grant Manage Channels, Manage Roles, Send Messages, and Embed Links.", action=RecommendedAction.MOVE_ROLE, manual=("Move Dank Shield high enough in Server Settings → Roles.",)))

    for key, label, role_id, required in (
        ("staff", "Ticket staff role", cfg.effective_staff_role_id, True),
        ("unverified", "New/waiting role", cfg.unverified_role_id, True),
        ("verified", "Approved role", cfg.verified_role_id, True),
        ("member", "Member/resident role", cfg.effective_member_role_id, False),
        ("control", "Owner/admin role", cfg.server_control_role_id, False),
    ):
        if role_id <= 0:
            if required:
                findings.append(finding("role." + key + ".not_set", FindingSeverity.BLOCKER, label, "Role is not saved.", "Pick this server's existing role.", action=RecommendedAction.PICK_EXISTING))
            elif key == "member" and cfg.verified_role_id > 0:
                ok.append(f"Member/resident is using Verified: {role_label(guild, cfg.verified_role_id)}.")
            continue
        role = get_role(guild, role_id)
        if role is None:
            sev = FindingSeverity.BLOCKER if required else FindingSeverity.WARNING
            findings.append(finding("role." + key + ".missing", sev, label, f"Saved role `{role_id}` no longer exists.", "Pick a live role or clear the stale value.", action=RecommendedAction.PICK_EXISTING))
            continue
        ok.append(f"{label} exists: {role.mention}.")
        if key in {"unverified", "verified", "member"}:
            manageable, reason = bot_can_manage_role(guild, role_id)
            if not manageable:
                findings.append(finding("role." + key + ".unmanageable", FindingSeverity.BLOCKER, label, f"Bot cannot manage {role.mention}: {reason}.", "Bot role must be above managed roles.", action=RecommendedAction.MOVE_ROLE, manual=("Move Dank Shield above this role.",)))

    for key, label, cid, required in (
        ("ticket_open", "Open ticket folder", cfg.ticket_category_id, True),
        ("ticket_archive", "Closed ticket folder", cfg.archive_category_id, False),
        ("transcripts", "Transcript channel", cfg.transcript_channel_id, False),
        ("ticket_panel", "Public ticket panel channel", cfg.ticket_panel_channel_id, True),
        ("verify", "Verify text channel", cfg.verify_channel_id, True),
        ("vc_verify", "Voice check channel", cfg.vc_verify_channel_id, True),
        ("vc_queue", "Voice check request channel", cfg.vc_queue_channel_id, False),
        ("modlog", "Log channel", cfg.modlog_channel_id, False),
    ):
        if cid <= 0:
            if required:
                findings.append(finding("channel." + key + ".not_set", FindingSeverity.BLOCKER, label, "Channel/category is not saved.", "Pick this server's existing target.", action=RecommendedAction.PICK_EXISTING))
            continue
        channel = get_channel(guild, cid)
        if channel is None:
            sev = FindingSeverity.BLOCKER if required else FindingSeverity.WARNING
            findings.append(finding("channel." + key + ".missing", sev, label, f"Saved target `{cid}` no longer exists.", "Pick a live target or clear the stale value.", action=RecommendedAction.PICK_EXISTING))
            continue
        if key == "vc_verify" and not is_voice(channel):
            findings.append(finding("channel.vc_verify.wrong_type", FindingSeverity.BLOCKER, label, f"{target_label(channel)} is not a voice/stage channel.", "Pick a voice/stage channel.", action=RecommendedAction.PICK_EXISTING))
        else:
            ok.append(f"{label} is chosen: {target_label(channel)}.")


def add_visibility(guild: discord.Guild, cfg: Any, findings: list[HealthFinding], ok: list[str]) -> None:
    unverified = get_role(guild, cfg.unverified_role_id)
    if unverified is None:
        return
    public_saved = saved_public_ids(cfg)
    public_allowed = allowed_public_ids(cfg, guild)
    private_ids = private_or_staff_ids(cfg, guild)

    for cid in sorted(public_saved):
        channel = get_channel(guild, cid)
        if channel is None:
            continue
        if not can_see(channel, unverified):
            findings.append(finding("visibility.onboarding_hidden." + str(cid), FindingSeverity.WARNING, "Onboarding visibility", f"{target_label(channel)} is not visible to {unverified.mention}.", "Unverified should see onboarding entry points.", targets=(cid,), action=RecommendedAction.FIX_PERMISSIONS, repairable=True, plan="repair.onboarding_visibility"))
        if is_voice(channel):
            open_bits = []
            if permission(channel, unverified, "connect"):
                open_bits.append("Connect")
            if permission(channel, unverified, "speak"):
                open_bits.append("Speak")
            if can_send(channel, unverified):
                open_bits.append("Send Messages")
            if open_bits:
                findings.append(finding("visibility.vc_verify_too_open." + str(cid), FindingSeverity.WARNING, "VC verification access", f"{target_label(channel)} allows {unverified.mention}: {', '.join(open_bits)}.", "Unverified may see VC verification, but cannot freely connect, speak, or send messages there.", targets=(cid,), action=RecommendedAction.FIX_PERMISSIONS, repairable=True, plan="repair.vc_verification_access"))
        elif can_send(channel, unverified):
            findings.append(finding("visibility.onboarding_writable." + str(cid), FindingSeverity.WARNING, "Onboarding read-only", f"{target_label(channel)} lets {unverified.mention} send messages.", "Unverified onboarding surfaces should be read-only.", targets=(cid,), action=RecommendedAction.FIX_PERMISSIONS, repairable=True, plan="repair.onboarding_readonly"))
        pid = parent_id(channel)
        parent = get_channel(guild, pid)
        if pid > 0 and parent is not None and can_see(channel, unverified) and not can_see(parent, unverified) and pid not in private_ids:
            findings.append(finding("visibility.parent_hidden." + str(pid), FindingSeverity.WARNING, "Onboarding category header", f"{target_label(channel)} is visible but parent {target_label(parent)} is hidden.", "Safe onboarding parent category headers may be visible.", targets=(pid, cid), action=RecommendedAction.FIX_PERMISSIONS, repairable=True, plan="repair.onboarding_parent"))
        elif pid in private_ids:
            findings.append(finding("layout.onboarding_inside_private." + str(cid), FindingSeverity.WARNING, "Onboarding placement", f"{target_label(channel)} is under private/staff category {target_label(parent)}.", "Move/reselect it into public onboarding.", targets=(cid, pid), action=RecommendedAction.MOVE_CHANNEL, manual=("Use My Existing Server → Discord Categories to choose a public onboarding category.", "Move or reselect this channel there.")))

    for target in all_channel_targets(guild):
        tid = target_id(target)
        if tid <= 0 or tid in public_allowed:
            continue
        if can_see(target, unverified):
            sev = FindingSeverity.BLOCKER if tid in private_ids else FindingSeverity.WARNING
            findings.append(finding("visibility.unverified_leak." + str(tid), sev, "Unverified visibility leak", f"{target_label(target)} is visible to {unverified.mention}.", "Only onboarding/public review surfaces should be visible before verification.", targets=(tid,), action=RecommendedAction.FIX_PERMISSIONS, repairable=True, plan="repair.hide_unverified"))
    ok.append("Canonical visibility policy checked onboarding, private/staff targets, and Unverified leaks.")


def build_setup_health_report(guild: discord.Guild, raw_cfg: Any) -> SetupHealthReport:
    cfg = snapshot_from_config(int(guild.id), raw_cfg)
    findings: list[HealthFinding] = []
    ok: list[str] = []
    add_core(guild, cfg, findings, ok)
    if cfg.uses_verified_as_member:
        ok.append("Verified is the effective Member/Resident role for this server.")
    add_visibility(guild, cfg, findings, ok)
    ok.append(f"Canonical setup engine: {ENGINE_VERSION}.")
    return SetupHealthReport(int(guild.id), ENGINE_VERSION, cfg, tuple(findings), tuple(), tuple(ok))


def build_legacy_health_lists(guild: discord.Guild, raw_cfg: Any) -> tuple[list[str], list[str], list[str]]:
    report = build_setup_health_report(guild, raw_cfg)
    blockers = [item.legacy_line() for item in report.blockers]
    warnings = [item.legacy_line() for item in report.warnings]
    return blockers, warnings, list(report.ok_lines)
