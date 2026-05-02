from __future__ import annotations

"""Auto-route verification-needed users from the public ticket panel.

The public Create Ticket button should feel like TicketTool for verified users,
but members who still need verification should not be asked to describe a
support issue. They should get a verification ticket immediately.

Production rule:
A member is verification-needed when they are not staff/admin, do not have the
configured Verified role, and do not have the configured Resident/Member role.
The configured Unverified role is helpful, but it is not required because fresh
joins can press the panel before Discord/the bot finishes assigning that role.
"""

import inspect
from typing import Any, Dict, Optional

import discord

_PATCHED = False


def _log(message: str) -> None:
    try:
        print(f"🎟️ unverified_ticket_panel_flow {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ unverified_ticket_panel_flow {message}")
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


def _member_has_role(member: discord.Member, role_id: int) -> bool:
    if role_id <= 0:
        return False
    try:
        return any(int(getattr(role, "id", 0) or 0) == int(role_id) for role in (member.roles or []))
    except Exception:
        return False


def _member_role_ids(member: discord.Member) -> set[int]:
    try:
        return {int(getattr(role, "id", 0) or 0) for role in (member.roles or []) if int(getattr(role, "id", 0) or 0) > 0}
    except Exception:
        return set()


def _config_role_id(cfg: Any, *names: str) -> int:
    for name in names:
        value = _safe_int(getattr(cfg, name, 0), 0)
        if value > 0:
            return value
    return 0


def _is_staff(member: discord.Member, cfg: Any) -> bool:
    try:
        if member.guild_permissions.administrator or member.guild_permissions.manage_guild or member.guild_permissions.manage_channels:
            return True
    except Exception:
        pass

    staff_like_role_ids = {
        _config_role_id(cfg, "staff_role_id"),
        _config_role_id(cfg, "vc_staff_role_id"),
        _config_role_id(cfg, "server_control_role_id"),
        _config_role_id(cfg, "control_role_id"),
        _config_role_id(cfg, "perm_role_id"),
        _config_role_id(cfg, "bot_manager_role_id"),
    }
    staff_like_role_ids.discard(0)

    member_role_ids = _member_role_ids(member)
    return bool(staff_like_role_ids and member_role_ids.intersection(staff_like_role_ids))


async def _is_unverified_only_member(member: discord.Member) -> bool:
    """Return True when the member should skip support intake and verify.

    This deliberately handles the race where a fresh join has not received the
    Unverified role yet. If verification roles are configured and the user has
    neither Verified nor Resident/Member, they are treated as unverified.
    """
    if getattr(member, "bot", False):
        return False

    try:
        from stoney_verify.guild_config import get_guild_config

        cfg = await get_guild_config(member.guild.id, refresh=True)
    except Exception as e:
        _warn(f"config lookup failed while checking verification-needed member guild={getattr(member.guild, 'id', None)} user={getattr(member, 'id', None)}: {e!r}")
        return False

    if _is_staff(member, cfg):
        return False

    unverified_role_id = _config_role_id(cfg, "unverified_role_id")
    verified_role_id = _config_role_id(cfg, "verified_role_id")
    resident_role_id = _config_role_id(cfg, "resident_role_id", "member_role_id")

    has_unverified = unverified_role_id > 0 and _member_has_role(member, unverified_role_id)
    has_verified = verified_role_id > 0 and _member_has_role(member, verified_role_id)
    has_resident = resident_role_id > 0 and _member_has_role(member, resident_role_id)

    if has_verified or has_resident:
        return False

    if has_unverified:
        _log(f"verification-needed user matched by Unverified role guild={member.guild.id} user={member.id}")
        return True

    # Fresh-join safety: role assignment can lag behind the button press. Once a
    # server has verification/member roles configured, lacking Verified/Resident
    # is enough to send the user to verification instead of the generic modal.
    if unverified_role_id > 0 or verified_role_id > 0 or resident_role_id > 0:
        _log(
            "verification-needed user matched by missing verified/resident roles "
            f"guild={member.guild.id} user={member.id} roles={sorted(_member_role_ids(member))} "
            f"configured_unverified={unverified_role_id} configured_verified={verified_role_id} configured_resident={resident_role_id}"
        )
        return True

    # If no role config exists, do not hijack the public ticket panel. Setup gate
    # should handle missing configuration instead.
    return False


