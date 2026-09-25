from __future__ import annotations

"""Owned setup permission-repair UI and execution service.

Normal setup repair is deliberately scoped to configured/exact-name setup
channels. Whole-server activity coverage is opt-in from the dedicated read-only
activity-access screen so ordinary repair does not explode into dozens of
unrelated targets.
"""

from typing import Any, Optional
import re
import unicodedata

import discord

from . import permission_repair_core as repair_core
from .members_new.activity_scope import audit_activity_scope


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
    return re.sub(r"[^a-z0-9]+", "", raw).strip()


def _all_channels(guild: discord.Guild, classes: tuple[type, ...]) -> list[Any]:
    out: list[Any] = []
    for channel in list(getattr(guild, "channels", []) or []):
        try:
            if isinstance(channel, classes):
                out.append(channel)
        except Exception:
            continue
    return out


def _find_exact_named(
    guild: discord.Guild,
    classes: tuple[type, ...],
    aliases: set[str],
    *,
    notes: list[str],
    label: str,
) -> Optional[Any]:
    wanted = {_norm_name(item) for item in aliases if _norm_name(item)}
    matches = [
        channel
        for channel in _all_channels(guild, classes)
        if _norm_name(getattr(channel, "name", "")) in wanted
    ]
    if len(matches) == 1:
        notes.append(f"Auto-detected unsaved {label}: {legacy_label(matches[0])}.")
        return matches[0]
    if len(matches) > 1:
        notes.append(
            f"Skipped auto-detect for {label}: multiple exact-name matches. "
            "Save the intended channel in Setup Plan & Server Items → Choose Roles & Channels."
        )
    return None


def legacy_label(channel: Any) -> str:
    try:
        from stoney_verify.startup_guards import setup_permission_repair_guard as legacy

        return legacy._channel_label(channel)
    except Exception:
        mention = getattr(channel, "mention", None)
        return str(mention or getattr(channel, "name", "unknown"))


def _bot_blockers(guild: discord.Guild) -> list[str]:
    me = repair_core._bot_member(guild)
    if me is None:
        return ["Dank Shield could not resolve its bot member in this server."]

    blockers: list[str] = []
    perms = me.guild_permissions

    # Permission-overwrite mutation itself requires Manage Roles / Manage
    # Permissions. Manage Channels is still important for ticket/channel
    # creation, but it must not be misreported as the overwrite-edit gate.
    if not (perms.manage_roles or perms.administrator):
        blockers.append(
            "Dank Shield is missing **Manage Roles** at the server level. Discord requires "
            "Manage Roles (shown as Manage Permissions in channel settings) to repair channel overwrites."
        )
    if not (perms.manage_channels or perms.administrator):
        blockers.append(
            "Dank Shield is missing **Manage Channels** at the server level; ticket/channel creation "
            "features may still fail after permission-overwrite repair."
        )
    if not (perms.view_channel or perms.administrator):
        blockers.append("Dank Shield is missing baseline **View Channels** access.")
    if not (perms.view_audit_log or perms.administrator):
        blockers.append("Dank Shield is missing **View Audit Log**; audit-backed setup checks will be less reliable.")
    return blockers


def _split_legacy_targets(raw: Any) -> tuple[list[Any], list[str], list[str], list[str]]:
    try:
        items = tuple(raw or ())
    except Exception:
        return [], ["Permission repair target builder returned an unreadable result."], [], []
    targets = list(items[0] if len(items) >= 1 and items[0] is not None else [])
    notes = list(items[1] if len(items) >= 2 and items[1] is not None else [])
    missing_mappings = list(items[2] if len(items) >= 3 and items[2] is not None else [])
    manual_actions = list(items[3] if len(items) >= 4 and items[3] is not None else [])
    return targets, notes, missing_mappings, manual_actions


