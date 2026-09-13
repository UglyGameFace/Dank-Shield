from __future__ import annotations

"""Gateway transport hardening for the canonical :mod:`stoney_verify.anti_nuke` engine.

Discord's ``on_audit_log_entry_create`` gateway event can arrive before a REST
``guild.audit_logs`` lookup would observe the same entry. This adapter does not own
AntiNuke policy: it feeds high-confidence audit entries directly into the existing
canonical counters, trust rules, containment primitive, and incident logger.

The existing Discord event + REST correlation listeners remain as fallback. Shared
audit-entry dedupe guarantees that whichever path claims the entry first is the only
path that enforces it.
"""

from typing import Any, Optional

import discord

from . import anti_nuke


_DIRECT_DESTRUCTIVE_ACTIONS: dict[str, tuple[str, str, Optional[int]]] = {
    "channel_create": (
        "Channel creation burst",
        "antinuke_channel_delete_threshold",
        None,
    ),
    "channel_delete": (
        "Mass channel deletion",
        "antinuke_channel_delete_threshold",
        None,
    ),
    "role_delete": (
        "Mass role deletion",
        "antinuke_role_delete_threshold",
        None,
    ),
    "ban": (
        "Mass member bans",
        "antinuke_ban_threshold",
        None,
    ),
    "kick": (
        "Mass member kicks",
        "antinuke_kick_threshold",
        None,
    ),
    "member_prune": (
        "Member prune triggered",
        "antinuke_kick_threshold",
        1,
    ),
    "webhook_create": (
        "Webhook creation",
        "antinuke_webhook_create_threshold",
        None,
    ),
    "webhook_update": (
        "Webhook modification",
        "antinuke_webhook_create_threshold",
        None,
    ),
    "webhook_delete": (
        "Webhook deletion",
        "antinuke_webhook_create_threshold",
        None,
    ),
}

_INSTALL_FLAG = "_dank_antinuke_gateway_runtime_installed"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


def _action_name(entry: Any) -> str:
    action = getattr(entry, "action", None)
    name = getattr(action, "name", None)
    if name:
        return str(name).strip().lower()
    text = str(action or "").strip().lower()
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text


def _target_label(action_name: str, entry: Any) -> str:
    target = getattr(entry, "target", None)
    target_id = _safe_int(getattr(target, "id", 0), 0)
    target_name = str(getattr(target, "name", "") or target or "Unknown")

    if action_name in {"channel_create", "channel_delete"}:
        return f"#{target_name} (`{target_id}`)" if target_id else f"#{target_name}"
    if action_name in {"role_create", "role_delete"}:
        return f"@{target_name} (`{target_id}`)" if target_id else f"@{target_name}"
    if action_name in {"ban", "kick"}:
        return f"{target_name} (`{target_id}`)" if target_id else target_name
    if action_name == "member_prune":
        return "member prune operation"
    if action_name.startswith("webhook_"):
        return (
            f"Webhook {target_name} (`{target_id}`)"
            if target_id
            else f"Webhook {target_name}"
        )
    return f"{target_name} (`{target_id}`)" if target_id else target_name