def _interaction_member(interaction: discord.Interaction) -> Optional[discord.Member]:
    try:
        if isinstance(interaction.user, discord.Member):
            return interaction.user
        guild = interaction.guild
        if guild is None:
            return None
        return guild.get_member(int(interaction.user.id))
    except Exception:
        return None


def _site_url() -> str:
    try:
        from stoney_verify import globals as g

        return str(getattr(g, "VERIFY_SITE_URL", "") or getattr(g, "SITE_URL", "") or "").strip()
    except Exception:
        return ""


def _token_ttl_minutes() -> int:
    try:
        from stoney_verify import globals as g

        return int(getattr(g, "TOKEN_TTL_MINUTES", 20) or 20)
    except Exception:
        return 20


def _allow_user_regen() -> bool:
    try:
        from stoney_verify import globals as g

        return bool(getattr(g, "ALLOW_USER_VERIFYLINK", False))
    except Exception:
        return False


async def _reply(interaction: discord.Interaction, content: str) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(content, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        else:
            await interaction.followup.send(content, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        pass


async def _defer(interaction: discord.Interaction) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
    except Exception:
        pass


async def _existing_open_ticket_channel(guild: discord.Guild, owner_id: int) -> Optional[discord.TextChannel]:
    try:
        from stoney_verify.tickets_new.service import find_open_ticket_for_owner

        row = await find_open_ticket_for_owner(guild_id=int(guild.id), owner_id=int(owner_id), category=None)
        if not isinstance(row, dict):
            return None
        channel_id = _safe_int(row.get("discord_thread_id") or row.get("channel_id"), 0)
        if channel_id <= 0:
            return None
        ch = guild.get_channel(channel_id)
        if isinstance(ch, discord.TextChannel):
            return ch
        fetched = await guild.fetch_channel(channel_id)
        return fetched if isinstance(fetched, discord.TextChannel) else None
    except Exception:
        return None


async def _post_verify_ui(channel: discord.TextChannel, member: discord.Member) -> None:
    try:
        from stoney_verify.verify_ui import post_or_replace_verify_ui

        await post_or_replace_verify_ui(
            channel,
            requester_id=int(member.id),
            reason="auto-routed from public ticket panel",
            site_url=_site_url(),
            ttl_minutes=_token_ttl_minutes(),
            allow_regen=_allow_user_regen(),
        )
    except Exception as e:
        _warn(f"verify UI post failed channel={getattr(channel, 'id', None)} user={getattr(member, 'id', None)}: {e!r}")


async def _create_verification_ticket(guild: discord.Guild, member: discord.Member) -> Optional[discord.TextChannel]:
    from stoney_verify.tickets_new import service

    create_ticket_channel = getattr(service, "create_ticket_channel")
    reason = "Verification assistance requested by unverified member from public ticket panel."
    category = "verification_issue"
    metadata = {
        "auto_routed": True,
        "auto_route_reason": "verification_needed_member_pressed_public_ticket_panel",
        "matched_category_slug": category,
        "matched_category_name": "Verification",
        "matched_intake_type": "verification",
    }

    kwargs: Dict[str, Any] = {
        "guild": guild,
        "owner": member,
        "member": member,
        "requester": member,
        "user": member,
        "created_by": member,
        "actor": member,
        "category": category,
        "reason": reason,
        "initial_message": reason,
        "title": "Verification",
        "priority": "medium",
        "is_ghost": False,
        "metadata": metadata,
        "extra_metadata": metadata,
        "category_metadata": metadata,
    }

    try:
        sig = inspect.signature(create_ticket_channel)
        params = sig.parameters
        if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
            filtered = kwargs
        else:
            filtered = {k: v for k, v in kwargs.items() if k in params}
        result = await create_ticket_channel(**filtered)
        return result if isinstance(result, discord.TextChannel) else None
    except TypeError as first_error:
        # Fallback for older signatures during refactor windows.
        patterns = (
            lambda: create_ticket_channel(guild=guild, owner=member, category=category, reason=reason, is_ghost=False),
            lambda: create_ticket_channel(guild, member, category, reason),
            lambda: create_ticket_channel(guild, member, category=category, reason=reason),
        )
        for factory in patterns:
            try:
                result = await factory()
                return result if isinstance(result, discord.TextChannel) else None
            except TypeError:
                continue
        raise first_error


async def _handle_unverified_panel_click(interaction: discord.Interaction) -> bool:
    guild = interaction.guild
    member = _interaction_member(interaction)
    if guild is None or member is None:
        return False

    if not await _is_unverified_only_member(member):
        return False

    await _defer(interaction)

    existing = await _existing_open_ticket_channel(guild, int(member.id))
    if existing is not None:
        await _post_verify_ui(existing, member)
        await _reply(
            interaction,
            f"✅ You already have a verification ticket open: {existing.mention}\nI refreshed the verification panel there.",
        )
        return True

    try:
        channel = await _create_verification_ticket(guild, member)
    except Exception as e:
        await _reply(interaction, f"❌ I could not open your verification ticket: `{type(e).__name__}: {str(e)[:220]}`")
        return True

    if channel is None:
        await _reply(interaction, "❌ I tried to open your verification ticket, but the ticket service did not return a channel.")
        return True

    await _post_verify_ui(channel, member)
    await _reply(
        interaction,
        f"✅ Opened your verification ticket: {channel.mention}\nUse the verification buttons inside that ticket.",
    )
    return True


def _button_looks_like_create_ticket(item: Any) -> bool:
    try:
        if not isinstance(item, discord.ui.Button):
            return False
        label = str(getattr(item, "label", "") or "").lower()
        custom_id = str(getattr(item, "custom_id", "") or "").lower()
        text = f"{label} {custom_id}"
        return "ticket" in text and ("create" in text or "open" in text or "panel" in text)
    except Exception:
        return False


def patch_ticket_panel_view() -> bool:
    global _PATCHED
    if _PATCHED:
        return True

    try:
        from stoney_verify.tickets_new import panel

        view_cls = getattr(panel, "TicketPanelView", None)
        if view_cls is None:
            _warn("TicketPanelView not found")
            return False

        original_init = getattr(view_cls, "__init__", None)
        if not callable(original_init) or getattr(original_init, "_unverified_flow_wrapped", False):
            _PATCHED = True
            return True

        def _patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
            original_init(self, *args, **kwargs)

            try:
                for item in list(getattr(self, "children", []) or []):
                    if not _button_looks_like_create_ticket(item):
                        continue
                    original_callback = getattr(item, "callback", None)
                    if not callable(original_callback) or getattr(original_callback, "_unverified_flow_wrapped", False):
                        continue

                    async def _wrapped_callback(interaction: discord.Interaction, *, _original=original_callback) -> Any:
                        handled = await _handle_unverified_panel_click(interaction)
                        if handled:
                            return None
                        return await _original(interaction)

                    try:
                        setattr(_wrapped_callback, "_unverified_flow_wrapped", True)
                    except Exception:
                        pass
                    item.callback = _wrapped_callback
            except Exception as e:
                _warn(f"failed wiring TicketPanelView button callback: {e!r}")

        try:
            setattr(_patched_init, "_unverified_flow_wrapped", True)
        except Exception:
            pass
        setattr(view_cls, "__init__", _patched_init)
        _PATCHED = True
        _log("patched TicketPanelView so verification-needed members open verification tickets directly")
        return True
    except Exception as e:
        _warn(f"patch failed: {e!r}")
        return False


patch_ticket_panel_view()


__all__ = ["patch_ticket_panel_view"]