def _activity_scope_object(guild: discord.Guild, channel_id: int) -> Any | None:
    channel_id = int(channel_id or 0)
    if channel_id <= 0:
        return None

    getter = getattr(guild, "get_channel_or_thread", None)
    if callable(getter):
        try:
            found = getter(channel_id)
            if found is not None:
                return found
        except Exception:
            pass

    for collection_name in ("channels", "threads"):
        for item in list(getattr(guild, collection_name, []) or []):
            try:
                if int(getattr(item, "id", 0) or 0) == channel_id:
                    return item
            except Exception:
                continue
    return None


def _activity_repair_channel(guild: discord.Guild, problem: Any) -> Any | None:
    item = _activity_scope_object(guild, int(getattr(problem, "channel_id", 0) or 0))
    if item is None:
        return None

    if str(getattr(problem, "channel_kind", "") or "").strip().lower() == "thread":
        item = getattr(item, "parent", None)
        if item is None:
            return None

    if not callable(getattr(item, "set_permissions", None)):
        return None
    return item


def _merge_activity_coverage_targets(
    guild: discord.Guild,
    targets: list[Any],
    seen: set[int],
    notes: list[str],
    manual_actions: list[str],
) -> None:
    """Build bot-only repair targets directly from the authoritative activity audit."""
    from stoney_verify.startup_guards import setup_permission_repair_guard as legacy

    me = repair_core._bot_member(guild)
    if me is None:
        manual_actions.append("Dank Shield could not resolve its bot member for activity-access repair.")
        return

    temporary_admin_active = bool(
        getattr(getattr(me, "guild_permissions", None), "administrator", False)
    )
    report = audit_activity_scope(
        guild,
        ignore_administrator=temporary_admin_active,
    )
    if not bool(getattr(report, "bot_member_resolved", False)):
        manual_actions.append("Dank Shield could not resolve its bot member for activity-access repair.")
        return

    planned: dict[int, dict[str, Any]] = {}
    unresolved: list[str] = []

    for problem in tuple(getattr(report, "problems", ()) or ()):
        channel = _activity_repair_channel(guild, problem)
        if channel is None:
            unresolved.append(
                f"{getattr(problem, 'display_name', None) or getattr(problem, 'channel_name', 'unknown')}: "
                "the audited activity target could not be mapped to a repairable channel."
            )
            continue

        cid = int(getattr(channel, "id", 0) or 0)
        if cid <= 0:
            continue

        entry = planned.get(cid)
        if entry is None:
            try:
                current = channel.overwrites_for(me)
            except Exception:
                current = discord.PermissionOverwrite()
            expected, inherited = repair_core.seed_bot_overwrite_from_parent(
                guild,
                channel,
                current,
                include_manage_roles=temporary_admin_active,
            )
            entry = {
                "channel": channel,
                "expected": expected,
                "required": set(),
                "inherited": inherited,
            }
            planned[cid] = entry

        required = entry["required"]
        for name in tuple(getattr(problem, "missing_permissions", ()) or ()):
            required.add(str(name))

    for cid, entry in planned.items():
        expected = entry["expected"]
        required = entry["required"]

        # Repair only Dank Shield's own overwrite. If the child has no bot-specific
        # overwrite, its parent category can seed that one bot entry. Never perform
        # a full Discord category sync and never clear an explicit bot deny here.
        permission_map = {
            "View Channel": "view_channel",
            "Read Message History": "read_message_history",
            "Manage Threads": "manage_threads",
        }
        for label, attr in permission_map.items():
            if label not in required:
                continue
            if getattr(expected, attr, None) is False and not temporary_admin_active:
                manual_actions.append(
                    f"{legacy._channel_label(entry['channel'])}: Dank Shield has an explicit "
                    f"deny for {label}. Use Specific Channel → Resolve Explicit Denies if that deny is accidental."
                )
                continue
            # Emergency recovery was explicitly authorized to restore Dank
            # Shield's own access. It may replace a conflicting bot-member deny,
            # but never touches another role/member overwrite.
            setattr(expected, attr, True)

        # During explicit temporary-Administrator recovery, persist a bot-member
        # Manage Permissions allow on the affected target before Administrator is
        # removed again. Normal non-Admin repair never manufactures this powerful
        # channel allow merely because server-level Manage Roles is effective.
        if temporary_admin_active:
            try:
                from stoney_verify.services.setup_permission_policy import (
                    permissions_without_administrator,
                )

                underlying = permissions_without_administrator(
                    entry["channel"],
                    me,
                )
                if underlying is not None and not bool(
                    getattr(underlying, "manage_roles", False)
                ):
                    # Explicit emergency recovery: replace any bot-member deny
                    # with an allow so removing Administrator does not re-lock
                    # the same target. No unrelated role/member overwrite moves.
                    expected.manage_roles = True
            except Exception:
                pass

        legacy._add_target(
            targets,
            seen,
            entry["channel"],
            "Authoritative activity coverage",
            {me: expected},
        )

    if planned:
        notes.append(
            f"Activity access scope: {len(planned)} channel overwrite(s) cover "
            f"{len(tuple(getattr(report, 'problems', ()) or ()))} Diagnostics gap(s). "
            "Only Dank Shield's own overwrite is changed. An empty child bot overwrite may be seeded from its parent category; unrelated role/member overwrites are preserved."
        )
    elif not tuple(getattr(report, "problems", ()) or ()):
        notes.append("Diagnostics activity scope is already fully accessible.")

    manual_actions.extend(unresolved)


