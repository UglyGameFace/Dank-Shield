from __future__ import annotations

"""Confirmed member cleanup commands for /dank members.

This module attaches explicit cleanup workflows to the existing `/dank members`
group without changing the scan/review console internals.
"""

import asyncio
from dataclasses import dataclass
from typing import Any, Optional

import discord
from discord import app_commands

from .common import reply_once
from .public_members_group import members_group
from stoney_verify.members_new.activity_service import (
    InactiveMemberCandidate,
    InactiveScanOptions,
    InactiveScanReport,
    get_last_scan,
    scan_inactive_members,
)
from stoney_verify.members_new.cleanup_service import (
    MemberCleanupRequest,
    MemberCleanupValidation,
    MemberCleanupResult,
    execute_member_cleanup,
    validate_member_cleanup,
)
from stoney_verify.members_new.cleanup_settings_service import (
    MemberCleanupSettings,
    get_cleanup_settings,
    update_cleanup_settings,
)

_REGISTERED = False
_QUEUE_MAX_LIMIT = 20
_QUEUE_DEFAULT_LIMIT = 10


@dataclass
class QueuePreviewItem:
    candidate: InactiveMemberCandidate
    validation: MemberCleanupValidation


def _trim(text: str, limit: int = 3900) -> str:
    raw = str(text or "")
    return raw if len(raw) <= limit else raw[: max(0, limit - 1)] + "…"


def _can_cleanup_members(interaction: discord.Interaction) -> bool:
    try:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return False
        perms = interaction.user.guild_permissions
        return bool(perms.administrator or perms.manage_guild or perms.kick_members)
    except Exception:
        return False


def _can_manage_cleanup_settings(interaction: discord.Interaction) -> bool:
    try:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return False
        perms = interaction.user.guild_permissions
        return bool(perms.administrator or perms.manage_guild)
    except Exception:
        return False


async def _require_cleanup_permission(interaction: discord.Interaction) -> bool:
    if interaction.guild is None:
        await reply_once(interaction, {"content": "❌ This must be used inside a server.", "ephemeral": True})
        return False
    if not _can_cleanup_members(interaction):
        await reply_once(
            interaction,
            {"content": "❌ Confirmed cleanup requires Administrator, Manage Server, or Kick Members.", "ephemeral": True},
        )
        return False
    return True


async def _require_cleanup_settings_permission(interaction: discord.Interaction) -> bool:
    if interaction.guild is None:
        await reply_once(interaction, {"content": "❌ This must be used inside a server.", "ephemeral": True})
        return False
    if not _can_manage_cleanup_settings(interaction):
        await reply_once(
            interaction,
            {"content": "❌ Cleanup settings require Administrator or Manage Server.", "ephemeral": True},
        )
        return False
    return True


def _result_embed(title: str, description: str, *, ok: bool) -> discord.Embed:
    return discord.Embed(
        title=title,
        description=_trim(description, 3900),
        color=discord.Color.green() if ok else discord.Color.orange(),
    )


def _safe_name(value: Any) -> str:
    raw = str(value or "Unknown member").strip()
    try:
        raw = discord.utils.escape_markdown(raw, as_needed=True)
    except Exception:
        pass
    return _trim(raw, 64)


def _candidate_is_queue_eligible(candidate: InactiveMemberCandidate, *, include_low_confidence: bool) -> tuple[bool, str]:
    try:
        if not bool(getattr(candidate, "removable", False)):
            return False, "scan did not mark this user as queue-ready"
        if bool(getattr(candidate, "protected", False)):
            return False, "protected by scan safety"
        if bool(getattr(candidate, "cannot_remove", False)):
            return False, "blocked by scan safety"
        confidence = str(getattr(candidate, "confidence", "") or "").lower()
        if confidence == "low" and not include_low_confidence:
            return False, "low confidence needs one-user manual review"
        status = str(getattr(candidate, "status", "") or "")
        if status not in {"Review candidate", "Needs review"}:
            return False, f"status is {status or 'unknown'}"
        return True, "eligible"
    except Exception:
        return False, "could not read scan candidate"


def _queue_source_summary(report: InactiveScanReport) -> str:
    try:
        return (
            f"Scan age: <t:{int(report.scanned_at.timestamp())}:R> • "
            f"Found: {len(report.candidates)} • "
            f"Data: {report.data_confidence_label} ({report.data_coverage_percent}%)"
        )
    except Exception:
        return "Latest scan loaded."


