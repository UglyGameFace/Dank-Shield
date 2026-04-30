from __future__ import annotations

"""
Clean public /ticket command registration.

This folds the former root-level runtime_ticket_lifecycle_command_truth_patch.py
behavior into the normal commands_ext source tree.

It deliberately reuses public_ticket_group's existing command definitions while
replacing only the lifecycle callbacks that were unsafe:
- /ticket close
- /ticket reopen

Those callbacks now:
- rename ticket-XXXX <-> closed-XXXX through lifecycle_categories
- move Active Tickets <-> Ticket Archive through lifecycle_categories
- repair lifecycle drift when DB state and Discord channel state disagree
- refuse to show a clean green success if the Discord move/rename failed
"""

from typing import Any, Optional

import discord

from . import public_ticket_group as base


def _permission_snapshot(channel: discord.TextChannel) -> str:
    try:
        me = channel.guild.me
        if me is None:
            return "bot member unavailable"
        perms = channel.permissions_for(me)
        missing: list[str] = []
        for label, ok in (
            ("View Channel", getattr(perms, "view_channel", False)),
            ("Manage Channels", getattr(perms, "manage_channels", False)),
            ("Read Message History", getattr(perms, "read_message_history", False)),
            ("Send Messages", getattr(perms, "send_messages", False)),
            ("Embed Links", getattr(perms, "embed_links", False)),
        ):
            if not ok:
                missing.append(label)
        return ", ".join(missing) if missing else "none"
    except Exception as e:
        return f"snapshot failed: {type(e).__name__}"


def _category_label(channel: discord.TextChannel) -> str:
    try:
        if channel.category is not None:
            return f"**{channel.category.name}** (`{channel.category.id}`)"
    except Exception:
        pass
    return "No category"


def _channel_name_label(channel: discord.TextChannel) -> str:
    try:
        return f"`#{channel.name}`"
    except Exception:
        return "unknown"


async def _move_archive_strict(channel: discord.TextChannel) -> tuple[bool, str]:
    try:
        from ..tickets_new.lifecycle_categories import move_ticket_to_archive_category

        result = await move_ticket_to_archive_category(channel)
        if result.already_correct:
            return True, f"Already archived/named correctly in **{result.target_category_name}**."
        return True, f"Moved/renamed into archive category **{result.target_category_name}**."
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


async def _move_active_strict(channel: discord.TextChannel) -> tuple[bool, str]:
    try:
        from ..tickets_new.lifecycle_categories import move_ticket_to_active_category

        result = await move_ticket_to_active_category(channel)
        if result.already_correct:
            return True, f"Already active/named correctly in **{result.target_category_name}**."
        return True, f"Moved/renamed into active category **{result.target_category_name}**."
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


