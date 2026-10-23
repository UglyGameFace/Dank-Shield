from __future__ import annotations

"""4D.4 application-owner-only, two-step, explicitly gated canary console.

No automatic caller, new slash command, scheduled loop or bulk member lookup.
Delivery requires FOUR process-local target locks, two private PostgreSQL
migrations, durable audit writes, and a fresh affirmative member DM opt-in.
These switches are absent by default and are not configured by this PR.
"""

import asyncio
import os
from uuid import uuid4

import discord

from stoney_verify.action_canary_audit import (
    audit_ready_sync, begin_canary_audit_sync, finish_canary_audit_sync,
)
from stoney_verify.action_canary_delivery import (
    ENABLE_ENV, GUILD_ENV, USER_ENV, REVISION_ENV,
    _canary_enabled, dispatch_single_member_setup_canary,
)
from stoney_verify.action_center_postgres import ledger_ready_sync
from stoney_verify.action_delivery_readiness import inspect_member_setup_readiness
from stoney_verify.action_canary_readiness_report import read_canary_readiness
from stoney_verify.panel_lifecycle import PRIVATE_MENU_TTL_SECONDS
from .public_action_builder import _defer, _msg, _replace

OPERATOR_TIMEOUT_SECONDS = 8.0


async def _is_app_owner(interaction: discord.Interaction) -> bool:
    """Actual bot application owner, not server admin/staff or configured role."""
    try:
        return bool(await interaction.client.is_owner(interaction.user))
    except Exception:
        return False


def _target(guild: discord.Guild):
    """Use exactly the locked member/revision. Never search or fetch all users."""
    try:
        if os.getenv(ENABLE_ENV, "") != "1" or os.getenv(GUILD_ENV, "") != str(guild.id):
            return None
        uid, revision = int(os.getenv(USER_ENV, "")), int(os.getenv(REVISION_ENV, ""))
        if uid <= 0 or revision <= 0 or not _canary_enabled(guild.id, uid, revision):
            return None
        member = guild.get_member(uid)
        if not isinstance(member, discord.Member) or member.bot or member.guild is not guild:
            return None
        return member, revision
    except (ValueError, TypeError, OverflowError):
        return None


async def _canary_state(interaction: discord.Interaction) -> tuple[object, int, str]:
    if interaction.guild is None or not await _is_app_owner(interaction):
        return None, 0, "Only the Dank Shield application owner can inspect this console."
    selected = _target(interaction.guild)
    if selected is None:
        return None, 0, "Four exact canary gates are disabled/mismatched, or the selected member is not in guild cache."
    member, revision = selected
    try:
        checks = await asyncio.wait_for(
            asyncio.gather(
                asyncio.to_thread(ledger_ready_sync),
                asyncio.to_thread(audit_ready_sync),
            ),
            timeout=OPERATOR_TIMEOUT_SECONDS,
        )
        if checks != [True, True]:
            return None, 0, "Private claims and audit migrations are not both ready. No canary is authorized."
        inspection = await inspect_member_setup_readiness(
            member, expected_revision=revision,
        )
        if inspection.status != "preview_due" or not inspection.ledger_checked:
            return None, 0, (
                "Canary not due or owner/consent/history unavailable: "
                + inspection.status
            )
    except Exception:
        return None, 0, "Canary readiness could not be verified."
    return member, revision, "ready"


class ConfirmSingleCanaryModal(discord.ui.Modal, title="Confirm One Member Setup DM"):
    confirmation = discord.ui.TextInput(
        label="Type the exact confirmation phrase",
        placeholder="SEND guild_id/user_id/revision",
        min_length=6, max_length=100,
    )

    def __init__(self, owner_id: int, guild_id: int, target_id: int, revision: int):
        super().__init__(timeout=120)
        self.owner_id = owner_id
        self.guild_id = guild_id
        self.target_id = target_id
        self.revision = revision
        self._used = False

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if self._used:
            return await _msg(interaction, "This one-time confirmation was already submitted.")
        self._used = True  # Synchronous guard BEFORE any asynchronous work.
        expected = f"SEND {self.guild_id}/{self.target_id}/{self.revision}"
        if (
            interaction.user.id != self.owner_id
            or interaction.guild_id != self.guild_id
            or str(self.confirmation.value or "").strip() != expected
        ):
            return await _msg(interaction, "Confirmation rejected. No audit or message was created.")
        await _defer(interaction)
        if not await _is_app_owner(interaction):
            return await _msg(interaction, "Application-owner permission could not be verified.")
        member, revision, why = await _canary_state(interaction)
        if member is None or member.id != self.target_id or revision != self.revision:
            return await _msg(interaction, why if member is None else "Canary target changed. No message sent.")
        operation = uuid4()
        try:
            recorded = await asyncio.wait_for(
                asyncio.to_thread(
                    begin_canary_audit_sync,
                    operation, self.guild_id, self.target_id, self.owner_id, self.revision,
                ),
                timeout=OPERATOR_TIMEOUT_SECONDS,
            )
        except Exception:
            recorded = False
        if recorded is not True:
            return await _msg(interaction, "Durable operator audit unavailable. No message was attempted.")
        try:
            result = await dispatch_single_member_setup_canary(
                member, expected_revision=self.revision,
            )
        except Exception:
            result = None
        status = result.status if result is not None else "unavailable"
        claimed = bool(result.claimed) if result else False
        sent = bool(result.sent) if result else False
        try:
            closed = await asyncio.wait_for(
                asyncio.to_thread(
                    finish_canary_audit_sync, operation,
                    status=status, claimed=claimed, sent=sent,
                ),
                timeout=OPERATOR_TIMEOUT_SECONDS,
            )
        except Exception:
            closed = False
        if closed is not True:
            # A send could have happened already. Never encourage another.
            return await _msg(
                interaction, "Canary result cannot be audited completely. "
                "Do NOT retry. Inspect the durable request and claim tables.",
            )
        return await _msg(
            interaction,
            f"Single-recipient canary finished: **{status}**. "
            f"Claim recorded: **{claimed}**. Discord DM confirmed: **{sent}**. "
            "This operation cannot be replayed from the same confirmation.",
        )