def _format_queue_lines(items: list[QueuePreviewItem], *, limit: int = 15) -> str:
    lines: list[str] = []
    for idx, item in enumerate(items[:limit], start=1):
        c = item.candidate
        days = "?d" if c.inactivity_days is None else f"{c.inactivity_days}d"
        name = _safe_name(getattr(c, "display_name", None) or item.validation.target_display_name)
        lines.append(f"`{idx}.` **{name}** (`{c.user_id}`) • {days} quiet • {c.confidence}")
    extra = max(0, len(items) - limit)
    if extra:
        lines.append(f"…and **{extra}** more queued member(s).")
    return _trim("\n".join(lines) if lines else "None", 1800)


def _format_blocked_lines(blocked: list[tuple[InactiveMemberCandidate, str]], *, limit: int = 8) -> str:
    if not blocked:
        return "✅ No scan candidates were skipped before validation."
    lines: list[str] = []
    for idx, (candidate, reason) in enumerate(blocked[:limit], start=1):
        name = _safe_name(getattr(candidate, "display_name", None) or candidate.user_id)
        lines.append(f"`{idx}.` **{name}** • {reason}")
    extra = max(0, len(blocked) - limit)
    if extra:
        lines.append(f"…and **{extra}** more skipped item(s).")
    return _trim("\n".join(lines), 1200)


def _settings_embed(settings: MemberCleanupSettings) -> discord.Embed:
    updated = "Never" if settings.updated_at is None else f"<t:{int(settings.updated_at.timestamp())}:R>"
    updated_by = "Unknown" if not settings.updated_by else f"<@{settings.updated_by}>"
    embed = discord.Embed(
        title="🧹 Member Cleanup Settings",
        description="Server-level defaults for `/dank members cleanup-queue`.",
        color=discord.Color.green() if settings.require_queue_confirmation else discord.Color.orange(),
    )
    embed.add_field(
        name="Current Mode",
        value=(
            f"Queue confirmation: **{settings.mode_label}**\n"
            f"Low-confidence queue: **{settings.low_confidence_label}**\n"
            f"Default queue size: **{settings.default_queue_limit}**"
        ),
        inline=False,
    )
    embed.add_field(
        name="Storage",
        value=(
            f"Persistence: **{'Supabase' if settings.persisted else 'Memory/default'}**\n"
            f"Storage note: `{_trim(settings.storage_note, 700)}`\n"
            f"Last updated: {updated}\n"
            f"Updated by: {updated_by}"
        ),
        inline=False,
    )
    embed.add_field(
        name="Safety Note",
        value=(
            "Confirmation is required by default. Turning it off means the queue command will process immediately after showing the preview text, "
            "but every member still receives final permission, role, lock, staff, owner, and bot checks."
        ),
        inline=False,
    )
    return embed


def _queue_context_line(settings: MemberCleanupSettings, *, include_low_confidence: bool, safe_limit: int) -> str:
    return (
        f"Queue confirmation: **{settings.mode_label}**\n"
        f"Queue limit: **{safe_limit}**\n"
        f"Low-confidence included: **{'Yes' if include_low_confidence else 'No'}**"
    )


async def _load_report_for_queue(guild: discord.Guild) -> tuple[InactiveScanReport, bool]:
    report = get_last_scan(int(guild.id))
    if report is not None:
        return report, False
    report = await scan_inactive_members(guild, InactiveScanOptions())
    return report, True


async def _build_queue_preview(
    guild: discord.Guild,
    *,
    actor_user_id: int,
    limit: int,
    include_low_confidence: bool,
) -> tuple[InactiveScanReport, bool, list[QueuePreviewItem], list[tuple[InactiveMemberCandidate, str]], list[tuple[InactiveMemberCandidate, MemberCleanupValidation]]]:
    report, fresh_scan = await _load_report_for_queue(guild)
    queued: list[QueuePreviewItem] = []
    skipped: list[tuple[InactiveMemberCandidate, str]] = []
    validation_blocked: list[tuple[InactiveMemberCandidate, MemberCleanupValidation]] = []

    for candidate in report.candidates:
        if len(queued) >= limit:
            break
        ok, reason = _candidate_is_queue_eligible(candidate, include_low_confidence=include_low_confidence)
        if not ok:
            skipped.append((candidate, reason))
            continue
        request = MemberCleanupRequest(
            guild_id=int(guild.id),
            target_user_id=int(candidate.user_id),
            actor_user_id=int(actor_user_id),
            reason="Confirmed inactive verified/resident cleanup queue",
        )
        validation = await validate_member_cleanup(guild, request)
        if validation.ok:
            queued.append(QueuePreviewItem(candidate=candidate, validation=validation))
        else:
            validation_blocked.append((candidate, validation))

    return report, fresh_scan, queued, skipped, validation_blocked


