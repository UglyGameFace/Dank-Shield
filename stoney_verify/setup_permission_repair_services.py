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
    from stoney_verify.startup_guards import setup_permission_repair_guard as legacy

    me = legacy._bot_member(guild)
    if me is None:
        return ["Dank Shield could not resolve its bot member in this server."]

    blockers: list[str] = []
    perms = me.guild_permissions
    if not (perms.manage_channels or perms.administrator):
        blockers.append("Dank Shield is missing **Manage Channels** at the server level.")
    if not (perms.view_channel or perms.administrator):
        blockers.append("Dank Shield is missing baseline **View Channels** access.")
    if not (perms.manage_roles or perms.administrator):
        blockers.append("Dank Shield is missing **Manage Roles**; role-related setup repairs still require it.")
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


def _activity_coverage_channel(channel: Any) -> bool:
    return isinstance(channel, (discord.TextChannel, discord.ForumChannel)) or callable(getattr(channel, "history", None))


def _activity_coverage_expected(channel: Any) -> discord.PermissionOverwrite:
    expected = discord.PermissionOverwrite(view_channel=True, read_message_history=True)
    if isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
        expected.manage_threads = True
    return expected


def _activity_coverage_needs_repair(channel: Any, me: discord.Member) -> bool:
    try:
        permissions = channel.permissions_for(me)
    except Exception:
        return True
    if not bool(getattr(permissions, "view_channel", False)):
        return True
    if not bool(getattr(permissions, "read_message_history", False)):
        return True
    return bool(
        isinstance(channel, (discord.TextChannel, discord.ForumChannel))
        and not getattr(permissions, "manage_threads", False)
    )


def _merge_activity_coverage_targets(
    guild: discord.Guild,
    targets: list[Any],
    seen: set[int],
    notes: list[str],
) -> None:
    """Opt-in bot-only activity repair. Member visibility is not changed."""
    from stoney_verify.startup_guards import setup_permission_repair_guard as legacy

    me = legacy._bot_member(guild)
    if not isinstance(me, discord.Member):
        return

    by_channel = {
        int(getattr(item.channel, "id", 0) or 0): item
        for item in targets
        if int(getattr(item.channel, "id", 0) or 0) > 0
    }
    repair_count = 0
    for channel in list(getattr(guild, "channels", []) or []):
        if not _activity_coverage_channel(channel) or not _activity_coverage_needs_repair(channel, me):
            continue
        cid = int(getattr(channel, "id", 0) or 0)
        if cid <= 0:
            continue
        existing = by_channel.get(cid)
        if existing is not None:
            current = existing.overwrites.get(me, discord.PermissionOverwrite())
            current.view_channel = True
            current.read_message_history = True
            if isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
                current.manage_threads = True
            existing.overwrites[me] = current
        else:
            legacy._add_target(
                targets,
                seen,
                channel,
                "Authoritative activity coverage",
                {me: _activity_coverage_expected(channel)},
            )
            if targets:
                by_channel[cid] = targets[-1]
        repair_count += 1

    if repair_count:
        notes.append(
            f"Activity access scope: {repair_count} channel(s) need Dank Shield bot-only "
            "View Channel / Read Message History / Manage Threads access. Member visibility is not changed."
        )


async def _build_expanded_targets(
    guild: discord.Guild,
    *,
    include_activity_coverage: bool = False,
) -> tuple[list[Any], list[str], list[str], list[str]]:
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

    if include_activity_coverage:
        _merge_activity_coverage_targets(guild, targets, seen, notes)

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
    hard_blockers = [item for item in blockers if "Manage Channels" in item or "could not resolve" in item]
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
    me = legacy._bot_member(guild)

    for item in targets:
        channel = item.channel
        if legacy._channel_manage_missing(channel, me):
            manual_actions.append(
                f"{legacy._channel_label(channel)}: Discord blocks Dank Shield from managing this channel/category."
            )
            continue

        pending_labels: list[str] = []
        applied_labels: list[str] = []
        for target, expected in item.overwrites.items():
            try:
                current = channel.overwrites_for(target)
            except Exception:
                current = discord.PermissionOverwrite()
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
                failed.append(
                    f"{legacy._channel_label(channel)} → {label}: Discord denied Manage Channels."
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

    if apply:
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
    }


def _line_list(lines: list[str], *, empty: str = "None", limit: int = 760, max_rows: int = 5) -> str:
    clean = [str(line or "").strip() for line in lines if str(line or "").strip()]
    if not clean:
        return empty
    shown = clean[: max(1, max_rows)]
    if len(clean) > len(shown):
        shown.append(f"…and {len(clean) - len(shown)} more")
    return "\n".join(shown)[:limit]


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

    if applied:
        title = "✅ Permission Repair Finished" if not attention else "⚠️ Permission Repair Partially Finished"
        summary = (
            f"Applied **{len(changed)}** safe target change(s). "
            f"**{len(attention)}** item(s) still need attention."
        )
    else:
        title = "🛠️ Permission Repair Preview"
        summary = (
            f"Found **{len(changed)}** safe target change(s) across **{int(result.get('target_count') or 0)}** checked target(s). "
            "Nothing changes until you press **Apply Safe Fixes**."
        )

    embed = discord.Embed(
        title=title,
        description=summary,
        color=discord.Color.green() if not attention and not result.get("error") else discord.Color.orange(),
    )
    embed.add_field(
        name="Scope",
        value=(
            "Activity access only: setup targets plus bot-only history/thread access requested from **Check Bot Access**."
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
        embed.add_field(
            name="What to do",
            value=(
                "For a blocked channel, use **Specific Channel** to inspect it. If Discord says Dank Shield lacks "
                "Manage Channels there, fix the bot role/channel deny or use **Reauthorize Dank Shield**, then preview again. "
                "For missing mappings, use **Setup Plan & Server Items → Choose Roles & Channels**."
            ),
            inline=False,
        )
    if notes:
        embed.add_field(name="Notes", value=_line_list(notes, max_rows=3), inline=False)
    embed.set_footer(text=f"Already safe: {len(unchanged)} target(s) • No unrelated member/staff visibility is changed")
    return embed


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
    ) -> None:
        super().__init__(timeout=900)
        self.parent = str(parent or "security").strip().lower()
        self.include_activity_coverage = bool(include_activity_coverage)
        if guild is not None:
            button = _reauthorize_button(guild)
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
    ) -> None:
        super().__init__(timeout=900)
        self.parent = str(parent or "security").strip().lower()
        self.include_activity_coverage = bool(include_activity_coverage)
        if guild is not None:
            button = _reauthorize_button(guild)
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

    await solid._safe_defer_update(interaction)
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

    # Component update defer, not thinking=True. The original ephemeral card is
    # edited when the queued job completes, so Discord cannot leave a permanent
    # "Dank Shield is thinking…" placeholder behind.
    await solid._safe_defer_update(interaction)

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