async def _build_expanded_targets(
    guild: discord.Guild,
    *,
    include_activity_coverage: bool = False,
) -> tuple[list[Any], list[str], list[str], list[str]]:
    if include_activity_coverage:
        targets: list[Any] = []
        notes: list[str] = []
        missing_mappings: list[str] = []
        manual_actions: list[str] = []
        seen: set[int] = set()
        _merge_activity_coverage_targets(
            guild,
            targets,
            seen,
            notes,
            manual_actions,
        )
        return targets, notes, missing_mappings, manual_actions

    from stoney_verify.guild_config import get_guild_config
    from stoney_verify.startup_guards import setup_permission_repair_guard as legacy

    targets, notes, missing_mappings, manual_actions = _split_legacy_targets(await legacy._build_targets(guild))
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
        legacy._add_target(
            targets,
            seen,
            _find_exact_named(guild, (discord.CategoryChannel,), aliases, notes=notes, label=label),
            label,
            public_ow,
        )
    for label, aliases in _STAFF_CATEGORY_ALIASES.items():
        legacy._add_target(
            targets,
            seen,
            _find_exact_named(guild, (discord.CategoryChannel,), aliases, notes=notes, label=label),
            label,
            staff_ow,
        )
    for label, aliases in _PUBLIC_TEXT_ALIASES.items():
        legacy._add_target(
            targets,
            seen,
            _find_exact_named(guild, (discord.TextChannel,), aliases, notes=notes, label=label),
            label,
            public_ow,
        )
    for label, aliases in _STAFF_TEXT_ALIASES.items():
        legacy._add_target(
            targets,
            seen,
            _find_exact_named(guild, (discord.TextChannel,), aliases, notes=notes, label=label),
            label,
            staff_ow,
        )
    for label, aliases in _VOICE_ALIASES.items():
        legacy._add_target(
            targets,
            seen,
            _find_exact_named(guild, legacy._voice_channel_classes(), aliases, notes=notes, label=label),
            label,
            voice_ow,
        )

    for item in list(targets):
        if isinstance(item.channel, discord.CategoryChannel) and item.label in {
            "Active tickets category",
            "Ticket archive category",
            "Staff tools category",
        }:
            for child in list(getattr(item.channel, "channels", []) or []):
                legacy._add_target(targets, seen, child, f"{item.label} child channel", item.overwrites)

    if not targets:
        notes.append(
            "No saved or exact-name setup channels/categories were found. "
            "Use Setup Plan & Server Items → Choose Roles & Channels."
        )
    return targets, notes, missing_mappings, manual_actions