async def _process_queue_items(
    interaction: discord.Interaction,
    *,
    items: list[QueuePreviewItem],
    reason: str,
) -> tuple[list[str], list[str], list[str]]:
    removed: list[str] = []
    blocked: list[str] = []
    failed: list[str] = []
    if interaction.guild is None:
        return removed, blocked, ["Guild missing while processing queue."]

    for item in items:
        request = MemberCleanupRequest(
            guild_id=int(interaction.guild.id),
            target_user_id=int(item.candidate.user_id),
            actor_user_id=int(interaction.user.id),
            reason=reason,
        )
        result: MemberCleanupResult = await execute_member_cleanup(interaction.guild, request)
        line = f"**{_safe_name(result.target_display_name)}** (`{result.target_user_id}`) — {result.status}"
        if result.ok:
            removed.append(line)
        elif "blocked" in str(result.status).lower() or "not found" in str(result.status).lower():
            blocked.append(line)
        else:
            failed.append(line)
        await asyncio.sleep(0.35)
    return removed, blocked, failed


def _queue_result_embed(*, removed: list[str], blocked: list[str], failed: list[str]) -> discord.Embed:
    body = (
        f"Completed queue.\n\n"
        f"✅ Removed: **{len(removed)}**\n"
        f"⛔ Blocked/skipped: **{len(blocked)}**\n"
        f"⚠️ Failed: **{len(failed)}**"
    )
    if removed:
        body += "\n\n**Removed**\n" + _trim("\n".join(removed[:10]), 1000)
    if blocked:
        body += "\n\n**Blocked / skipped by final checks**\n" + _trim("\n".join(blocked[:8]), 900)
    if failed:
        body += "\n\n**Failed**\n" + _trim("\n".join(failed[:8]), 900)
    return _result_embed("🧹 Cleanup Queue Result", body, ok=not failed)


