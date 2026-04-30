from __future__ import annotations

"""
Truthful /ticket close + /ticket reopen lifecycle commands.

Problem this fixes:
The grouped public commands could say "✅ Closed" / "✅ Reopened" even when the
real Discord lifecycle work did not finish:
- channel did not move to the configured category
- channel did not rename to closed-XXXX / ticket-XXXX

Also fixes lifecycle drift:
If a ticket is already marked closed/open in the database but the Discord channel
is still in the wrong category/name, running /ticket close or /ticket reopen now
repairs the Discord lifecycle instead of stopping early with a misleading
"already closed/open" message.
"""

import builtins
import sys
from typing import Any, Optional

import discord

_ORIGINAL_IMPORT = builtins.__import__
_PATCHED = False
_PATCHING = False


def _log(message: str) -> None:
    try:
        print(f"🧾 runtime_ticket_lifecycle_command_truth {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ runtime_ticket_lifecycle_command_truth {message}")
    except Exception:
        pass


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
        from stoney_verify.tickets_new.lifecycle_categories import move_ticket_to_archive_category

        result = await move_ticket_to_archive_category(channel)
        if result.already_correct:
            return True, f"Already archived/named correctly in **{result.target_category_name}**."
        return True, f"Moved/renamed into archive category **{result.target_category_name}**."
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


async def _move_active_strict(channel: discord.TextChannel) -> tuple[bool, str]:
    try:
        from stoney_verify.tickets_new.lifecycle_categories import move_ticket_to_active_category

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
    from stoney_verify.commands_ext import public_ticket_group as group

    legacy = group.legacy

    if not await group._staff_only(interaction):
        return

    await group.safe_defer(interaction, ephemeral=True)

    ch, row = await legacy._ensure_ticket_context(interaction, channel)
    if ch is None:
        return

    if legacy.service_mark_ticket_closed is None:
        return await interaction.followup.send("❌ Ticket close service is unavailable.", ephemeral=True)

    status = legacy._ticket_status(row)
    if status == "deleted":
        return await interaction.followup.send("❌ This ticket is already marked deleted and cannot be closed.", ephemeral=True)

    # Critical drift repair: DB says closed, but Discord channel may still be in
    # ACTIVE TICKETS and named ticket-XXXX. Do not stop early; repair lifecycle.
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
        group.mark_ticket_activity(ch.id)
        group.RUNTIME_STATS["tickets_closed"] = int(group.RUNTIME_STATS.get("tickets_closed", 0) or 0) + 1
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
    from stoney_verify.commands_ext import public_ticket_group as group

    legacy = group.legacy

    if not await group._staff_only(interaction):
        return

    await group.safe_defer(interaction, ephemeral=True)

    ch, row = await legacy._ensure_ticket_context(interaction, channel)
    if ch is None:
        return

    if legacy.service_reopen_ticket_channel is None:
        return await interaction.followup.send("❌ Ticket reopen service is unavailable.", ephemeral=True)

    status = legacy._ticket_status(row)
    if status == "deleted":
        return await interaction.followup.send("❌ This ticket is deleted and cannot be reopened.", ephemeral=True)

    # Critical drift repair: DB says open, but Discord channel may still be in
    # archive and/or named closed-XXXX. Do not stop early; repair lifecycle.
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


def _patch_public_ticket_group(module: Any) -> None:
    global _PATCHED
    if _PATCHED:
        return

    try:
        ticket_group = getattr(module, "ticket_group", None)
        commands = list(getattr(ticket_group, "commands", []) or [])
    except Exception:
        commands = []

    patched: list[str] = []
    for command in commands:
        name = str(getattr(command, "name", "") or "")
        if name == "close":
            if _set_command_callback(command, _strict_ticket_close):
                patched.append("close")
        elif name == "reopen":
            if _set_command_callback(command, _strict_ticket_reopen):
                patched.append("reopen")

    if "close" in patched and "reopen" in patched:
        _PATCHED = True
        _log("patched /ticket close and /ticket reopen callbacks with truthful lifecycle checks + drift repair")
    elif patched:
        _PATCHED = True
        _warn(f"partially patched public ticket lifecycle callbacks: {patched}")


def _patch_loaded() -> None:
    try:
        module = sys.modules.get("stoney_verify.commands_ext.public_ticket_group")
        if module is not None:
            _patch_public_ticket_group(module)
    except Exception as e:
        _warn(f"loaded patch failed: {e!r}")


def _safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)
    try:
        if name == "stoney_verify.commands_ext.public_ticket_group" or name.endswith("commands_ext.public_ticket_group"):
            target = sys.modules.get("stoney_verify.commands_ext.public_ticket_group") or sys.modules.get(name)
            if target is not None:
                _patch_public_ticket_group(target)
        elif name == "stoney_verify.commands_ext" or name.endswith("commands_ext"):
            names = {str(x) for x in (fromlist or [])}
            if "public_ticket_group" in names:
                _patch_loaded()
    except Exception as e:
        _warn(f"post-import patch failed for {name}: {e!r}")
    return module


builtins.__import__ = _safe_import
_patch_loaded()
_log("loaded; truthful ticket lifecycle command responses + drift repair active")
