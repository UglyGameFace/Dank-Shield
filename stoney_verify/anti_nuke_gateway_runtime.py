from __future__ import annotations

"""Compatibility entrypoint for the AntiNuke audit gateway.

The broad implementation lives in ``anti_nuke_guardian_runtime``. This module keeps
the historical installer and immediate authority-rollback paths so production boot
retains one stable listener contract while all punishment still uses the canonical
AntiNuke containment engine.
"""

from datetime import datetime, timezone
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


def _member_target_label(entry: Any) -> str:
    target = getattr(entry, "target", None)
    target_id = _safe_int(getattr(target, "id", 0), 0)
    mention = getattr(target, "mention", None)
    name = str(getattr(target, "name", "") or target or "member")
    if mention and target_id:
        return f"{mention} (`{target_id}`)"
    return f"{name} (`{target_id}`)" if target_id else name


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


async def _resolve_target_member(guild: discord.Guild, entry: Any) -> Any:
    target = getattr(entry, "target", None)
    target_id = _safe_int(getattr(target, "id", 0), 0)
    if target_id <= 0:
        return None
    if isinstance(target, discord.Member):
        return target
    getter = getattr(guild, "get_member", None)
    if callable(getter):
        try:
            found = getter(target_id)
        except Exception:
            found = None
        if found is not None:
            return found
    fetcher = getattr(guild, "fetch_member", None)
    if callable(fetcher):
        try:
            return await fetcher(target_id)
        except Exception:
            return None
    return target if hasattr(target, "remove_roles") else None


def _role_diff(entry: Any) -> tuple[list[Any], list[Any]]:
    before = getattr(entry, "before", None)
    after = getattr(entry, "after", None)
    removed = list(getattr(before, "roles", []) or []) if before is not None else []
    added = list(getattr(after, "roles", []) or []) if after is not None else []
    return added, removed


def _timeout_extended(entry: Any) -> bool:
    before = getattr(entry, "before", None)
    after = getattr(entry, "after", None)
    before_until = getattr(before, "timed_out_until", None) if before is not None else None
    after_until = getattr(after, "timed_out_until", None) if after is not None else None
    if not isinstance(after_until, datetime):
        return False
    if after_until.tzinfo is None:
        after_until = after_until.replace(tzinfo=timezone.utc)
    after_until = after_until.astimezone(timezone.utc)
    now = datetime.now(timezone.utc)
    if after_until <= now:
        return False
    if not isinstance(before_until, datetime):
        return True
    if before_until.tzinfo is None:
        before_until = before_until.replace(tzinfo=timezone.utc)
    return after_until > before_until.astimezone(timezone.utc)


async def _handle_role_create(guild: discord.Guild, entry: Any) -> None:
    role = getattr(entry, "target", None)
    if role is None:
        return
    actor = getattr(entry, "user", None)

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
                and isinstance(role, discord.Role)
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


