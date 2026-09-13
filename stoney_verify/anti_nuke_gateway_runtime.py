from __future__ import annotations

"""Compatibility entrypoint for the AntiNuke audit gateway.

The broad implementation lives in ``anti_nuke_guardian_runtime``. This module keeps
the historical installer and dangerous-role-create fast path so production boot and
existing regressions retain one stable contract while the guardian owns the wider
audit surface and coordinated-attack circuit breaker.
"""

from typing import Any

import discord

from . import anti_nuke
from . import anti_nuke_guardian_runtime as guardian

_INSTALL_FLAG = "_dank_antinuke_gateway_runtime_installed"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


def _target_label(entry: Any) -> str:
    target = getattr(entry, "target", None)
    target_id = _safe_int(getattr(target, "id", 0), 0)
    name = str(getattr(target, "name", "") or target or "role")
    return f"@{name} (`{target_id}`)" if target_id else f"@{name}"


async def _contain_actor(guild: discord.Guild, actor: Any, *, reason: str):
    actor_id = _safe_int(getattr(actor, "id", 0), 0)
    lock = anti_nuke._lock_for(  # noqa: SLF001
        anti_nuke._CONTAINMENT_LOCKS,
        (int(guild.id), actor_id),
    )
    async with lock:
        return await anti_nuke._contain_actor(  # noqa: SLF001
            guild,
            actor,
            reason=reason,
        )


async def _handle_role_create(guild: discord.Guild, entry: Any) -> None:
    role = getattr(entry, "target", None)
    if role is None:
        return
    actor = getattr(entry, "user", None)

    # Harmless-looking role creation still feeds the canonical counter. It also
    # participates in the guild panic window so multiple delegated operators cannot
    # split role-creation spam across identities.
    if not anti_nuke.role_has_dangerous_permissions(role):
        panic_active, panic_triggered, observed = guardian._panic_state(  # noqa: SLF001
            guild,
            actor,
            "role_create",
        )
        handled = await anti_nuke._process_claimed_destructive_event(  # noqa: SLF001
            guild,
            entry=entry,
            action_key="role_create",
            action_label="Role creation burst",
            target_label=_target_label(entry),
            threshold_key="antinuke_role_delete_threshold",
            threshold_override=1 if panic_active else None,
        )
        if not handled:
            guardian._clear_panic(int(guild.id))  # noqa: SLF001
        elif panic_triggered:
            await guardian._post_panic_incident(  # noqa: SLF001
                guild,
                actor=actor,
                action_label="Role creation burst",
                target_label=_target_label(entry),
                observed=observed,
            )
        return

    settings = await anti_nuke.get_antinuke_settings(int(guild.id))
    if not settings["antinuke_enabled"]:
        return
    if anti_nuke._actor_is_owner_or_bot(guild, actor):  # noqa: SLF001
        return

    _panic_active, panic_triggered, observed = guardian._panic_state(  # noqa: SLF001
        guild,
        actor,
        "role_create",
    )
    response = "Alert-only mode: newly created dangerous role was left unchanged."
    if settings["antinuke_mode"] == "contain":
        deleted = False
        try:
            me = getattr(guild, "me", None)
            if (
                isinstance(me, discord.Member)
                and not bool(getattr(role, "managed", False))
                and anti_nuke._role_is_below(role, me.top_role)  # noqa: SLF001
            ):
                await role.delete(
                    reason="Dank Shield AntiNuke rollback: dangerous role creation"
                )
                deleted = True
        except Exception:
            deleted = False

        removed, blocked = await _contain_actor(
            guild,
            actor,
            reason="Dank Shield AntiNuke containment: dangerous role creation",
        )
        response = (
            "Deleted the newly created dangerous role."
            if deleted
            else "Could not delete the newly created dangerous role."
        )
        if removed:
            response += " Containment actions: " + ", ".join(removed) + "."
        if blocked:
            response += " Could not fully contain actor: " + ", ".join(blocked) + "."

    await anti_nuke._post_incident(  # noqa: SLF001
        guild,
        title="🚨 AntiNuke Dangerous Role Created",
        actor=actor,
        action_label="Dangerous role created",
        target_label=_target_label(entry),
        response_label=response,
        details="Gateway audit fast path; REST audit lookup was not required.",
    )
    if panic_triggered:
        await guardian._post_panic_incident(  # noqa: SLF001
            guild,
            actor=actor,
            action_label="Dangerous role created",
            target_label=_target_label(entry),
            observed=observed,
        )


async def _on_audit_log_entry_create(entry: discord.AuditLogEntry) -> None:
    action_name = guardian._action_name(entry)  # noqa: SLF001
    if action_name != "role_create":
        await guardian._on_audit_log_entry_create(entry)  # noqa: SLF001
        return

    guild = getattr(entry, "guild", None)
    if guild is None:
        return
    actor = await guardian._resolve_actor(guild, entry)  # noqa: SLF001
    if actor is None:
        return
    claimed = guardian._EntryProxy(entry, actor)  # noqa: SLF001
    if anti_nuke._consume_audit_entry(claimed):  # noqa: SLF001
        return
    await _handle_role_create(guild, claimed)


def install_anti_nuke_gateway_runtime(bot: discord.Client) -> bool:
    """Install exactly one guardian-backed audit listener set on production."""

    if bool(getattr(bot, _INSTALL_FLAG, False)) or bool(
        getattr(bot, guardian._INSTALL_FLAG, False)  # noqa: SLF001
    ):
        return False

    bot.add_listener(_on_audit_log_entry_create, "on_audit_log_entry_create")
    bot.add_listener(
        guardian._on_guild_channel_update_fallback,  # noqa: SLF001
        "on_guild_channel_update",
    )
    setattr(bot, _INSTALL_FLAG, True)
    setattr(bot, guardian._INSTALL_FLAG, True)  # noqa: SLF001

    moderation = bool(getattr(getattr(bot, "intents", None), "moderation", False))
    if moderation:
        print(
            "🛡️ AntiNuke guardian gateway active with broad audit coverage and "
            "target-correct overwrite fallback"
        )
    else:
        print(
            "⚠️ AntiNuke gateway installed without moderation intent; "
            "native/REST fallbacks remain active"
        )
    return True


__all__ = ["install_anti_nuke_gateway_runtime"]
