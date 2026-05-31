from __future__ import annotations

"""Add TicketTool-style ticket access management.

Staff can add or remove a member/role from the current ticket through the existing
"More ticket actions" menu. This avoids adding another slash command while still
covering the production feature server staff expect from a serious ticket bot.
"""

import re
from typing import Any, Optional, Union

import discord

_TARGET_RE = re.compile(r"(?:<@!?|<@&)?(\d{15,25})>?")


def _log(message: str) -> None:
    try:
        print(f"✅ ticket_access_management_guard: {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ ticket_access_management_guard: {message}")
    except Exception:
        pass


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _extract_target_id(raw: Any) -> int:
    text = _safe_str(raw)
    match = _TARGET_RE.search(text)
    return _safe_int(match.group(1), 0) if match else 0


def _target_kind_hint(raw: Any) -> str:
    text = _safe_str(raw)
    if "<@&" in text:
        return "role"
    if "<@" in text:
        return "member"
    return "unknown"


async def _resolve_access_target(
    guild: discord.Guild,
    raw: Any,
) -> Optional[Union[discord.Member, discord.Role]]:
    target_id = _extract_target_id(raw)
    if target_id <= 0:
        return None

    kind = _target_kind_hint(raw)
    if kind != "member":
        role = guild.get_role(target_id)
        if isinstance(role, discord.Role):
            return role

    member = guild.get_member(target_id)
    if isinstance(member, discord.Member):
        return member

    try:
        fetched = await guild.fetch_member(target_id)
        if isinstance(fetched, discord.Member):
            return fetched
    except Exception:
        pass

    if kind != "role":
        role = guild.get_role(target_id)
        if isinstance(role, discord.Role):
            return role

    return None


def _target_label(target: Union[discord.Member, discord.Role]) -> str:
    try:
        return target.mention
    except Exception:
        return str(target)


def _target_id(target: Union[discord.Member, discord.Role]) -> int:
    return _safe_int(getattr(target, "id", 0), 0)


def _target_type(target: Union[discord.Member, discord.Role]) -> str:
    return "role" if isinstance(target, discord.Role) else "member"


def _owner_id_from_row_or_topic(panel_mod: Any, channel: discord.TextChannel, row: Optional[dict[str, Any]]) -> int:
    for key in ("user_id", "owner_id", "requester_id"):
        value = _safe_int((row or {}).get(key), 0)
        if value > 0:
            return value
    try:
        topic = str(channel.topic or "")
        match = re.search(r"(?:^|;)owner_id=(\d+)(?:;|$)", topic)
        if match:
            return _safe_int(match.group(1), 0)
    except Exception:
        pass
    return 0


async def _send_ephemeral(panel_mod: Any, interaction: discord.Interaction, content: str) -> None:
    try:
        await panel_mod._safe_followup(interaction, content)
    except Exception:
        try:
            if interaction.response.is_done():
                await interaction.followup.send(content, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
            else:
                await interaction.response.send_message(content, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            pass


async def _defer(panel_mod: Any, interaction: discord.Interaction) -> None:
    try:
        await panel_mod._safe_defer(interaction)
    except Exception:
        try:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True, thinking=True)
        except Exception:
            pass


async def _channel_notice(channel: discord.TextChannel, content: str) -> None:
    try:
        await channel.send(content, allowed_mentions=discord.AllowedMentions(users=True, roles=True, everyone=False))
    except Exception:
        pass


class TicketAccessModal(discord.ui.Modal):
    def __init__(self, panel_mod: Any, *, mode: str, channel_id: int):
        self.panel_mod = panel_mod
        self.mode = "remove" if str(mode).lower() == "remove" else "add"
        self.channel_id = int(channel_id)
        title = "Add Ticket Access" if self.mode == "add" else "Remove Ticket Access"
        super().__init__(title=title, timeout=300)

        self.target = discord.ui.TextInput(
            label="Member or role ID / mention",
            placeholder="Example: @member, @role, or 123456789012345678",
            required=True,
            max_length=120,
        )
        self.reason = discord.ui.TextInput(
            label="Reason",
            placeholder="Optional note for staff records",
            required=False,
            max_length=300,
            style=discord.TextStyle.paragraph,
        )
        self.add_item(self.target)
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        panel_mod = self.panel_mod
        channel = interaction.channel
        actor = None
        try:
            actor = panel_mod._resolve_member(interaction)
        except Exception:
            actor = interaction.user if isinstance(interaction.user, discord.Member) else None

        if not isinstance(channel, discord.TextChannel) or int(channel.id) != int(self.channel_id):
            return await _send_ephemeral(panel_mod, interaction, "This access form belongs to a different ticket channel.")
        if actor is None or not isinstance(actor, discord.Member):
            return await _send_ephemeral(panel_mod, interaction, "This can only be used inside the server.")
        try:
            if not panel_mod._is_staff_member(actor):
                return await _send_ephemeral(panel_mod, interaction, "Only staff can manage ticket access.")
        except Exception:
            return await _send_ephemeral(panel_mod, interaction, "Only staff can manage ticket access.")

        await _defer(panel_mod, interaction)

        try:
            row = await panel_mod._ticket_row_for_channel(channel)
        except Exception:
            row = None

        try:
            if panel_mod._ticket_is_deleted(row):
                return await _send_ephemeral(panel_mod, interaction, "❌ This ticket is deleted.")
        except Exception:
            pass

        try:
            if not panel_mod._ticket_is_open_like(channel, row):
                return await _send_ephemeral(panel_mod, interaction, panel_mod._open_panel_state_error(channel, row))
        except Exception:
            pass

        target = await _resolve_access_target(channel.guild, str(self.target.value or ""))
        if target is None:
            return await _send_ephemeral(panel_mod, interaction, "I could not find that member or role in this server. Use a mention or raw ID.")

        if isinstance(target, discord.Member) and getattr(target, "bot", False):
            return await _send_ephemeral(panel_mod, interaction, "Bots cannot be added as ticket participants.")

        owner_id = _owner_id_from_row_or_topic(panel_mod, channel, row)
        if self.mode == "remove" and isinstance(target, discord.Member) and owner_id > 0 and int(target.id) == int(owner_id):
            return await _send_ephemeral(panel_mod, interaction, "The ticket owner cannot be removed from their own open ticket. Close the ticket instead.")

        target_text = _target_label(target)
        reason = _safe_str(self.reason.value, "No reason provided")
        audit_reason = f"Ticket access {self.mode} by {actor} ({actor.id}): {reason}"[:512]

        try:
            if self.mode == "add":
                overwrite = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    attach_files=True,
                    embed_links=True,
                    read_message_history=True,
                )
                await channel.set_permissions(target, overwrite=overwrite, reason=audit_reason)
                await _channel_notice(
                    channel,
                    f"➕ Ticket access added for {target_text} by {actor.mention}."
                )
                return await _send_ephemeral(panel_mod, interaction, f"Added {target_text} to this ticket.")

            await channel.set_permissions(target, overwrite=None, reason=audit_reason)
            await _channel_notice(
                channel,
                f"➖ Ticket access removed for {target_text} by {actor.mention}."
            )
            return await _send_ephemeral(panel_mod, interaction, f"Removed direct ticket access for {target_text}.")
        except discord.Forbidden:
            return await _send_ephemeral(panel_mod, interaction, "I do not have permission to manage that ticket permission overwrite.")
        except Exception as e:
            _warn(f"ticket access {self.mode} failed channel={channel.id}: {type(e).__name__}: {e}")
            return await _send_ephemeral(panel_mod, interaction, f"Could not update ticket access: `{type(e).__name__}`")