async def _handle_dangerous_role_update(
    guild: discord.Guild,
    entry: Any,
    actor: Any,
) -> None:
    added = anti_nuke.dangerous_permissions_added(
        getattr(entry, "before", None),
        getattr(entry, "after", None),
    )
    if not added:
        return
    settings = await anti_nuke.get_antinuke_settings(int(guild.id))
    if (
        not settings["antinuke_enabled"]
        or not settings["antinuke_protect_role_escalation"]
    ):
        return
    if anti_nuke._actor_is_owner_or_bot(guild, actor):  # noqa: SLF001
        return

    _panic_active, panic_triggered, observed = guardian._panic_state(  # noqa: SLF001
        guild,
        actor,
        "role_update",
    )
    role = getattr(entry, "target", None)
    rollback = "Alert-only mode: dangerous role escalation was not reverted."
    if settings["antinuke_mode"] == "contain":
        reverted = False
        before_permissions = getattr(getattr(entry, "before", None), "permissions", None)
        try:
            me = getattr(guild, "me", None)
            if (
                before_permissions is not None
                and isinstance(me, discord.Member)
                and isinstance(role, discord.Role)
                and not bool(getattr(role, "managed", False))
                and anti_nuke._role_is_below(role, me.top_role)  # noqa: SLF001
            ):
                await role.edit(
                    permissions=before_permissions,
                    reason=(
                        "Dank Shield AntiNuke rollback: gateway-detected dangerous "
                        "role permission escalation"
                    ),
                )
                reverted = True
        except Exception:
            reverted = False

        removed, blocked = await _contain_actor(
            guild,
            actor,
            reason="Dank Shield AntiNuke containment: dangerous role escalation",
        )
        rollback = (
            "Reverted the dangerous role permissions."
            if reverted
            else "Could not revert the role before containment."
        )
        if removed:
            rollback += " Containment actions: " + ", ".join(removed) + "."
        if blocked:
            rollback += " Containment blockers: " + ", ".join(blocked) + "."

    await anti_nuke._post_incident(  # noqa: SLF001
        guild,
        title="🚨 AntiNuke Permission Escalation",
        actor=actor,
        action_label="Dangerous permissions added: " + ", ".join(added),
        target_label=_target_label(entry),
        response_label=rollback,
        details="Gateway-fast authority rollback; REST propagation was not required.",
    )
    if panic_triggered:
        await guardian._post_panic_incident(  # noqa: SLF001
            guild,
            actor=actor,
            action_label="Dangerous role permission escalation",
            target_label=_target_label(entry),
            observed=observed,
        )


async def _handle_member_role_update(
    guild: discord.Guild,
    entry: Any,
    actor: Any,
) -> None:
    added_roles, removed_roles = _role_diff(entry)
    settings = await anti_nuke.get_antinuke_settings(int(guild.id))
    if not settings["antinuke_enabled"]:
        return
    if anti_nuke._actor_is_owner_or_bot(guild, actor):  # noqa: SLF001
        return

    trusted_role_ids = set(
        anti_nuke._safe_id_list(settings.get("antinuke_trusted_role_ids"))  # noqa: SLF001
    )
    sensitive_added = [
        role
        for role in added_roles
        if anti_nuke.role_has_dangerous_permissions(role)
        or _safe_int(getattr(role, "id", 0), 0) in trusted_role_ids
    ]

    if sensitive_added and settings["antinuke_protect_role_escalation"]:
        _panic_active, panic_triggered, observed = guardian._panic_state(  # noqa: SLF001
            guild,
            actor,
            "role_update",
        )
        target = await _resolve_target_member(guild, entry)
        names = ", ".join(str(getattr(role, "name", role)) for role in sensitive_added)
        response = "Alert-only mode: security-sensitive role grant was not reverted."

        if settings["antinuke_mode"] == "contain":
            removable: list[Any] = []
            blocked_roles: list[Any] = []
            me = getattr(guild, "me", None)
            target_manageable = (
                target is not None
                and anti_nuke._member_is_manageable_by_bot(guild, target)  # noqa: SLF001
            )
            for role in sensitive_added:
                if (
                    isinstance(me, discord.Member)
                    and target_manageable
                    and isinstance(role, discord.Role)
                    and not bool(getattr(role, "managed", False))
                    and anti_nuke._role_is_below(role, me.top_role)  # noqa: SLF001
                ):
                    removable.append(role)
                else:
                    blocked_roles.append(role)

            if removable and target is not None:
                try:
                    await target.remove_roles(
                        *removable,
                        reason=(
                            "Dank Shield AntiNuke rollback: gateway-detected "
                            "security-sensitive role grant"
                        ),
                    )
                except Exception:
                    blocked_roles.extend(removable)
                    removable = []

            removed_actor, blocked_actor = await _contain_actor(
                guild,
                actor,
                reason="Dank Shield AntiNuke containment: security-sensitive role grant",
            )
            response = "Rolled back security-sensitive role grant(s): " + names + "."
            if blocked_roles:
                response += " Target rollback blockers: " + ", ".join(
                    str(getattr(role, "name", role)) for role in blocked_roles
                ) + "."
            if removed_actor:
                response += " Containment actions: " + ", ".join(removed_actor) + "."
            if blocked_actor:
                response += " Containment blockers: " + ", ".join(blocked_actor) + "."

        await anti_nuke._post_incident(  # noqa: SLF001
            guild,
            title="🚨 AntiNuke Security-Sensitive Role Grant",
            actor=actor,
            action_label="Dangerous or trusted-exemption role granted",
            target_label=_member_target_label(entry) + " • " + names,
            response_label=response,
            details="Gateway-fast role-grant rollback; REST propagation was not required.",
        )
        if panic_triggered:
            await guardian._post_panic_incident(  # noqa: SLF001
                guild,
                actor=actor,
                action_label="Security-sensitive role grant",
                target_label=_member_target_label(entry),
                observed=observed,
            )
        return

    if not removed_roles:
        return

    panic_active, panic_triggered, observed = guardian._panic_state(  # noqa: SLF001
        guild,
        actor,
        "role_update",
    )
    handled = await anti_nuke._process_claimed_destructive_event(  # noqa: SLF001
        guild,
        entry=entry,
        action_key="member_role_remove",
        action_label="Member role removal",
        target_label=(
            _member_target_label(entry)
            + " • removed: "
            + ", ".join(str(getattr(role, "name", role)) for role in removed_roles[:10])
        ),
        threshold_key="antinuke_role_delete_threshold",
        threshold_override=1 if panic_active else None,
    )
    if not handled:
        guardian._clear_panic(int(guild.id))  # noqa: SLF001
    elif panic_triggered:
        await guardian._post_panic_incident(  # noqa: SLF001
            guild,
            actor=actor,
            action_label="Member role removal",
            target_label=_member_target_label(entry),
            observed=observed,
        )