async def preview_or_apply(
    guild: discord.Guild,
    *,
    apply: bool,
    include_activity_coverage: bool = False,
) -> dict[str, Any]:
    from stoney_verify.startup_guards import setup_permission_repair_guard as legacy

    blockers = _bot_blockers(guild)
    if include_activity_coverage:
        blockers = [
            item
            for item in blockers
            if "Manage Roles" in item or "could not resolve" in item
        ]
    reauthorize_recommended = (
        any("Manage Roles" in item for item in blockers)
        if include_activity_coverage
        else repair_core.reauthorize_recommended(guild)
    )
    hard_blockers = [
        item
        for item in blockers
        if "Manage Roles" in item or "could not resolve" in item
    ]
    if hard_blockers:
        return {
            "ok": False,
            "error": "Permission repair is blocked by missing bot prerequisites.",
            "changed": [],
            "unchanged": [],
            "failed": [],
            "notes": blockers,
            "missing_mappings": [],
            "manual_actions": hard_blockers,
            "target_count": 0,
            "applied": bool(apply),
            "include_activity_coverage": bool(include_activity_coverage),
            "reauthorize_recommended": reauthorize_recommended,
        }

    targets, notes, missing_mappings, manual_actions = await _build_expanded_targets(
        guild,
        include_activity_coverage=include_activity_coverage,
    )
    if blockers:
        notes = blockers + notes

    changed: list[str] = []
    unchanged: list[str] = []
    failed: list[str] = []
    me = repair_core._bot_member(guild)
    temporary_admin_active = bool(
        me is not None
        and getattr(getattr(me, "guild_permissions", None), "administrator", False)
    )
    emergency_recovery_targets: list[str] = []

    for item in targets:
        channel = item.channel
        overwrite_blocker = repair_core.permission_overwrite_edit_blocker(
            guild,
            channel,
        )
        if overwrite_blocker:
            if repair_core.emergency_recovery_needed(guild, channel):
                emergency_recovery_targets.append(legacy._channel_label(channel))
            else:
                manual_actions.append(
                    f"{legacy._channel_label(channel)}: {overwrite_blocker}"
                )
            continue

        pending_labels: list[str] = []
        applied_labels: list[str] = []
        for target, expected in item.overwrites.items():
            try:
                current = channel.overwrites_for(target)
            except Exception:
                current = discord.PermissionOverwrite()

            # Normal repair does not add or remove channel-level Manage
            # Permissions. Preserve whatever explicit bot-member value already
            # exists. Temporary Administrator recovery is the one deliberate
            # exception and prepares the expected overwrite earlier.
            try:
                is_bot_target = bool(
                    me is not None
                    and int(getattr(target, "id", 0) or 0)
                    == int(getattr(me, "id", 0) or 0)
                )
            except Exception:
                is_bot_target = target is me
            if is_bot_target and not temporary_admin_active:
                try:
                    expected = discord.PermissionOverwrite.from_pair(*expected.pair())
                    expected.manage_roles = getattr(current, "manage_roles", None)
                except Exception:
                    pass

            if not legacy._overwrite_changed(current, expected):
                continue

            label = legacy._target_label(target)
            pending_labels.append(label)
            if not apply:
                continue
            try:
                await channel.set_permissions(
                    target,
                    overwrite=expected,
                    reason="Dank Shield setup permission repair",
                )
                applied_labels.append(label)
            except discord.Forbidden:
                blocker = repair_core.permission_overwrite_edit_blocker(
                    guild,
                    channel,
                )
                failed.append(
                    f"{legacy._channel_label(channel)} → {label}: "
                    + (
                        blocker
                        or "Discord denied permission-overwrite editing after the cached Manage Roles check passed."
                    )
                )
            except Exception as exc:
                failed.append(
                    f"{legacy._channel_label(channel)} → {label}: {type(exc).__name__}."
                )

        visible_labels = applied_labels if apply else pending_labels
        if visible_labels:
            changed.append(
                f"{legacy._channel_label(channel)} — {', '.join(visible_labels[:6])}"
                f"{'…' if len(visible_labels) > 6 else ''}"
            )
        elif not pending_labels:
            unchanged.append(legacy._channel_label(channel))

    if emergency_recovery_targets:
        sample = ", ".join(emergency_recovery_targets[:4])
        more = (
            f" and {len(emergency_recovery_targets) - 4} more"
            if len(emergency_recovery_targets) > 4
            else ""
        )
        manual_actions.insert(
            0,
            (
                f"{len(emergency_recovery_targets)} channel/category target(s) are already self-locked "
                "against Dank Shield's Manage Permissions. Discord will not accept ordinary overwrite "
                "repair there. Use **Temporary Admin Recovery** once for this server, then press "
                f"**Preview Again** and run the safe repair. Affected examples: {sample}{more}."
            ),
        )

    if apply:
        if include_activity_coverage:
            notes.insert(
                0,
                "Activity access repair finished. Re-run Diagnostics to confirm coverage after Discord propagates the overwrite updates.",
            )
        else:
            try:
                from stoney_verify.guild_config import get_guild_config
                from stoney_verify.setup_engine import build_setup_health_report

                cfg = await get_guild_config(guild.id, refresh=True)
                report = build_setup_health_report(guild, cfg)
                remaining = [item for item in report.findings if getattr(item, "repairable", False)]
                if remaining:
                    notes.insert(0, f"Post-repair Setup Check: {len(remaining)} repairable finding(s) still remain.")
                else:
                    notes.insert(0, "Post-repair Setup Check: no repairable findings remain.")
            except Exception as exc:
                notes.insert(0, f"Post-repair Setup Check could not run: {type(exc).__name__}.")

    return {
        "ok": not failed and not manual_actions and not missing_mappings,
        "error": "" if not failed else "Some permission overwrites could not be repaired.",
        "changed": changed,
        "unchanged": unchanged,
        "failed": failed,
        "notes": notes,
        "missing_mappings": missing_mappings,
        "manual_actions": manual_actions,
        "target_count": len(targets),
        "applied": bool(apply),
        "include_activity_coverage": bool(include_activity_coverage),
        "reauthorize_recommended": reauthorize_recommended,
        "emergency_recovery_recommended": bool(emergency_recovery_targets),
        "emergency_recovery_count": len(emergency_recovery_targets),
        "emergency_recovery_targets": emergency_recovery_targets[:12],
        "temporary_admin_active": temporary_admin_active,
    }


