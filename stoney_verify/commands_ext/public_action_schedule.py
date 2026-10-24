from __future__ import annotations

"""Private manager-only Action Center 4C schedule safety settings and preview."""

from datetime import datetime, timezone

import discord

from stoney_verify.action_builder_policy import load_action_builder, preview_rules
from stoney_verify.action_center_service import load_member_actions
from stoney_verify.action_schedule_policy import (
    ActionSchedule, edit_schedule, load_schedule, preview_candidate, save_schedule,
)
from .public_action_builder import _OwnedStaffView, _defer, _msg, _replace
from .public_staff_scope import scoped_interaction_is_staff


def _embed(schedule: ActionSchedule) -> discord.Embed:
    e = discord.Embed(
        title="🕓 Action Reminder Safety Schedule",
        description=(
            f"**Draft revision {schedule.revision}** · **🔒 Delivery disabled**\n"
            "Plan quiet hours, retry spacing and attempt limits before delivery exists. "
            "Nothing on this page sends reminders."
        ),
        color=discord.Color.blurple(),
    )
    e.add_field(name="First wait", value=f"{schedule.first_delay_hours} hours after join")
    e.add_field(name="Retry spacing", value=f"{schedule.repeat_hours} hours minimum")
    e.add_field(name="Maximum attempts", value=str(schedule.max_attempts))
    e.add_field(
        name="Quiet hours",
        value=f"{schedule.quiet_start:02d}:00–{schedule.quiet_end:02d}:00 ({schedule.timezone})"
              + (" • disabled" if schedule.quiet_start == schedule.quiet_end else ""),
        inline=False,
    )
    e.add_field(name="Guild daily cap", value=str(schedule.max_guild_per_day))
    e.set_footer(text="Draft only • No messages • Uses canonical completion truth")
    return e


class ScheduleModal(discord.ui.Modal, title="Draft Reminder Safety"):
    windows = discord.ui.TextInput(
        label="First wait / repeat (hours)", placeholder="24/72", max_length=6,
    )
    attempts = discord.ui.TextInput(label="Maximum attempts (1-3)", placeholder="2", max_length=1)
    quiet = discord.ui.TextInput(label="Quiet hours HH-HH (00-00 off)", placeholder="22-08", max_length=5)
    timezone = discord.ui.TextInput(label="IANA timezone", placeholder="America/New_York", max_length=90)
    cap = discord.ui.TextInput(label="Max sends per guild/day (1-10)", placeholder="5", max_length=2)

    def __init__(self, owner: int, guild_id: int, saved: ActionSchedule) -> None:
        super().__init__(timeout=300)
        self.owner = owner
        self.guild_id = guild_id
        self.revision = saved.revision
        self.windows.default = f"{saved.first_delay_hours}/{saved.repeat_hours}"
        self.attempts.default = str(saved.max_attempts)
        self.quiet.default = f"{saved.quiet_start:02}-{saved.quiet_end:02}"
        self.timezone.default = saved.timezone
        self.cap.default = str(saved.max_guild_per_day)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if (
            interaction.user.id != self.owner or interaction.guild_id != self.guild_id
            or not scoped_interaction_is_staff(interaction)
        ):
            return await _msg(interaction, "Staff permission required to edit this schedule.")
        try:
            if "/" not in self.windows.value or "-" not in self.quiet.value:
                raise ValueError("Use 24/72 for timing and 22-08 for quiet hours.")
            first, repeat = (int(v.strip()) for v in self.windows.value.split("/", 1))
            start, end = (int(v.strip()) for v in self.quiet.value.split("-", 1))
            tries = int(self.attempts.value)
            cap = int(self.cap.value)
        except (ValueError, TypeError):
            return await _msg(interaction, "Enter valid numbers. Timing: 24/72; quiet hours: 22-08.")
        await _defer(interaction)
        try:
            original, current = await load_schedule(self.guild_id, refresh=True)
            if current.revision != self.revision:
                return await _msg(interaction, "The schedule changed. Reopen it before saving.")
            changed = edit_schedule(
                current, first_delay_hours=first, repeat_hours=repeat,
                max_attempts=tries, quiet_start=start, quiet_end=end,
                timezone=str(self.timezone.value).strip(), max_guild_per_day=cap,
            )
            ok, _saved = await save_schedule(
                self.guild_id, expected_config=original, schedule=changed,
            )
            if not ok:
                return await _msg(interaction, "Another manager saved newer settings. Reopen to avoid overwriting.")
        except ValueError as exc:
            return await _msg(interaction, str(exc)[:180])
        except Exception:
            return await _msg(interaction, "The schedule database couldn't confirm the save. Nothing was enabled.")
        await open_schedule_panel(interaction)