class AppOwnerCanaryView(discord.ui.View):
    def __init__(self, user_id: int, guild_id: int, target_id: int, revision: int):
        super().__init__(timeout=PRIVATE_MENU_TTL_SECONDS)
        self.user_id = int(user_id)
        self.guild_id = int(guild_id)
        self.target_id = int(target_id)
        self.revision = int(revision)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Application ownership is the authority; a guild staff role is NOT
        # an additional prerequisite. Still bind this private view to opener.
        if (
            interaction.user.id != self.user_id
            or interaction.guild_id != self.guild_id
            or not await _is_app_owner(interaction)
        ):
            await _msg(interaction, "Only the original Dank Shield application owner can use this console.")
            return False
        return True

    @discord.ui.button(label="Readiness Report", emoji="🛡️", style=discord.ButtonStyle.secondary, row=0)
    async def report(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _defer(interaction)
        try:
            details = await read_canary_readiness(self.guild_id)
        except Exception:
            return await _msg(interaction, "Read-only readiness checks could not be completed.")
        def status(ok: bool) -> str:
            return "✅ verified by runtime" if ok else "⛔ not verified"
        e = discord.Embed(
            title="🛡️ One-Member Canary • Read-only Preflight",
            description=(
                "**No migration, no claim, no DM.** This checks the deployed process "
                "and its existing PostgreSQL permissions. A runtime SHA is NOT "
                "independent proof of what GitHub currently has on main."
            ),
            color=discord.Color.blurple(),
        )
        e.add_field(
            name="Deployed source observation",
            value=(
                "Declared SHA: `" + (details.declared_sha or "not exposed") + "`\n"
                "Source fingerprint: `" + details.source_fingerprint + "`\n"
                "GitHub main match: **not independently verified**"
            ),
            inline=False,
        )
        e.add_field(
            name="Private prerequisites",
            value=(
                "PostgreSQL configured: " + status(details.db_configured) + "\n"
                "Claim tables accessible: " + status(details.claims_ready) + "\n"
                "Audit table accessible: " + status(details.audit_ready) + "\n"
                "Exact target switches: " + status(details.flags_complete and details.target_guild_match)
            ),
            inline=False,
        )
        e.add_field(
            name="Remaining blockers",
            value="\n".join("• " + issue for issue in details.blockers)[:1024]
                  if details.blockers else "None identified by these limited checks.",
            inline=False,
        )
        e.set_footer(text="Read-only • App owner only • Never authorizes sending")
        await _replace(
            interaction, embed=e,
            view=AppOwnerCanaryView(self.user_id, self.guild_id, self.target_id, self.revision),
        )

    @discord.ui.button(label="Review & Confirm One DM", emoji="🔐", style=discord.ButtonStyle.danger, row=0)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _is_app_owner(interaction):
            return await _msg(interaction, "Only the application owner can authorize a canary.")
        if (
            interaction.user.id != self.user_id
            or interaction.guild_id != self.guild_id
            or not _canary_enabled(self.guild_id, self.target_id, self.revision)
        ):
            return await _msg(interaction, "This canary target is no longer approved.")
        await interaction.response.send_modal(
            ConfirmSingleCanaryModal(self.user_id, self.guild_id, self.target_id, self.revision)
        )

    @discord.ui.button(label="Back to Safety Schedule", emoji="↩️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from .public_action_schedule import open_schedule_panel
        await open_schedule_panel(interaction)


async def open_app_owner_canary_console(interaction: discord.Interaction) -> None:
    if interaction.guild is None or not await _is_app_owner(interaction):
        return await _msg(interaction, "Canary console is available only to the Dank Shield application owner.")
    await _defer(interaction)
    member, revision, reason = await _canary_state(interaction)
    ready = member is not None
    e = discord.Embed(
        title="🔐 Member Setup DM Canary • Operator Console",
        description=(
            "**One member / one revision only**. No automatic scheduling.\n"
            f"**Status:** {'Ready for explicit owner confirmation' if ready else 'Locked'}\n"
            f"**Reason:** {reason if not ready else 'Exact target, consent, limits and private ledgers verified'}\n\n"
            "A durable audit record must be committed before any claim/DM. "
            "Every attempt is final, even if sending fails."
        ),
        color=discord.Color.green() if ready else discord.Color.orange(),
    )
    if ready:
        e.add_field(
            name="Approved target",
            value=f"Guild: `{interaction.guild.id}`\n"
                  f"Member: `{member.id}`\nRevision: `{revision}`\n"
                  f"Confirm phrase: `SEND {interaction.guild.id}/{member.id}/{revision}`",
            inline=False,
        )
        e.set_footer(text="Private • App owner only • Explicit confirmation • Never auto-retry")
    view = AppOwnerCanaryView(
        interaction.user.id, interaction.guild.id,
        member.id if ready else 0, revision if ready else 0,
    )
    if not ready:
        view.confirm.disabled = True
    await _replace(interaction, embed=e, view=view)
