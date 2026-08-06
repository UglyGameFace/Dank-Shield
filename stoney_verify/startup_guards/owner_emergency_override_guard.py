from __future__ import annotations

"""Install the real-guild-owner Emergency Override controls on ticket panels."""

import asyncio
from typing import Any, Optional

import discord

from ..tickets_new.owner_emergency_override import (
    available_owner_emergency_actions,
    execute_owner_emergency_override,
    is_actual_guild_owner,
)


_PATCHED = False
_READY_LISTENER_ATTACHED = False
_REFRESH_RAN = False

_ACTION_LABELS = {
    "transfer": "Force Transfer",
    "unclaim": "Force Unclaim",
    "close": "Emergency Close",
    "delete": "Safe Emergency Delete",
}

_ACTION_DESCRIPTIONS = {
    "transfer": "Assign this open ticket to another configured staff member.",
    "unclaim": "Remove the current claimant and return the ticket to the queue.",
    "close": "Close the ticket without stealing its existing claim history.",
    "delete": "Delete a closed ticket only after a transcript is verified.",
}

_ACTION_EMOJIS = {
    "transfer": "🔁",
    "unclaim": "↩️",
    "close": "🔒",
    "delete": "🗑️",
}


def _log(message: str) -> None:
    try:
        print(f"✅ owner_emergency_override_guard: {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ owner_emergency_override_guard: {message}")
    except Exception:
        pass


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return default
        return int(str(value).strip())
    except Exception:
        return default


def _resolve_member(interaction: discord.Interaction) -> Optional[discord.Member]:
    guild = interaction.guild
    user = interaction.user
    if guild is None:
        return None
    if isinstance(user, discord.Member):
        return user
    try:
        return guild.get_member(int(user.id))
    except Exception:
        return None