async def _handle_member_timeout(
    guild: discord.Guild,
    entry: Any,
    actor: Any,
) -> None:
    panic_active, panic_triggered, observed = guardian._panic_state(  # noqa: SLF001
        guild,
        actor,
        "kick",
    )
    handled = await anti_nuke._process_claimed_destructive_event(  # noqa: SLF001
        guild,
        entry=entry,
        action_key="member_timeout",
        action_label="Member timeout applied or extended",
        target_label=_member_target_label(entry),
        threshold_key="antinuke_kick_threshold",
        threshold_override=1 if panic_active else None,
    )
    if not handled:
        guardian._clear_panic(int(guild.id))  # noqa: SLF001
    elif panic_triggered:
        await guardian._post_panic_incident(  # noqa: SLF001
            guild,
            actor=actor,
            action_label="Member timeout applied or extended",
            target_label=_member_target_label(entry),
            observed=observed,
        )


async def _on_audit_log_entry_create(entry: discord.AuditLogEntry) -> None:
    action_name = guardian._action_name(entry)  # noqa: SLF001

    if action_name == "role_create":
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
        return

    if action_name == "role_update":
        if not anti_nuke.dangerous_permissions_added(
            getattr(entry, "before", None),
            getattr(entry, "after", None),
        ):
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
        await _handle_dangerous_role_update(guild, claimed, actor)
        return

    if action_name == "member_role_update":
        added_roles, removed_roles = _role_diff(entry)
        if not added_roles and not removed_roles:
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
        await _handle_member_role_update(guild, claimed, actor)
        return

    if action_name == "member_update" and _timeout_extended(entry):
        guild = getattr(entry, "guild", None)
        if guild is None:
            return
        actor = await guardian._resolve_actor(guild, entry)  # noqa: SLF001
        if actor is None:
            return
        claimed = guardian._EntryProxy(entry, actor)  # noqa: SLF001
        if anti_nuke._consume_audit_entry(claimed):  # noqa: SLF001
            return
        await _handle_member_timeout(guild, claimed, actor)
        return

    await guardian._on_audit_log_entry_create(entry)  # noqa: SLF001


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
            "🛡️ AntiNuke guardian gateway active with broad audit coverage, "
            "authority rollback, and target-correct overwrite fallback"
        )
    else:
        print(
            "⚠️ AntiNuke gateway installed without moderation intent; "
            "native/REST fallbacks remain active"
        )
    return True


__all__ = ["install_anti_nuke_gateway_runtime"]
