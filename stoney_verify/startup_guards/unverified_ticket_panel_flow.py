from __future__ import annotations

"""Auto-route verification-needed users from the public ticket panel.

The public Create Ticket button should feel like TicketTool for verified users,
but members who still need verification should not be asked to describe a
support issue. They should get a verification ticket immediately.

Production rule:
A member is verification-needed when they are not staff/admin, do not have the
configured approved/verified role, and do not have the configured full access
member/resident role. The configured waiting/pending role is helpful, but it is
not required because fresh joins can press the panel before Discord/the bot
finishes assigning that role.
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
    waiting/pending role yet. If verification roles are configured and the user
    has neither approved nor full-access/member roles, they are treated as
    verification-needed.
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

    waiting_role_id = _config_role_id(cfg, "unverified_role_id")
    approved_role_id = _config_role_id(cfg, "verified_role_id")
    member_role_id = _config_role_id(cfg, "resident_role_id", "member_role_id")

    has_waiting = waiting_role_id > 0 and _member_has_role(member, waiting_role_id)
    has_approved = approved_role_id > 0 and _member_has_role(member, approved_role_id)
    has_member = member_role_id > 0 and _member_has_role(member, member_role_id)

    if has_approved or has_member:
        return False

    if has_waiting:
        _log(f"verification-needed user matched by waiting/pending role guild={member.guild.id} user={member.id}")
        return True

    # Fresh-join safety: role assignment can lag behind the button press. Once a
    # server has verification/member roles configured, lacking approved/member
    # roles is enough to send the user to verification instead of the generic
    # support modal.
    if waiting_role_id > 0 or approved_role_id > 0 or member_role_id > 0:
        _log(
            "verification-needed user matched by missing approved/member roles "
            f"guild={member.guild.id} user={member.id} roles={sorted(_member_role_ids(member))} "
            f"configured_waiting={waiting_role_id} configured_approved={approved_role_id} configured_member={member_role_id}"
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


async def _resolve_channel_from_ticket_result(guild: discord.Guild, result: Any) -> Optional[discord.TextChannel]:
    if isinstance(result, discord.TextChannel):
        return result

    if isinstance(result, dict):
        channel_id = _safe_int(result.get("discord_thread_id") or result.get("channel_id"), 0)
        if channel_id > 0:
            channel = guild.get_channel(channel_id)
            if isinstance(channel, discord.TextChannel):
                return channel
            try:
                fetched = await guild.fetch_channel(channel_id)
                return fetched if isinstance(fetched, discord.TextChannel) else None
            except Exception:
                return None

    # Some older paths may return (channel, row) or similar.
    if isinstance(result, (list, tuple)):
        for item in result:
            resolved = await _resolve_channel_from_ticket_result(guild, item)
            if resolved is not None:
                return resolved

    return None


async def _call_ticket_creator(
    create_ticket_channel: Any,
    *,
    guild: discord.Guild,
    member: discord.Member,
    category: str,
    reason: str,
    metadata: Dict[str, Any],
) -> Optional[discord.TextChannel]:
    """Call ticket creation across old/new service signatures safely.

    The runtime wrapped service can expose ``**kwargs`` while forwarding to an
    older keyword-only function. So the first attempts must be the smallest
    payloads the old service accepts: guild, owner, category, and maybe is_ghost.
    Do not lead with ``reason=`` or alias kwargs like ``member=``/``requester=``.
    """
    canonical: Dict[str, Any] = {
        "guild": guild,
        "owner": member,
        "category": category,
        "is_ghost": False,
        "reason": reason,
        "metadata": metadata,
        "extra_metadata": metadata,
        "category_metadata": metadata,
        "initial_message": reason,
        "title": "Verification",
        "priority": "medium",
    }

    attempts: list[tuple[str, Any]] = [
        ("owner_category_ghost", lambda: create_ticket_channel(guild=guild, owner=member, category=category, is_ghost=False)),
        ("owner_category", lambda: create_ticket_channel(guild=guild, owner=member, category=category)),
        ("owner_only", lambda: create_ticket_channel(guild=guild, owner=member)),
    ]

    try:
        sig = inspect.signature(create_ticket_channel)
        params = sig.parameters
        has_varkw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
        if not has_varkw:
            filtered = {k: v for k, v in canonical.items() if k in params}
            if filtered:
                # Put exact signature match first for true new signatures, but
                # keep no-reason fallback attempts immediately after it.
                attempts.insert(0, ("signature_filtered", lambda filtered=filtered: create_ticket_channel(**filtered)))
    except Exception:
        pass

    attempts.extend(
        [
            ("owner_category_reason", lambda: create_ticket_channel(guild=guild, owner=member, category=category, reason=reason)),
            ("owner_category_reason_ghost", lambda: create_ticket_channel(guild=guild, owner=member, category=category, reason=reason, is_ghost=False)),
            ("requester_category", lambda: create_ticket_channel(guild=guild, requester=member, category=category, is_ghost=False)),
            ("user_category", lambda: create_ticket_channel(guild=guild, user=member, category=category, is_ghost=False)),
            ("positional_four", lambda: create_ticket_channel(guild, member, category, reason)),
            ("positional_three", lambda: create_ticket_channel(guild, member, category)),
        ]
    )

    seen: set[str] = set()
    type_errors: list[str] = []
    last_error: Optional[BaseException] = None

    for label, factory in attempts:
        if label in seen:
            continue
        seen.add(label)
        try:
            result = await factory()
            channel = await _resolve_channel_from_ticket_result(guild, result)
            if channel is not None:
                return channel
            if result is not None:
                _warn(f"ticket creator returned unsupported result label={label} type={type(result).__name__}")
                return None
        except TypeError as e:
            last_error = e
            type_errors.append(f"{label}: {e}")
            continue
        except Exception as e:
            last_error = e
            raise

    if type_errors:
        _warn("ticket creator signature attempts failed: " + " | ".join(type_errors[:8]))
    if last_error is not None:
        raise last_error
    return None


async def _create_verification_ticket(guild: discord.Guild, member: discord.Member) -> Optional[discord.TextChannel]:
    from stoney_verify.tickets_new import service

    create_ticket_channel = getattr(service, "create_ticket_channel")
    reason = "Verification assistance requested from the public ticket panel."
    category = "verification_issue"
    metadata = {
        "auto_routed": True,
        "auto_route_reason": "verification_needed_member_pressed_public_ticket_panel",
        "matched_category_slug": category,
        "matched_category_name": "Verification",
        "matched_intake_type": "verification",
    }

    return await _call_ticket_creator(
        create_ticket_channel,
        guild=guild,
        member=member,
        category=category,
        reason=reason,
        metadata=metadata,
    )


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
        _warn(f"verification ticket auto-route failed guild={getattr(guild, 'id', None)} user={getattr(member, 'id', None)}: {e!r}")
        await _reply(
            interaction,
            "❌ I could not open your verification ticket. Staff has been given the technical reason in the bot logs. Please try again in a moment.",
        )
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