class ScheduleView(_OwnedStaffView):
    def __init__(self, owner: int, guild_id: int, schedule: ActionSchedule) -> None:
        super().__init__(owner, guild_id)
        self.schedule = schedule

    @discord.ui.button(label="Edit Timing & Limits", emoji="⚙️", style=discord.ButtonStyle.primary)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(ScheduleModal(self.user_id, self.guild_id, self.schedule))

    @discord.ui.button(label="Preview Timing", emoji="🔎", style=discord.ButtonStyle.secondary)
    async def preview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not isinstance(interaction.user, discord.Member):
            return await _msg(interaction, "Preview this from your server.")
        await _defer(interaction)
        try:
            cfg, schedule = await load_schedule(self.guild_id, refresh=True)
            _, builder = await load_action_builder(self.guild_id, refresh=True)
            snapshot = await load_member_actions(interaction.user)
            cases = preview_rules(builder, snapshot)
            published_at = None
            # The canonical Member Setup owner records revision publication.
            # Use it, not a member's age, as the reminder starting point.
            if any(item.condition == "member_setup_revision" and item.status == "pending"
                   and item.included for item in cases):
                from stoney_verify.member_setup_service import load_guild_setup_state, latest_revision
                setup_state = await load_guild_setup_state(self.guild_id, refresh=True)
                latest = latest_revision(setup_state)
                if latest is not None and any(
                    action.key == f"member_setup:revision:{latest['revision']}"
                    for action in snapshot.items
                ):
                    raw_date = latest.get("published_at")
                    if isinstance(raw_date, str):
                        try:
                            parsed = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
                            if parsed.tzinfo is not None:
                                published_at = parsed
                        except ValueError:
                            pass
        except Exception:
            return await _msg(interaction, "Authoritative conditions unavailable. No preview was generated.")
        embed = discord.Embed(
            title="🔍 Reminder Timing Dry Run",
            description=(
                "**Private preview for your own account only.** "
                "Assumes zero previous claims; the future ledger and delivery "
                "worker are NOT active. Any real send requires fresh owner checks."
            ), color=discord.Color.blurple(),
        )
        for item in cases:
            dec = preview_candidate(
                schedule, condition=item.condition, owner_status=item.status,
                now=datetime.now(timezone.utc),
                joined_at=interaction.user.joined_at,
                condition_started_at=published_at if item.condition == "member_setup_revision" else None,
                included=item.included,
            )
            timing = (
                f"\nEarliest: <t:{int(dec.next_at.timestamp())}:R>"
                if dec.next_at else ""
            )
            embed.add_field(
                name=f"{item.condition.replace('_', ' ').title()} • {dec.status}",
                value=f"{dec.reason}{timing}", inline=False,
            )
        embed.set_footer(text="Read only • No automatic reminders enabled")
        await _replace(interaction, embed=embed, view=SchedulePreviewView(self.user_id, self.guild_id))

    @discord.ui.button(label="Check Delivery Readiness", emoji="🛡️", style=discord.ButtonStyle.secondary)
    async def inspect_readiness(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Inspect only the staff member's own status and real claim history."""
        if not isinstance(interaction.user, discord.Member):
            return await _msg(interaction, "Open your own private readiness check in this server.")
        await _defer(interaction)
        try:
            from stoney_verify.member_setup_service import load_guild_setup_state
            from stoney_verify.action_delivery_readiness import inspect_member_setup_readiness
            guild_state = await load_guild_setup_state(self.guild_id, refresh=True)
            revision = int(guild_state.get("current_revision") or 0)
            result = await inspect_member_setup_readiness(
                interaction.user, expected_revision=revision,
            )
        except Exception:
            return await _msg(interaction, "Couldn't verify delivery readiness. No reminder is authorized.")
        embed = discord.Embed(
            title="🛡️ Action Delivery Readiness • Private Check",
            description=(
                "**Your own Member Setup status only.** This uses current consent, "
                "canonical completion, and (where applicable) actual private "
                "PostgreSQL attempt history. Staff exemptions may apply.\\n\\n"
                f"**Status:** `{result.status}`\\n"
                f"**Reason:** {result.reason}\\n\\n"
                "🔒 **Automatic Action Center DMs remain disabled.**"
            ),
            color=discord.Color.blurple(),
        )
        if result.ledger_checked:
            embed.add_field(
                name="Observed counters",
                value=f"Member attempts for this revision: **{result.attempts}**\\n"
                      f"Guild claims today: **{result.daily_count}**",
                inline=False,
            )
        if result.next_at is not None:
            embed.add_field(
                name="Preview timing",
                value=f"<t:{int(result.next_at.timestamp())}:F>",
                inline=False,
            )
        embed.set_footer(text="No delivery • No claim reserved • No other members inspected")
        await _replace(
            interaction, embed=embed,
            view=SchedulePreviewView(self.user_id, self.guild_id),
        )

    @discord.ui.button(label="Preview Opted-in Members", emoji="👥", style=discord.ButtonStyle.secondary)
    async def opted_in_preview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Bounded five-profile dry run using existing action readiness checks."""
        if (
            interaction.guild_id != self.guild_id
            or not scoped_interaction_is_staff(interaction)
        ):
            return await _msg(interaction, "Server staff required to review reminder candidates.")
        from .public_action_member_setup_preview import open_member_setup_candidate_preview
        await open_member_setup_candidate_preview(interaction)

    @discord.ui.button(label="Owner Canary Console", emoji="🔐", style=discord.ButtonStyle.secondary)
    async def canary_console(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        # Not normal staff/administrator authority: actual application owner
        # is independently verified by the destination console at click-time.
        from .public_action_canary import open_app_owner_canary_console
        await open_app_owner_canary_console(interaction)

    @discord.ui.button(label="Back to Action Builder", emoji="↩️", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from .public_action_builder import open_action_builder
        await open_action_builder(interaction)


class SchedulePreviewView(_OwnedStaffView):
    @discord.ui.button(label="Back to Schedule", emoji="↩️", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await open_schedule_panel(interaction)


async def open_schedule_panel(interaction: discord.Interaction) -> None:
    if interaction.guild is None or not scoped_interaction_is_staff(interaction):
        return await _msg(interaction, "Action Schedule is for authorized server staff.")
    await _defer(interaction)
    try:
        _, schedule = await load_schedule(interaction.guild.id, refresh=True)
    except Exception:
        return await _msg(interaction, "Schedule configuration unavailable. No changes were made.")
    await _replace(
        interaction, embed=_embed(schedule),
        view=ScheduleView(interaction.user.id, interaction.guild.id, schedule),
    )