def _line_list(lines: list[str], *, empty: str = "None", limit: int = 760, max_rows: int = 5) -> str:
    clean = [str(line or "").strip() for line in lines if str(line or "").strip()]
    if not clean:
        return empty
    shown = clean[: max(1, max_rows)]
    if len(clean) > len(shown):
        shown.append(f"…and {len(clean) - len(shown)} more")
    return "\n".join(shown)[:limit]


def _repair_attention(result: dict[str, Any]) -> list[str]:
    return [
        *list(result.get("failed") or []),
        *list(result.get("manual_actions") or []),
        *list(result.get("missing_mappings") or []),
    ]


def _preview_action_state(
    result: dict[str, Any] | None,
) -> tuple[str, discord.ButtonStyle, bool]:
    """Return the one honest primary action for the canonical repair hub."""
    if result is None:
        return "Apply Safe Fixes", discord.ButtonStyle.success, False

    changed = list(result.get("changed") or [])
    attention = _repair_attention(result)
    error = str(result.get("error") or "").strip()

    if changed:
        return "Apply Safe Fixes", discord.ButtonStyle.success, False
    if bool(result.get("emergency_recovery_recommended")):
        return "Recovery Access Needed", discord.ButtonStyle.secondary, True
    if attention or error:
        return "Manual Discord Fix Required", discord.ButtonStyle.secondary, True
    return "Access Healthy", discord.ButtonStyle.secondary, True