async def _strict_ticket_close(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
    reason: Optional[str] = None,
) -> None:
    legacy = base.legacy

    if not await base._staff_only(interaction):
        return

    await base.safe_defer(interaction, ephemeral=True)

    ch, row = await legacy._ensure_ticket_context(interaction, channel)
    if ch is None:
        return

    if legacy.service_mark_ticket_closed is None:
        return await interaction.followup.send("❌ Ticket close service is unavailable.", ephemeral=True)

    status = legacy._ticket_status(row)
    if status == "deleted":
        return await interaction.followup.send("❌ This ticket is already marked deleted and cannot be closed.", ephemeral=True)

    # DB already says closed, but Discord may still be wrong. Repair lifecycle.
    if legacy._ticket_effectively_closed(channel=ch, row=row) and status == "closed":
        lifecycle_ok, lifecycle_detail = await _move_archive_strict(ch)
        if lifecycle_ok:
            try:
                await ch.send(f"🧰 Closed ticket lifecycle repaired by {interaction.user.mention}.\n📦 {lifecycle_detail}")
            except Exception:
                pass
            return await interaction.followup.send(
                "✅ This ticket was already marked closed, so I repaired/verified its Discord lifecycle.\n"
                f"📦 {lifecycle_detail}\n"
                f"Current channel: {_channel_name_label(ch)}\n"
                f"Current location: {_category_label(ch)}",
                ephemeral=True,
            )

        return await interaction.followup.send(
            "⚠️ This ticket is marked closed in the database, but I could not repair its Discord lifecycle.\n"
            "It is still not fully archived/renamed.\n"
            f"Current channel: {_channel_name_label(ch)}\n"
            f"Current location: {_category_label(ch)}\n"
            f"Missing channel permissions from bot view: `{_permission_snapshot(ch)}`\n"
            f"Error: `{lifecycle_detail[:900]}`",
            ephemeral=True,
        )

    owner = await legacy._owner_for_ticket(ch, row)
    actor_member = legacy._actor_member(interaction.guild, interaction.user) or interaction.user
    decision = reason.strip() if reason and reason.strip() else "STAFF CLOSED"

    await legacy._cleanup_ticket_timer_state(ch.id)

    try:
        state_ok = await legacy.service_mark_ticket_closed(
            channel=ch,
            closed_by=actor_member,
            reason=decision,
        )
    except Exception as e:
        return await interaction.followup.send(f"❌ Failed closing ticket state: `{e}`", ephemeral=True)

    if not state_ok:
        return await interaction.followup.send("❌ Failed to close this ticket state.", ephemeral=True)

    lifecycle_ok, lifecycle_detail = await _move_archive_strict(ch)

    transcript_ok, transcript_url = await legacy._post_ticket_transcript(
        channel=ch,
        owner=owner,
        actor=actor_member,
        reason=decision,
    )

    try:
        if lifecycle_ok:
            await ch.send(
                f"🔒 Ticket closed by {interaction.user.mention}.\n"
                f"**Reason:** {decision}\n"
                f"📦 {lifecycle_detail}"
            )
        else:
            await ch.send(
                f"⚠️ Ticket state was closed by {interaction.user.mention}, but I could not move/rename this channel.\n"
                f"**Reason:** {decision}\n"
                f"**Lifecycle error:** `{lifecycle_detail[:900]}`"
            )
    except Exception:
        pass

    try:
        base.mark_ticket_activity(ch.id)
        base.RUNTIME_STATS["tickets_closed"] = int(base.RUNTIME_STATS.get("tickets_closed", 0) or 0) + 1
    except Exception:
        pass

    if lifecycle_ok:
        lines = [
            f"✅ Closed {ch.mention}.",
            f"📦 {lifecycle_detail}",
            f"Current channel: {_channel_name_label(ch)}",
            f"Current location: {_category_label(ch)}",
        ]
    else:
        lines = [
            f"⚠️ Ticket state was closed for {ch.mention}, but lifecycle failed.",
            "The channel was **not fully archived/renamed**.",
            f"Current channel: {_channel_name_label(ch)}",
            f"Current location: {_category_label(ch)}",
            f"Missing channel permissions from bot view: `{_permission_snapshot(ch)}`",
            f"Error: `{lifecycle_detail[:900]}`",
        ]

    if transcript_ok and transcript_url:
        lines.append(f"🧾 Transcript: {transcript_url}")
    elif not transcript_ok:
        lines.append("⚠️ Transcript generation failed.")

    await interaction.followup.send("\n".join(lines), ephemeral=True)