async def _contain_actor(guild: discord.Guild, actor: Any, *, reason: str):
    actor_id = _safe_int(getattr(actor, "id", 0), 0)
    lock = anti_nuke._lock_for(  # noqa: SLF001 - canonical AntiNuke primitive
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

    # Dangerous role creation keeps the canonical immediate rollback semantics.
    if anti_nuke.role_has_dangerous_permissions(role):
        settings = await anti_nuke.get_antinuke_settings(int(guild.id))
        if not settings["antinuke_enabled"]:
            return

        actor = getattr(entry, "user", None)
        if anti_nuke._actor_is_owner_or_bot(guild, actor):  # noqa: SLF001
            return

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
                response += (
                    " Could not fully contain actor: " + ", ".join(blocked) + "."
                )

        await anti_nuke._post_incident(  # noqa: SLF001
            guild,
            title="🚨 AntiNuke Dangerous Role Created",
            actor=actor,
            action_label="Dangerous role created",
            target_label=_target_label("role_create", entry),
            response_label=response,
            details="Gateway audit fast path; REST audit lookup was not required.",
        )
        return

    # Harmless-looking role creation is still part of the canonical delegated
    # long-horizon budget. Do not consume-and-drop it just because its base
    # permissions look benign.
    await anti_nuke._process_claimed_destructive_event(  # noqa: SLF001
        guild,
        entry=entry,
        action_key="role_create",
        action_label="Role creation burst",
        target_label=_target_label("role_create", entry),
        threshold_key="antinuke_role_delete_threshold",
    )


async def _handle_bot_add(guild: discord.Guild, entry: Any) -> None:
    member = getattr(entry, "target", None)
    if member is None:
        return

    settings = await anti_nuke.get_antinuke_settings(int(guild.id))
    if not settings["antinuke_enabled"]:
        return

    actor = getattr(entry, "user", None)
    if anti_nuke._actor_is_owner_or_bot(guild, actor):  # noqa: SLF001
        return

    response = "Alert-only mode: newly added bot was left in the server."
    if settings["antinuke_mode"] == "contain":
        removed_bot = False
        try:
            await guild.kick(
                member,
                reason="Dank Shield AntiNuke rollback: untrusted bot addition",
            )
            removed_bot = True
        except Exception:
            removed_bot = False

        removed, blocked = await _contain_actor(
            guild,
            actor,
            reason="Dank Shield AntiNuke containment: untrusted bot addition",
        )
        response = (
            "Removed the newly added bot."
            if removed_bot
            else "Could not remove the newly added bot."
        )
        if removed:
            response += " Containment actions: " + ", ".join(removed) + "."
        if blocked:
            response += " Could not fully contain inviter: " + ", ".join(blocked) + "."

    await anti_nuke._post_incident(  # noqa: SLF001
        guild,
        title="🚨 AntiNuke Untrusted Bot Added",
        actor=actor,
        action_label="Bot added to server",
        target_label=_target_label("bot_add", entry),
        response_label=response,
        details="Gateway audit fast path; REST audit lookup was not required.",
    )


async def _on_audit_log_entry_create(entry: discord.AuditLogEntry) -> None:
    guild = getattr(entry, "guild", None)
    if guild is None:
        return

    action_name = _action_name(entry)
    is_direct = action_name in _DIRECT_DESTRUCTIVE_ACTIONS
    is_role_create = action_name == "role_create"
    is_bot_add = action_name == "bot_add"
    if not (is_direct or is_role_create or is_bot_add):
        return

    # The canonical Discord event listeners use the same seen-entry registry, so
    # a later REST fallback cannot double-enforce the gateway event.
    if anti_nuke._consume_audit_entry(entry):  # noqa: SLF001
        return

    if is_role_create:
        await _handle_role_create(guild, entry)
        return
    if is_bot_add:
        await _handle_bot_add(guild, entry)
        return

    action_label, threshold_key, threshold_override = _DIRECT_DESTRUCTIVE_ACTIONS[
        action_name
    ]
    await anti_nuke._process_claimed_destructive_event(  # noqa: SLF001
        guild,
        entry=entry,
        action_key=action_name,
        action_label=action_label,
        target_label=_target_label(action_name, entry),
        threshold_key=threshold_key,
        threshold_override=threshold_override,
    )


def install_anti_nuke_gateway_runtime(bot: discord.Client) -> bool:
    """Install the audit gateway fast path once on the shared production bot."""

    if bool(getattr(bot, _INSTALL_FLAG, False)):
        return False

    bot.add_listener(_on_audit_log_entry_create, "on_audit_log_entry_create")
    setattr(bot, _INSTALL_FLAG, True)

    moderation_intent = bool(
        getattr(getattr(bot, "intents", None), "moderation", False)
    )
    if not moderation_intent:
        print(
            "⚠️ AntiNuke gateway runtime installed without moderation intent; "
            "REST audit fallback remains active but gateway audit events may not arrive."
        )
    else:
        print(
            "🛡️ AntiNuke gateway runtime active; destructive audit events use the "
            "gateway fast path with REST correlation retained as fallback."
        )
    return True


__all__ = ["install_anti_nuke_gateway_runtime"]