def result_embed(result: dict[str, Any]) -> discord.Embed:
    applied = bool(result.get("applied"))
    changed = list(result.get("changed") or [])
    failed = list(result.get("failed") or [])
    manual = list(result.get("manual_actions") or [])
    mappings = list(result.get("missing_mappings") or [])
    notes = list(result.get("notes") or [])
    unchanged = list(result.get("unchanged") or [])
    attention = [*failed, *manual, *mappings]
    activity_scope = bool(result.get("include_activity_coverage"))
    emergency_recovery = bool(result.get("emergency_recovery_recommended"))
    emergency_count = int(result.get("emergency_recovery_count") or 0)
    temporary_admin_active = bool(result.get("temporary_admin_active"))

    if applied:
        title = "✅ Permission Repair Finished" if not attention else "⚠️ Permission Repair Partially Finished"
        summary = (
            f"Applied **{len(changed)}** safe target change(s). "
            f"**{len(attention)}** item(s) still need attention."
        )
    elif changed:
        title = "🛠️ Permission Repair Preview"
        action_label = "Fix All Safe Access" if activity_scope else "Apply Safe Fixes"
        summary = (
            f"Found **{len(changed)}** safe target change(s) across **{int(result.get('target_count') or 0)}** checked target(s). "
            f"Nothing changes until you press **{action_label}**."
        )
    elif emergency_recovery:
        title = "🔐 One-Time Recovery Access Needed"
        summary = (
            f"Discord has already self-locked **{emergency_count}** target(s) against Dank Shield's "
            "normal overwrite repair. Use the temporary recovery authorization below once, then "
            "return and press **Preview Again**."
        )
    elif attention or result.get("error"):
        title = "⚠️ Manual Discord Fix Required"
        summary = (
            "Dank Shield found **no safe overwrite changes it can apply itself**. "
            "Use the exact blocker guidance below, then run the preview again."
        )
    else:
        title = "✅ Bot Access Ready"
        summary = (
            f"Checked **{int(result.get('target_count') or 0)}** configured target(s). "
            "No safe permission repair is needed."
        )

    embed = discord.Embed(
        title=title,
        description=summary,
        color=discord.Color.green() if not attention and not result.get("error") else discord.Color.orange(),
    )
    embed.add_field(
        name="Scope",
        value=(
            "Activity access only: Diagnostics-reported bot-only View Channel / Read Message History / Manage Threads gaps. "
            "Member and staff overwrites are not changed."
            if activity_scope
            else "Setup channels only: saved setup targets, exact-name matches, and their managed ticket/staff children."
        ),
        inline=False,
    )
    embed.add_field(
        name="Safe fixes" if not applied else "Changed",
        value=_line_list(changed, empty="✅ No safe permission changes are needed."),
        inline=False,
    )
    if attention:
        embed.add_field(
            name="Needs your attention",
            value=_line_list(attention, empty="None"),
            inline=False,
        )
        if emergency_recovery:
            embed.add_field(
                name="One-time bulk recovery",
                value=(
                    "Press **Temporary Admin Recovery** and authorize Dank Shield for this server. Discord's "
                    "**Administrator** permission bypasses channel overwrites, which lets Fix Access repair all "
                    "already-locked bot overwrites in one pass. Return here, press **Preview Again**, then "
                    "**Fix All Safe Access**. This does not change member/staff overwrites."
                ),
                inline=False,
            )
        else:
            embed.add_field(
                name="What to do",
                value=(
                    "For a blocked channel, use **Specific Channel** to inspect it. Permission-overwrite repair requires "
                    "**Manage Roles** at the server level and **Manage Permissions** in that target. If a channel/category "
                    "deny is blocking Manage Permissions, remove that deny or explicitly allow it for Dank Shield in Discord, "
                    "then preview again. For missing mappings, use **Setup Plan & Server Items → Choose Roles & Channels**."
                ),
                inline=False,
            )
    if temporary_admin_active:
        embed.add_field(
            name="⚠️ Administrator currently enabled",
            value=(
                "Dank Shield does not require Administrator for normal operation. If you enabled it for this "
                "emergency repair, remove **Administrator** from the Dank Shield server role immediately after "
                "the repair is finished, then run **Preview Again** to confirm normal non-Administrator access."
            ),
            inline=False,
        )
    if notes:
        embed.add_field(name="Notes", value=_line_list(notes, max_rows=3), inline=False)
    embed.set_footer(text=f"Already safe: {len(unchanged)} target(s) • No unrelated member/staff visibility is changed")
    return embed