async def _action_manage_access(panel_mod: Any, interaction: discord.Interaction, mode: str) -> None:
    channel = interaction.channel
    actor = None
    try:
        actor = panel_mod._resolve_member(interaction)
    except Exception:
        actor = interaction.user if isinstance(interaction.user, discord.Member) else None

    if not isinstance(channel, discord.TextChannel):
        return await _send_ephemeral(panel_mod, interaction, "Invalid ticket channel.")
    if actor is None or not isinstance(actor, discord.Member):
        return await _send_ephemeral(panel_mod, interaction, "This can only be used inside the server.")
    try:
        if not panel_mod._is_staff_member(actor):
            return await _send_ephemeral(panel_mod, interaction, "Only staff can manage ticket access.")
    except Exception:
        return await _send_ephemeral(panel_mod, interaction, "Only staff can manage ticket access.")

    try:
        row = await panel_mod._ticket_row_for_channel(channel)
        if not panel_mod._ticket_is_open_like(channel, row):
            return await _send_ephemeral(panel_mod, interaction, panel_mod._open_panel_state_error(channel, row))
    except Exception:
        pass

    try:
        await interaction.response.send_modal(TicketAccessModal(panel_mod, mode=mode, channel_id=channel.id))
    except Exception as e:
        _warn(f"ticket access modal open failed channel={channel.id}: {type(e).__name__}: {e}")
        await _send_ephemeral(panel_mod, interaction, "Failed to open ticket access form.")


def _patch_action_select(panel_mod: Any) -> bool:
    select_cls = getattr(panel_mod, "TicketActionSelect", None)
    if not isinstance(select_cls, type):
        return False
    if getattr(select_cls, "_ACCESS_MANAGEMENT_PATCHED", False):
        return True

    original_init = select_cls.__init__
    original_callback = select_cls.callback

    def patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        try:
            values = {str(getattr(opt, "value", "")) for opt in list(getattr(self, "options", []) or [])}
            if "add_access" not in values:
                self.options.append(
                    discord.SelectOption(
                        label="Add Member / Role",
                        value="add_access",
                        description="Give someone access to this ticket",
                        emoji="➕",
                    )
                )
            if "remove_access" not in values:
                self.options.append(
                    discord.SelectOption(
                        label="Remove Member / Role",
                        value="remove_access",
                        description="Remove a direct ticket permission override",
                        emoji="➖",
                    )
                )
        except Exception as e:
            _warn(f"failed adding access options: {e!r}")

    async def patched_callback(self: Any, interaction: discord.Interaction) -> None:
        action = str(self.values[0] if getattr(self, "values", None) else "").strip()
        if action == "add_access":
            return await _action_manage_access(panel_mod, interaction, "add")
        if action == "remove_access":
            return await _action_manage_access(panel_mod, interaction, "remove")
        return await original_callback(self, interaction)

    select_cls.__init__ = patched_init
    select_cls.callback = patched_callback
    setattr(select_cls, "_ACCESS_MANAGEMENT_PATCHED", True)
    return True


def apply() -> bool:
    try:
        from ..tickets_new import panel as panel_mod
    except Exception as e:
        _warn(f"could not import tickets_new.panel: {e!r}")
        return False

    try:
        ok = _patch_action_select(panel_mod)
        if ok:
            _log("added access management actions to ticket action menu")
        return bool(ok)
    except Exception as e:
        _warn(f"patch failed: {e!r}")
        return False


apply()

__all__ = ["apply"]