async def _strict_ticket_reopen(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
    reason: Optional[str] = None,
) -> None:
    legacy = base.legacy

    if not await base._staff_only(interaction):
        return

    await base.safe_defer(interaction, ephemeral=True)

    ch, row = await legacy._ensure_ticket_context(interaction, channel)
    if ch is None:
        return

    if legacy.service_reopen_ticket_channel is None:
        return await interaction.followup.send("❌ Ticket reopen service is unavailable.", ephemeral=True)

    status = legacy._ticket_status(row)
    if status == "deleted":
        return await interaction.followup.send("❌ This ticket is deleted and cannot be reopened.", ephemeral=True)

    # DB already says open, but Discord may still be wrong. Repair lifecycle.
    if legacy._ticket_effectively_open(channel=ch, row=row) and status in {"open", "claimed"}:
        lifecycle_ok, lifecycle_detail = await _move_active_strict(ch)
        if lifecycle_ok:
            try:
                await ch.send(f"🧰 Open ticket lifecycle repaired by {interaction.user.mention}.\n📂 {lifecycle_detail}")
            except Exception:
                pass
            return await interaction.followup.send(
                "✅ This ticket was already marked open, so I repaired/verified its Discord lifecycle.\n"
                f"📂 {lifecycle_detail}\n"
                f"Current channel: {_channel_name_label(ch)}\n"
                f"Current location: {_category_label(ch)}",
                ephemeral=True,
            )

        return await interaction.followup.send(
            "⚠️ This ticket is marked open in the database, but I could not repair its Discord lifecycle.\n"
            "It is still not fully moved/renamed back to active.\n"
            f"Current channel: {_channel_name_label(ch)}\n"
            f"Current location: {_category_label(ch)}\n"
            f"Missing channel permissions from bot view: `{_permission_snapshot(ch)}`\n"
            f"Error: `{lifecycle_detail[:900]}`",
            ephemeral=True,
        )

    owner = await legacy._owner_for_ticket(ch, row)
    owner_member = owner if isinstance(owner, discord.Member) else None
    actor_member = legacy._actor_member(interaction.guild, interaction.user) or interaction.user
    reopen_reason = reason.strip() if reason and reason.strip() else "Reopened from /ticket reopen"

    try:
        state_ok = await legacy.service_reopen_ticket_channel(
            channel=ch,
            owner=owner_member,
            actor=actor_member,
            reason=reopen_reason,
        )
    except Exception as e:
        return await interaction.followup.send(f"❌ Failed reopening ticket state: `{e}`", ephemeral=True)

    if not state_ok:
        return await interaction.followup.send("❌ Failed to reopen this ticket state.", ephemeral=True)

    lifecycle_ok, lifecycle_detail = await _move_active_strict(ch)

    try:
        if lifecycle_ok:
            await ch.send(
                f"🔓 Ticket reopened by {interaction.user.mention}.\n"
                f"**Reason:** {reopen_reason}\n"
                f"📂 {lifecycle_detail}"
            )
        else:
            await ch.send(
                f"⚠️ Ticket state was reopened by {interaction.user.mention}, but I could not move/rename this channel.\n"
                f"**Reason:** {reopen_reason}\n"
                f"**Lifecycle error:** `{lifecycle_detail[:900]}`"
            )
    except Exception:
        pass

    if lifecycle_ok:
        lines = [
            f"✅ Reopened {ch.mention}.",
            f"📂 {lifecycle_detail}",
            f"Current channel: {_channel_name_label(ch)}",
            f"Current location: {_category_label(ch)}",
        ]
    else:
        lines = [
            f"⚠️ Ticket state was reopened for {ch.mention}, but lifecycle failed.",
            "The channel was **not fully moved/renamed back to active**.",
            f"Current channel: {_channel_name_label(ch)}",
            f"Current location: {_category_label(ch)}",
            f"Missing channel permissions from bot view: `{_permission_snapshot(ch)}`",
            f"Error: `{lifecycle_detail[:900]}`",
        ]

    await interaction.followup.send("\n".join(lines), ephemeral=True)


def _set_command_callback(command: Any, callback: Any) -> bool:
    changed = False
    for attr in ("callback", "_callback"):
        try:
            setattr(command, attr, callback)
            changed = True
        except Exception:
            pass
    return changed


def _patch_lifecycle_callbacks() -> None:
    patched: list[str] = []
    for command in list(getattr(base.ticket_group, "commands", []) or []):
        name = str(getattr(command, "name", "") or "")
        if name == "close" and _set_command_callback(command, _strict_ticket_close):
            patched.append("close")
        elif name == "reopen" and _set_command_callback(command, _strict_ticket_reopen):
            patched.append("reopen")

    if "close" in patched and "reopen" in patched:
        print("✅ public_ticket_group_clean: /ticket close/reopen use native truthful lifecycle handling")
    else:
        print(f"⚠️ public_ticket_group_clean: lifecycle callback patch incomplete patched={patched}")


def register_public_ticket_group_clean_commands(bot: Any, tree: Any) -> None:
    _patch_lifecycle_callbacks()
    base.register_public_ticket_group_commands(bot, tree)


__all__ = ["register_public_ticket_group_clean_commands"]