def _emergency_recovery_button(guild: discord.Guild, *, row: int = 1) -> discord.ui.Button | None:
    try:
        from stoney_verify.permission_repair import emergency_recovery_url

        url = emergency_recovery_url(guild)
    except Exception:
        url = ""
    if not url:
        return None
    return discord.ui.Button(
        label="Temporary Admin Recovery",
        emoji="🛟",
        style=discord.ButtonStyle.link,
        url=url,
        row=row,
    )


def _reauthorize_button(guild: discord.Guild, *, row: int = 1) -> discord.ui.Button | None:
    try:
        from stoney_verify.permission_repair import reauthorize_url

        url = reauthorize_url(guild)
    except Exception:
        url = ""
    if not url:
        return None
    return discord.ui.Button(
        label="Reauthorize Dank Shield",
        emoji="🔐",
        style=discord.ButtonStyle.link,
        url=url,
        row=row,
    )


async def _back_to_parent(interaction: discord.Interaction, parent: str) -> None:
    from stoney_verify.commands_ext import public_setup_recommend as recommend

    if str(parent or "security").strip().lower() == "logs":
        await recommend._open_advanced_logs_activity(interaction)
        return
    await recommend._open_advanced_security(interaction)


class PermissionRepairPreviewView(discord.ui.View):
    """Canonical preview controls that remember the setup parent and scope."""

    def __init__(
        self,
        *,
        guild: discord.Guild | None = None,
        parent: str = "security",
        include_activity_coverage: bool = False,
        result: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(timeout=900)
        self.parent = str(parent or "security").strip().lower()
        self.include_activity_coverage = bool(include_activity_coverage)

        label, style, disabled = _preview_action_state(result)
        if self.include_activity_coverage and label == "Apply Safe Fixes":
            label = "Fix All Safe Access"
        self.apply_fixes.label = label
        self.apply_fixes.style = style
        self.apply_fixes.disabled = disabled

        if guild is not None and (
            result is None or bool(result.get("reauthorize_recommended"))
        ):
            button = _reauthorize_button(guild)
            if button is not None:
                self.add_item(button)
        if (
            guild is not None
            and result is not None
            and bool(result.get("emergency_recovery_recommended"))
        ):
            button = _emergency_recovery_button(guild)
            if button is not None:
                self.add_item(button)

    @discord.ui.button(
        label="Apply Safe Fixes",
        emoji="🛠️",
        style=discord.ButtonStyle.success,
        custom_id="dank_setup_permission:apply",
        row=0,
    )
    async def apply_fixes(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await apply_permission_repair(
            interaction,
            parent=self.parent,
            include_activity_coverage=self.include_activity_coverage,
        )

    @discord.ui.button(
        label="Preview Again",
        emoji="🔍",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_permission:preview",
        row=0,
    )
    async def preview_again(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_permission_repair(
            interaction,
            parent=self.parent,
            include_activity_coverage=self.include_activity_coverage,
        )

    @discord.ui.button(
        label="Specific Channel",
        emoji="🎯",
        style=discord.ButtonStyle.primary,
        custom_id="dank_setup_permission:target",
        row=0,
    )
    async def target_repair(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from stoney_verify.permission_repair import open_target_permission_repair

        await open_target_permission_repair(interaction)

    @discord.ui.button(
        label="Back",
        emoji="⬅️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_permission:back",
        row=0,
    )
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _back_to_parent(interaction, self.parent)


class PermissionRepairResultView(discord.ui.View):
    """Canonical post-repair controls that remember the setup parent and scope."""

    def __init__(
        self,
        *,
        guild: discord.Guild | None = None,
        parent: str = "security",
        include_activity_coverage: bool = False,
        result: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(timeout=900)
        self.parent = str(parent or "security").strip().lower()
        self.include_activity_coverage = bool(include_activity_coverage)
        if guild is not None and (
            result is None or bool(result.get("reauthorize_recommended"))
        ):
            button = _reauthorize_button(guild)
            if button is not None:
                self.add_item(button)
        if (
            guild is not None
            and result is not None
            and bool(result.get("emergency_recovery_recommended"))
        ):
            button = _emergency_recovery_button(guild)
            if button is not None:
                self.add_item(button)

    @discord.ui.button(
        label="Preview Again",
        emoji="🔍",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_permission_done:preview",
        row=0,
    )
    async def preview_again(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_permission_repair(
            interaction,
            parent=self.parent,
            include_activity_coverage=self.include_activity_coverage,
        )

    @discord.ui.button(
        label="Specific Channel",
        emoji="🎯",
        style=discord.ButtonStyle.primary,
        custom_id="dank_setup_permission_done:target",
        row=0,
    )
    async def target_repair(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from stoney_verify.permission_repair import open_target_permission_repair

        await open_target_permission_repair(interaction)

    @discord.ui.button(
        label="Back",
        emoji="⬅️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank_setup_permission_done:back",
        row=0,
    )
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _back_to_parent(interaction, self.parent)


async def _claim_repair_interaction(
    interaction: discord.Interaction,
    *,
    action_name: str,
) -> bool:
    from stoney_verify.interaction_guard import safe_defer_interaction

    claimed = await safe_defer_interaction(
        interaction,
        ephemeral=True,
        action_name=action_name,
    )
    if not claimed:
        try:
            print(
                "❌ access_repair interaction claim failed "
                f"action={action_name} guild={getattr(getattr(interaction, 'guild', None), 'id', 0)} "
                f"user={getattr(getattr(interaction, 'user', None), 'id', 0)}"
            )
        except Exception:
            pass
    return claimed


async def open_permission_repair(
    interaction: discord.Interaction,
    *,
    parent: str = "security",
    include_activity_coverage: bool = False,
) -> None:
    from stoney_verify.commands_ext import public_setup_solid as solid

    if not await solid._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)

    if not await _claim_repair_interaction(
        interaction,
        action_name="access_repair_preview",
    ):
        return

    result = await preview_or_apply(
        guild,
        apply=False,
        include_activity_coverage=include_activity_coverage,
    )
    await solid._edit_or_followup(
        interaction,
        embed=result_embed(result),
        view=PermissionRepairPreviewView(
            guild=guild,
            parent=parent,
            include_activity_coverage=include_activity_coverage,
            result=result,
        ),
    )


async def apply_permission_repair(
    interaction: discord.Interaction,
    *,
    parent: str = "security",
    include_activity_coverage: bool = False,
) -> None:
    from stoney_verify.commands_ext import public_setup_solid as solid
    from stoney_verify.operation_queue import run_interaction_exclusive

    if not await solid._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)

    # Claim before queue/REST/database work. A failed acknowledgement must stop
    # the mutation path rather than continuing behind Discord's red banner.
    if not await _claim_repair_interaction(
        interaction,
        action_name="access_repair_apply",
    ):
        return

    async def job() -> dict[str, Any]:
        return await preview_or_apply(
            guild,
            apply=True,
            include_activity_coverage=include_activity_coverage,
        )

    result = await run_interaction_exclusive(
        interaction=interaction,
        operation_type="setup_permission_repair",
        action_label="Setup permission repair",
        factory=job,
        fingerprint={
            "guild_id": int(guild.id),
            "scope": "activity_access" if include_activity_coverage else "setup_permissions",
        },
        risk_level="moderate",
        concurrency_class="guild_config_write",
        concurrency_key="setup_permission_repair",
        timeout_seconds=180.0,
    )
    if result is None:
        return

    await solid._edit_or_followup(
        interaction,
        embed=result_embed(result),
        view=PermissionRepairResultView(
            guild=guild,
            parent=parent,
            include_activity_coverage=include_activity_coverage,
            result=result,
        ),
    )


__all__ = [
    "PermissionRepairPreviewView",
    "PermissionRepairResultView",
    "open_permission_repair",
    "apply_permission_repair",
    "preview_or_apply",
    "result_embed",
]