class ConfirmMemberCleanupView(discord.ui.View):
    def __init__(self, request: MemberCleanupRequest) -> None:
        super().__init__(timeout=180)
        self.request = request
        self.done = False

    @discord.ui.button(label="Confirm Remove", emoji="✅", style=discord.ButtonStyle.danger)
    async def confirm_remove(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.done:
            return await reply_once(interaction, {"content": "This cleanup request was already handled.", "ephemeral": True})
        if not await _require_cleanup_permission(interaction):
            return
        if interaction.guild is None:
            return
        if int(interaction.user.id) != int(self.request.actor_user_id):
            return await reply_once(interaction, {"content": "Only the staff member who opened this confirmation can confirm it.", "ephemeral": True})

        await interaction.response.defer(ephemeral=True)
        self.done = True
        for child in self.children:
            try:
                child.disabled = True
            except Exception:
                pass
        result = await execute_member_cleanup(interaction.guild, self.request)
        body = (
            f"Target: **{result.target_display_name}** (`{result.target_user_id}`)\n"
            f"Status: **{result.status}**\n\n"
            f"Why: {result.reason_text}"
        )
        if result.warnings:
            body += "\n\nWarnings:\n" + "\n".join(f"• {warning}" for warning in result.warnings[:5])
        await interaction.edit_original_response(embed=_result_embed("🧹 Cleanup Result", body, ok=result.ok), view=self)

    @discord.ui.button(label="Cancel", emoji="✋", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_cleanup_permission(interaction):
            return
        if int(interaction.user.id) != int(self.request.actor_user_id):
            return await reply_once(interaction, {"content": "Only the staff member who opened this confirmation can cancel it.", "ephemeral": True})
        self.done = True
        for child in self.children:
            try:
                child.disabled = True
            except Exception:
                pass
        await interaction.response.edit_message(
            embed=_result_embed("Cleanup Cancelled", "No action was taken.", ok=False),
            view=self,
        )


class ConfirmCleanupQueueView(discord.ui.View):
    def __init__(self, *, actor_user_id: int, items: list[QueuePreviewItem], reason: str) -> None:
        super().__init__(timeout=240)
        self.actor_user_id = int(actor_user_id)
        self.items = list(items)
        self.reason = str(reason or "Confirmed inactive verified/resident cleanup queue")[:450]
        self.done = False

    @discord.ui.button(label="Confirm Queue", emoji="✅", style=discord.ButtonStyle.danger)
    async def confirm_queue(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.done:
            return await reply_once(interaction, {"content": "This queue was already handled.", "ephemeral": True})
        if not await _require_cleanup_permission(interaction):
            return
        if interaction.guild is None:
            return
        if int(interaction.user.id) != self.actor_user_id:
            return await reply_once(interaction, {"content": "Only the staff member who opened this queue can confirm it.", "ephemeral": True})

        await interaction.response.defer(ephemeral=True)
        self.done = True
        for child in self.children:
            try:
                child.disabled = True
            except Exception:
                pass
        await interaction.edit_original_response(
            embed=_result_embed("🧹 Processing Cleanup Queue", f"Processing **{len(self.items)}** queued member(s). Final safety checks are running again now.", ok=False),
            view=self,
        )
        removed, blocked, failed = await _process_queue_items(interaction, items=self.items, reason=self.reason)
        await interaction.edit_original_response(embed=_queue_result_embed(removed=removed, blocked=blocked, failed=failed), view=self)

    @discord.ui.button(label="Cancel", emoji="✋", style=discord.ButtonStyle.secondary)
    async def cancel_queue(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_cleanup_permission(interaction):
            return
        if int(interaction.user.id) != self.actor_user_id:
            return await reply_once(interaction, {"content": "Only the staff member who opened this queue can cancel it.", "ephemeral": True})
        self.done = True
        for child in self.children:
            try:
                child.disabled = True
            except Exception:
                pass
        await interaction.response.edit_message(embed=_result_embed("Cleanup Queue Cancelled", "No action was taken.", ok=False), view=self)


@members_group.command(name="cleanup-user", description="Confirm cleanup for one reviewed inactive verified/resident member.")
@app_commands.describe(
    user="The server member to review for confirmed cleanup.",
    reason="Reason stored in Discord audit log and Dank Shield activity history.",
)
async def members_cleanup_user(
    interaction: discord.Interaction,
    user: discord.Member,
    reason: str = "Confirmed inactive verified/resident cleanup",
) -> None:
    if not await _require_cleanup_permission(interaction):
        return
    if interaction.guild is None:
        return

    request = MemberCleanupRequest(
        guild_id=int(interaction.guild.id),
        target_user_id=int(user.id),
        actor_user_id=int(interaction.user.id),
        reason=reason,
    )
    await interaction.response.defer(ephemeral=True, thinking=True)
    validation = await validate_member_cleanup(interaction.guild, request)
    body = (
        f"Target: {user.mention} **{validation.target_display_name}** (`{validation.target_user_id}`)\n"
        f"Status: **{validation.status}**\n\n"
        f"Checks:\n" + "\n".join(f"• {item}" for item in validation.reasons[:8])
    )
    if validation.warnings:
        body += "\n\nWarnings:\n" + "\n".join(f"• {warning}" for warning in validation.warnings[:5])

    if not validation.ok:
        return await interaction.followup.send(
            embed=_result_embed("⛔ Cleanup Blocked", body, ok=False),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    body += (
        "\n\nPress **Confirm Remove** to remove this member from the server. "
        "This action is immediate and will be recorded."
    )
    await interaction.followup.send(
        embed=_result_embed("⚠️ Confirm Member Cleanup", body, ok=False),
        view=ConfirmMemberCleanupView(request),
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


@members_group.command(name="cleanup-queue", description="Review and process a queue from the latest member scan.")
@app_commands.describe(
    limit="Maximum queue size. Leave blank to use cleanup settings.",
    include_low_confidence="Override cleanup settings for low-confidence users.",
    reason="Reason stored in Discord audit log and Dank Shield activity history.",
)
async def members_cleanup_queue(
    interaction: discord.Interaction,
    limit: int = 0,
    include_low_confidence: Optional[bool] = None,
    reason: str = "Confirmed inactive verified/resident cleanup queue",
) -> None:
    if not await _require_cleanup_permission(interaction):
        return
    if interaction.guild is None:
        return

    settings = await get_cleanup_settings(int(interaction.guild.id))
    safe_limit = max(1, min(int(limit or settings.default_queue_limit or _QUEUE_DEFAULT_LIMIT), _QUEUE_MAX_LIMIT))
    include_low = bool(settings.allow_low_confidence_queue if include_low_confidence is None else include_low_confidence)

    await interaction.response.defer(ephemeral=True, thinking=True)
    report, fresh_scan, queued, skipped, validation_blocked = await _build_queue_preview(
        interaction.guild,
        actor_user_id=int(interaction.user.id),
        limit=safe_limit,
        include_low_confidence=include_low,
    )

    if not queued:
        body = (
            "No queue-ready users were found.\n\n"
            f"{_queue_source_summary(report)}\n\n"
            f"{_queue_context_line(settings, include_low_confidence=include_low, safe_limit=safe_limit)}\n\n"
            "Try selecting users one-by-one from `/dank members scan`, or include low-confidence users only if you are ready to manually review weaker evidence."
        )
        if skipped:
            body += "\n\n**Skipped before validation**\n" + _format_blocked_lines(skipped)
        if validation_blocked:
            body += "\n\n**Blocked by final validation**\n" + _trim("\n".join(f"• {_safe_name(c.display_name)} — {v.status}" for c, v in validation_blocked[:8]), 900)
        return await interaction.edit_original_response(embed=_result_embed("🧹 Cleanup Queue Empty", body, ok=False))

    body = (
        f"{'This is a confirmation screen. Nothing has happened yet.' if settings.require_queue_confirmation else 'Auto-process mode is enabled. Processing starts from this message.'}\n\n"
        f"{_queue_source_summary(report)}\n"
        f"Fresh scan run for queue: **{'Yes' if fresh_scan else 'No, used latest scan'}**\n"
        f"{_queue_context_line(settings, include_low_confidence=include_low, safe_limit=safe_limit)}\n\n"
        f"**Queued members ({len(queued)})**\n{_format_queue_lines(queued)}"
    )

    if validation_blocked:
        body += "\n\n**Blocked by final validation**\n" + _trim("\n".join(f"• {_safe_name(c.display_name)} — {v.status}" for c, v in validation_blocked[:6]), 800)

    if settings.require_queue_confirmation:
        body += "\n\nPress **Confirm Queue** to process these members one by one with final safety checks. Press **Cancel** to do nothing."
        return await interaction.edit_original_response(
            embed=_result_embed("⚠️ Confirm Cleanup Queue", body, ok=False),
            view=ConfirmCleanupQueueView(actor_user_id=int(interaction.user.id), items=queued, reason=reason),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    await interaction.edit_original_response(
        embed=_result_embed("🧹 Processing Cleanup Queue", body, ok=False),
        allowed_mentions=discord.AllowedMentions.none(),
    )
    removed, blocked, failed = await _process_queue_items(interaction, items=queued, reason=reason)
    await interaction.edit_original_response(embed=_queue_result_embed(removed=removed, blocked=blocked, failed=failed))


@members_group.command(name="cleanup-settings", description="View or change member cleanup queue settings.")
@app_commands.describe(
    require_queue_confirmation="Require Confirm Queue before processing. Default/safest: true.",
    allow_low_confidence_queue="Allow low-confidence users into cleanup queues by default. Default: false.",
    default_queue_limit="Default queue size when cleanup-queue limit is blank. Max 20.",
)
async def members_cleanup_settings(
    interaction: discord.Interaction,
    require_queue_confirmation: Optional[bool] = None,
    allow_low_confidence_queue: Optional[bool] = None,
    default_queue_limit: Optional[int] = None,
) -> None:
    if not await _require_cleanup_settings_permission(interaction):
        return
    if interaction.guild is None:
        return

    changed = any(value is not None for value in (require_queue_confirmation, allow_low_confidence_queue, default_queue_limit))
    if changed:
        settings = await update_cleanup_settings(
            int(interaction.guild.id),
            actor_id=int(interaction.user.id),
            require_queue_confirmation=require_queue_confirmation,
            allow_low_confidence_queue=allow_low_confidence_queue,
            default_queue_limit=default_queue_limit,
        )
        title = "✅ Cleanup Settings Updated"
    else:
        settings = await get_cleanup_settings(int(interaction.guild.id))
        title = "🧹 Cleanup Settings"
    embed = _settings_embed(settings)
    embed.title = title
    await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


def register_public_members_cleanup_group_commands(bot: Any, tree: Any) -> None:
    global _REGISTERED
    _ = bot, tree
    if _REGISTERED:
        return
    try:
        print("✅ public_members_cleanup_group: /dank members cleanup-user, cleanup-queue, cleanup-settings available")
        _REGISTERED = True
    except Exception as e:
        print(f"⚠️ public_members_cleanup_group failed: {repr(e)}")
        raise


__all__ = ["register_public_members_cleanup_group_commands"]