async def _reply(interaction: discord.Interaction, content: str) -> None:
    try:
        if interaction.response.is_done():
            await interaction.followup.send(
                content,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        else:
            await interaction.response.send_message(
                content,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
    except Exception:
        pass


async def _ticket_row(channel_id: int) -> Optional[dict[str, Any]]:
    try:
        from ..tickets_new.repository import get_ticket_by_any_channel_id

        row = await get_ticket_by_any_channel_id(channel_id)
        return dict(row) if isinstance(row, dict) else None
    except Exception:
        return None


def _extract_user_id(value: str) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    digits = "".join(ch for ch in text if ch.isdigit())
    return _safe_int(digits, 0)


async def _resolve_target_member(
    guild: discord.Guild,
    raw_value: str,
) -> Optional[discord.Member]:
    target_id = _extract_user_id(raw_value)
    if target_id <= 0:
        return None

    member = guild.get_member(target_id)
    if member is not None:
        return member

    try:
        return await guild.fetch_member(target_id)
    except Exception:
        return None


class OwnerEmergencyOverrideModal(discord.ui.Modal):
    def __init__(self, *, action: str, channel_id: int, owner_id: int):
        label = _ACTION_LABELS.get(action, "Emergency Override")
        super().__init__(title=f"Owner {label}"[:45], timeout=300)
        self.action = str(action)
        self.channel_id = int(channel_id)
        self.owner_id = int(owner_id)

        self.target_user: Optional[discord.ui.TextInput] = None
        if self.action == "transfer":
            self.target_user = discord.ui.TextInput(
                label="New staff claimant",
                placeholder="Paste @mention or Discord user ID",
                style=discord.TextStyle.short,
                required=True,
                max_length=64,
            )
            self.add_item(self.target_user)

        self.reason = discord.ui.TextInput(
            label="Emergency reason",
            placeholder="Explain why the normal claimant workflow must be overridden.",
            style=discord.TextStyle.paragraph,
            required=True,
            min_length=8,
            max_length=500,
        )
        self.add_item(self.reason)

        self.confirmation = discord.ui.TextInput(
            label="Type OVERRIDE to confirm",
            placeholder="OVERRIDE",
            style=discord.TextStyle.short,
            required=True,
            min_length=8,
            max_length=8,
        )
        self.add_item(self.confirmation)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        channel = interaction.channel
        guild = interaction.guild
        actor = _resolve_member(interaction)

        if (
            guild is None
            or actor is None
            or not isinstance(channel, discord.TextChannel)
            or int(channel.id) != self.channel_id
        ):
            return await _reply(interaction, "❌ This override is no longer attached to the original ticket.")

        if int(actor.id) != self.owner_id or not is_actual_guild_owner(guild, actor):
            return await _reply(
                interaction,
                "❌ Only the actual Discord server owner can confirm Emergency Override.",
            )

        if str(self.confirmation.value or "").strip().upper() != "OVERRIDE":
            return await _reply(interaction, "❌ Confirmation did not match `OVERRIDE`. Nothing was changed.")

        target_member: Optional[discord.Member] = None
        if self.action == "transfer":
            target_member = await _resolve_target_member(
                guild,
                str(self.target_user.value if self.target_user is not None else ""),
            )
            if target_member is None:
                return await _reply(interaction, "❌ That transfer target was not found in this server.")

        try:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True)
        except Exception:
            pass

        result = await execute_owner_emergency_override(
            channel=channel,
            actor=actor,
            action=self.action,
            reason=str(self.reason.value or ""),
            target_member=target_member,
        )

        prefix = "✅" if result.ok else "❌"
        await _reply(interaction, f"{prefix} {result.message}\n`{result.code}`")


class OwnerEmergencyActionSelect(discord.ui.Select):
    def __init__(
        self,
        *,
        channel_id: int,
        owner_id: int,
        actions: tuple[str, ...],
    ):
        self.channel_id = int(channel_id)
        self.owner_id = int(owner_id)
        options = [
            discord.SelectOption(
                label=_ACTION_LABELS[action],
                value=action,
                description=_ACTION_DESCRIPTIONS[action][:100],
                emoji=_ACTION_EMOJIS[action],
            )
            for action in actions
        ]
        super().__init__(
            placeholder="Choose an emergency action…",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"ticket_owner_emergency_action:{self.channel_id}",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        channel = interaction.channel
        guild = interaction.guild
        actor = _resolve_member(interaction)

        if (
            guild is None
            or actor is None
            or not isinstance(channel, discord.TextChannel)
            or int(channel.id) != self.channel_id
        ):
            return await _reply(interaction, "❌ This Emergency Override menu is no longer valid.")

        if int(actor.id) != self.owner_id or not is_actual_guild_owner(guild, actor):
            return await _reply(interaction, "❌ Only the actual Discord server owner can use this menu.")

        action = str(self.values[0] if self.values else "").strip().lower()
        row = await _ticket_row(channel.id)
        if action not in available_owner_emergency_actions(row):
            return await _reply(
                interaction,
                "❌ The ticket state changed. Reopen Emergency Override and choose an available action.",
            )

        try:
            await interaction.response.send_modal(
                OwnerEmergencyOverrideModal(
                    action=action,
                    channel_id=channel.id,
                    owner_id=actor.id,
                )
            )
        except Exception as exc:
            _warn(f"could not open owner override modal channel={channel.id}: {exc!r}")
            await _reply(interaction, "❌ Failed to open the Emergency Override confirmation form.")


class OwnerEmergencyActionView(discord.ui.View):
    def __init__(
        self,
        *,
        channel_id: int,
        owner_id: int,
        actions: tuple[str, ...],
    ):
        super().__init__(timeout=180)
        self.add_item(
            OwnerEmergencyActionSelect(
                channel_id=channel_id,
                owner_id=owner_id,
                actions=actions,
            )
        )


async def _open_owner_emergency_menu(interaction: discord.Interaction) -> None:
    channel = interaction.channel
    guild = interaction.guild
    actor = _resolve_member(interaction)

    if guild is None or actor is None or not isinstance(channel, discord.TextChannel):
        return await _reply(interaction, "❌ This can only be used inside a ticket channel.")

    if not is_actual_guild_owner(guild, actor):
        return await _reply(
            interaction,
            "❌ Emergency Override is restricted to the actual Discord server owner. Administrator permission is not enough.",
        )

    row = await _ticket_row(channel.id)
    actions = available_owner_emergency_actions(row)
    if not actions:
        return await _reply(
            interaction,
            "❌ No emergency action is available for this ticket's current lifecycle state.",
        )

    claimed_by = 0
    try:
        from ..tickets_new.claim_policy import ticket_claimed_by_id

        claimed_by = ticket_claimed_by_id(row)
    except Exception:
        pass

    embed = discord.Embed(
        title="🚨 Server Owner Emergency Override",
        description=(
            "This bypass is for genuine emergencies only. Every confirmed action requires a written reason "
            "and is written to the ticket audit history. Ordinary administrators cannot use it."
        ),
        color=discord.Color.red(),
    )
    embed.add_field(
        name="Current claimant",
        value=f"<@{claimed_by}>" if claimed_by > 0 else "Nobody",
        inline=True,
    )
    embed.add_field(
        name="Available now",
        value="\n".join(
            f"{_ACTION_EMOJIS[action]} **{_ACTION_LABELS[action]}** — {_ACTION_DESCRIPTIONS[action]}"
            for action in actions
        ),
        inline=False,
    )
    embed.set_footer(text="Confirmation requires typing OVERRIDE exactly.")

    view = OwnerEmergencyActionView(
        channel_id=channel.id,
        owner_id=actor.id,
        actions=actions,
    )

    try:
        if interaction.response.is_done():
            await interaction.followup.send(
                embed=embed,
                view=view,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        else:
            await interaction.response.send_message(
                embed=embed,
                view=view,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
    except Exception as exc:
        _warn(f"could not open owner override menu channel={channel.id}: {exc!r}")


def _view_custom_ids(view: discord.ui.View) -> set[str]:
    out: set[str] = set()
    for child in list(getattr(view, "children", []) or []):
        custom_id = str(getattr(child, "custom_id", "") or "")
        if custom_id:
            out.add(custom_id)
    return out


def _patch_view_init(view_class: Any, *, custom_id: str, row: int) -> bool:
    original_init = getattr(view_class, "__init__", None)
    if not callable(original_init):
        return False
    if bool(getattr(original_init, "_owner_emergency_override_wrapped", False)):
        return True

    def wrapped_init(self: discord.ui.View, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        if custom_id in _view_custom_ids(self):
            return

        button = discord.ui.Button(
            label="Emergency Override",
            style=discord.ButtonStyle.danger,
            emoji="🚨",
            custom_id=custom_id,
            row=row,
        )

        async def callback(interaction: discord.Interaction) -> None:
            await _open_owner_emergency_menu(interaction)

        button.callback = callback
        self.add_item(button)

    setattr(wrapped_init, "_owner_emergency_override_wrapped", True)
    view_class.__init__ = wrapped_init
    return True


def _message_custom_ids(message: discord.Message) -> set[str]:
    out: set[str] = set()
    try:
        for action_row in getattr(message, "components", None) or []:
            for child in getattr(action_row, "children", None) or []:
                custom_id = str(getattr(child, "custom_id", "") or "")
                if custom_id:
                    out.add(custom_id)
    except Exception:
        pass
    return out


async def _refresh_existing_control_messages(bot: discord.Client) -> None:
    global _REFRESH_RAN
    if _REFRESH_RAN:
        return
    _REFRESH_RAN = True

    try:
        await asyncio.sleep(8)
    except Exception:
        pass

    try:
        from .. import transcripts
        from ..tickets_new import panel
    except Exception as exc:
        _warn(f"existing control refresh imports failed: {exc!r}")
        return

    open_panel_ids = {
        "ticket_claim_request",
        "ticket_unclaim_request",
        "ticket_actions_more_select",
        "ticket_transfer_request",
        "ticket_close_request",
        "ticket_owner_emergency_override",
    }
    open_transcript_ids = {
        "sv:ticket:close",
        "sv:ticket:delete_open",
        "sv:ticket:owner_emergency_override_open",
    }
    closed_ids = {
        "sv:ticket:reopen",
        "sv:ticket:transcript",
        "sv:ticket:delete",
        "sv:ticket:owner_emergency_override_closed",
    }

    scanned = 0
    updated = 0
    me_id = _safe_int(getattr(getattr(bot, "user", None), "id", 0), 0)

    for guild in list(getattr(bot, "guilds", []) or []):
        for channel in list(getattr(guild, "text_channels", []) or []):
            row = await _ticket_row(channel.id)
            status = str((row or {}).get("status") or "").strip().lower()
            if status in {"active", "reopened"}:
                status = "open"
            if status not in {"open", "claimed", "closed"}:
                continue

            try:
                async for message in channel.history(limit=80):
                    if me_id > 0 and _safe_int(getattr(getattr(message, "author", None), "id", 0), 0) != me_id:
                        continue
                    ids = _message_custom_ids(message)
                    if not ids:
                        continue
                    scanned += 1

                    replacement: Optional[discord.ui.View] = None
                    if status in {"open", "claimed"} and ids.intersection(open_panel_ids):
                        replacement = panel.TicketChannelActionsView()
                    elif status in {"open", "claimed"} and ids.intersection(open_transcript_ids):
                        replacement = transcripts.TicketOpenActionsView()
                    elif status == "closed" and ids.intersection(closed_ids):
                        replacement = transcripts.StaffClosedTicketView()

                    if replacement is None:
                        continue

                    try:
                        await message.edit(view=replacement)
                        updated += 1
                    except Exception as exc:
                        _warn(
                            f"could not refresh ticket controls guild={guild.id} "
                            f"channel={channel.id} message={message.id}: {exc!r}"
                        )
            except Exception:
                continue

    _log(f"existing ticket control refresh complete scanned={scanned} updated={updated}")


def _attach_ready_listener(bot: Any) -> None:
    global _READY_LISTENER_ATTACHED
    if _READY_LISTENER_ATTACHED or bot is None:
        return

    async def on_ready_owner_emergency_override() -> None:
        await _refresh_existing_control_messages(bot)

    try:
        bot.add_listener(on_ready_owner_emergency_override, "on_ready")
        _READY_LISTENER_ATTACHED = True
    except Exception as exc:
        _warn(f"could not attach on_ready control refresh: {exc!r}")


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True

    try:
        from .. import transcripts
        from ..tickets_new import panel
    except Exception as exc:
        _warn(f"could not import ticket control modules: {exc!r}")
        return False

    installed = {
        "ticket_channel_actions": _patch_view_init(
            panel.TicketChannelActionsView,
            custom_id="ticket_owner_emergency_override",
            row=2,
        ),
        "open_ticket_actions": _patch_view_init(
            transcripts.TicketOpenActionsView,
            custom_id="sv:ticket:owner_emergency_override_open",
            row=1,
        ),
        "closed_ticket_actions": _patch_view_init(
            transcripts.StaffClosedTicketView,
            custom_id="sv:ticket:owner_emergency_override_closed",
            row=1,
        ),
    }

    missing = sorted(name for name, ok in installed.items() if not ok)
    if missing:
        _warn("failed closed; missing view patches: " + ", ".join(missing))
        return False

    _attach_ready_listener(getattr(panel, "bot", None))
    _PATCHED = True
    _log("actual-guild-owner emergency override controls active")
    return True


apply()


__all__ = [
    "OwnerEmergencyActionView",
    "OwnerEmergencyOverrideModal",
    "apply",
]
