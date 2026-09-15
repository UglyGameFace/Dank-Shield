from __future__ import annotations

"""Close the post-admission race for already-known hostile identities.

Discord does not offer bots a pre-join authorization callback. This runtime therefore
uses the already-prewarmed/local hostile reputation state as the first decision source
for re-entry containment and removes integrations correlated with a known-hostile
re-add without waiting on a network reputation refresh.
"""

import asyncio
import time
from collections.abc import Mapping
from typing import Any, Optional

import discord

from . import anti_nuke
from . import anti_nuke_guardian_runtime as guardian
from . import anti_nuke_hostile_actor_runtime as hostile

_INSTALL_FLAG = "_dank_antinuke_reentry_race_runtime_installed"
_GUARDIAN_FLAG = "_dank_antinuke_reentry_race_guardian_patched"
_WINDOW_SECONDS = 15.0
_RECENT_HOSTILE_READD: dict[tuple[int, int], float] = {}
_PENDING_INTEGRATIONS: dict[tuple[int, int], list[tuple[float, Any]]] = {}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


def _key(guild_id: Any, actor_id: Any) -> tuple[int, int]:
    return (_safe_int(guild_id, 0), _safe_int(actor_id, 0))


def _prune() -> None:
    now = time.monotonic()
    for key, until in list(_RECENT_HOSTILE_READD.items()):
        if float(until) <= now:
            _RECENT_HOSTILE_READD.pop(key, None)
    for key, rows in list(_PENDING_INTEGRATIONS.items()):
        fresh = [row for row in rows if now - float(row[0]) <= _WINDOW_SECONDS]
        if fresh:
            _PENDING_INTEGRATIONS[key] = fresh
        else:
            _PENDING_INTEGRATIONS.pop(key, None)


def _mark_hostile_readd(guild_id: int, actor_id: int) -> None:
    key = _key(guild_id, actor_id)
    if min(key) <= 0:
        return
    _prune()
    _RECENT_HOSTILE_READD[key] = time.monotonic() + _WINDOW_SECONDS


def _recent_hostile_readd(guild_id: int, actor_id: int) -> bool:
    _prune()
    return float(_RECENT_HOSTILE_READD.get(_key(guild_id, actor_id), 0.0)) > time.monotonic()


def _record_pending_integration(guild_id: int, actor_id: int, target: Any) -> None:
    key = _key(guild_id, actor_id)
    if min(key) <= 0 or target is None:
        return
    _prune()
    _PENDING_INTEGRATIONS.setdefault(key, []).append((time.monotonic(), target))


async def _fast_reputation(guild_id: int, user_id: int) -> Optional[dict[str, Any]]:
    """Return hot/local reputation without waiting for Supabase.

    Explicit clears update both the in-process cache and local mirror, so using those
    sources first removes the network round-trip from the destructive re-entry path.
    A cache miss deliberately returns None so the existing canonical path can perform
    its authoritative lookup for first-seen identities.
    """

    key = _key(guild_id, user_id)
    if min(key) <= 0:
        return None

    cached = hostile._MEMORY.get(key)  # noqa: SLF001
    if isinstance(cached, Mapping):
        return dict(cached)

    try:
        local = await asyncio.to_thread(
            hostile._read_local_record,  # noqa: SLF001
            int(guild_id),
            int(user_id),
        )
    except Exception:
        local = None
    if isinstance(local, Mapping):
        row = dict(local)
        hostile._MEMORY[key] = row  # noqa: SLF001
        return dict(row)
    return None


def _integration_identity_ids(integration: Any) -> set[int]:
    found: set[int] = set()
    candidates = [
        getattr(integration, "user", None),
        getattr(integration, "application", None),
    ]
    application = getattr(integration, "application", None)
    candidates.append(getattr(application, "bot", None))
    for candidate in candidates:
        value = _safe_int(getattr(candidate, "id", 0), 0)
        if value > 0:
            found.add(value)
    return found


async def _delete_integration(guild: Any, integration: Any, *, reason: str) -> str:
    target = integration
    target_id = _safe_int(getattr(target, "id", 0), 0)

    deleter = getattr(target, "delete", None)
    if callable(deleter):
        try:
            await deleter(reason=reason)
            return "deleted correlated integration"
        except Exception as exc:
            direct_error = type(exc).__name__
    else:
        direct_error = "target_not_deletable"

    fetcher = getattr(guild, "integrations", None)
    if callable(fetcher) and target_id > 0:
        try:
            integrations = list(await fetcher())
        except Exception as exc:
            return f"integration rollback failed: {direct_error}; fetch={type(exc).__name__}"
        for current in integrations:
            if _safe_int(getattr(current, "id", 0), 0) != target_id:
                continue
            delete_current = getattr(current, "delete", None)
            if not callable(delete_current):
                break
            try:
                await delete_current(reason=reason)
                return "deleted correlated integration"
            except Exception as exc:
                return f"integration rollback failed: {type(exc).__name__}"

    return f"integration rollback unavailable: {direct_error}"


async def _purge_pending_integrations(guild: Any, actor_id: int) -> list[str]:
    key = _key(getattr(guild, "id", 0), actor_id)
    _prune()
    rows = list(_PENDING_INTEGRATIONS.pop(key, []))
    results: list[str] = []
    seen: set[int] = set()
    for _created_at, target in rows:
        target_id = _safe_int(getattr(target, "id", 0), 0)
        if target_id > 0 and target_id in seen:
            continue
        if target_id > 0:
            seen.add(target_id)
        results.append(
            await _delete_integration(
                guild,
                target,
                reason="Dank Shield AntiNuke: integration correlated with known-hostile re-add",
            )
        )
    return results


async def _purge_matching_integrations(guild: Any, hostile_user_id: int) -> list[str]:
    fetcher = getattr(guild, "integrations", None)
    if not callable(fetcher):
        return []
    try:
        integrations = list(await fetcher())
    except Exception as exc:
        return [f"integration scan failed: {type(exc).__name__}"]

    results: list[str] = []
    for integration in integrations:
        if int(hostile_user_id) not in _integration_identity_ids(integration):
            continue
        results.append(
            await _delete_integration(
                guild,
                integration,
                reason="Dank Shield AntiNuke: integration belongs to known-hostile identity",
            )
        )
    return results


async def _integration_create_guard(guild: Any, entry: Any, actor: Any) -> str:
    settings = await anti_nuke.get_antinuke_settings(int(guild.id))
    if not bool(settings.get("antinuke_enabled")) or str(
        settings.get("antinuke_mode") or ""
    ).lower() != "contain":
        return ""

    actor_id = _safe_int(getattr(actor, "id", 0), 0)
    target = getattr(entry, "target", None)
    if actor_id <= 0 or target is None:
        return ""

    strict = bool(settings.get("antinuke_strict_lockdown", False))
    owner_or_bot = anti_nuke._actor_is_owner_or_bot(guild, actor)  # noqa: SLF001
    trusted = False
    if not owner_or_bot:
        trusted = anti_nuke._actor_is_configured_trusted(actor, settings)  # noqa: SLF001

    correlated = _recent_hostile_readd(int(guild.id), actor_id)
    should_delete = correlated or (not owner_or_bot and (strict or not trusted))
    if should_delete:
        return await _delete_integration(
            guild,
            target,
            reason=(
                "Dank Shield AntiNuke: integration created during known-hostile re-entry"
                if correlated
                else "Dank Shield AntiNuke: unauthorized integration creation"
            ),
        )

    # Discord can emit integration_create before bot_add for the same OAuth install.
    # Keep the object briefly so a later exact-ID hostile bot_add can remove it even
    # when the installer is the physical owner and cannot itself be contained.
    _record_pending_integration(int(guild.id), actor_id, target)
    return "integration held for hostile-bot correlation"


async def _fast_member_join(member: Any) -> None:
    guild = getattr(member, "guild", None)
    if guild is None:
        return
    uid = _safe_int(getattr(member, "id", 0), 0)
    if uid <= 0 or hostile._is_owner(guild, uid) or hostile._is_dank_bot(anti_nuke, uid):  # noqa: SLF001
        return

    reputation = await _fast_reputation(int(guild.id), uid)
    if not reputation or not reputation.get("active"):
        return

    settings = await anti_nuke.get_antinuke_settings(int(guild.id))
    if not bool(settings.get("antinuke_enabled")) or str(
        settings.get("antinuke_mode") or ""
    ).lower() != "contain":
        return

    classification = str(reputation.get("classification") or "confirmed_destructive_actor")
    banned, response = await hostile._ban_identity(  # noqa: SLF001
        guild,
        uid,
        member=member,
        reason=f"Dank Shield fast re-entry block ({classification})",
    )
    if not banned:
        contain = hostile._canonical_contain(anti_nuke)  # noqa: SLF001
        try:
            removed, blocked = await contain(
                guild,
                member,
                reason="Dank Shield fast known-hostile re-entry containment",
            )
        except Exception as exc:
            removed, blocked = [], [f"fallback failed: {type(exc).__name__}"]
        if removed:
            response += " • fallback: " + ", ".join(removed)
        if blocked:
            response += " • blockers: " + ", ".join(blocked)

    await hostile._post_reputation_incident(  # noqa: SLF001
        anti_nuke,
        guild,
        title="🛑 Known Hostile Re-entry Fast Block",
        actor=member,
        action_label="Known hostile identity joined",
        target_label=f"{member} (`{uid}`)",
        response_label=response,
        details="Used prewarmed/local hostile reputation before any network refresh.",
    )


def _patch_guardian() -> bool:
    if bool(getattr(guardian, _GUARDIAN_FLAG, False)):
        return False

    original_bot_add = guardian._handle_bot_add  # noqa: SLF001
    original_process = guardian._process  # noqa: SLF001

    async def fast_bot_add(guild: Any, entry: Any, actor: Any) -> None:
        target = getattr(entry, "target", None)
        target_id = hostile._actor_id(target)  # noqa: SLF001
        if target_id <= 0:
            return await original_bot_add(guild, entry, actor)

        reputation = await _fast_reputation(int(guild.id), target_id)
        if not reputation or not reputation.get("active"):
            return await original_bot_add(guild, entry, actor)

        settings = await anti_nuke.get_antinuke_settings(int(guild.id))
        if not bool(settings.get("antinuke_enabled")) or str(
            settings.get("antinuke_mode") or ""
        ).lower() != "contain":
            return await original_bot_add(guild, entry, actor)

        actor_id = hostile._actor_id(actor)  # noqa: SLF001
        if actor_id > 0:
            _mark_hostile_readd(int(guild.id), actor_id)

        pending_task = asyncio.create_task(
            _purge_pending_integrations(guild, actor_id),
            name=f"dank-hostile-pending-integration-{guild.id}-{actor_id}",
        )
        matching_task = asyncio.create_task(
            _purge_matching_integrations(guild, target_id),
            name=f"dank-hostile-integration-scan-{guild.id}-{target_id}",
        )

        banned, result = await hostile._ban_identity(  # noqa: SLF001
            guild,
            target_id,
            member=target,
            reason="Dank Shield AntiNuke: fast known-hostile bot re-add block",
        )
        response_parts = [result]
        if not banned and target is not None:
            contain = hostile._canonical_contain(anti_nuke)  # noqa: SLF001
            try:
                removed_target, blocked_target = await contain(
                    guild,
                    target,
                    reason="Dank Shield AntiNuke: fast known-hostile bot fallback",
                )
            except Exception as exc:
                removed_target, blocked_target = [], [
                    f"target fallback failed: {type(exc).__name__}"
                ]
            if removed_target:
                response_parts.append("target fallback: " + ", ".join(removed_target))
            if blocked_target:
                response_parts.append("target blockers: " + ", ".join(blocked_target))

        if (
            actor_id > 0
            and not hostile._is_owner(guild, actor_id)  # noqa: SLF001
            and not hostile._is_dank_bot(anti_nuke, actor_id)  # noqa: SLF001
        ):
            removed_actor, blocked_actor = await anti_nuke._contain_actor(  # noqa: SLF001
                guild,
                actor,
                reason="Dank Shield AntiNuke: re-added known-hostile bot",
            )
            if removed_actor:
                response_parts.append("inviter: " + ", ".join(removed_actor))
            if blocked_actor:
                response_parts.append("inviter blockers: " + ", ".join(blocked_actor))
        elif hostile._is_owner(guild, actor_id):  # noqa: SLF001
            response_parts.append("physical owner cannot be contained; hostile target/integration blocked instead")

        pending_results, matching_results = await asyncio.gather(
            pending_task,
            matching_task,
        )
        rollback_results = [
            value for value in [*pending_results, *matching_results] if value
        ]
        if rollback_results:
            response_parts.append("integration cleanup: " + " | ".join(rollback_results[:4]))

        await hostile._post_reputation_incident(  # noqa: SLF001
            anti_nuke,
            guild,
            title="🛑 Known Hostile Bot Re-add Fast Block",
            actor=actor,
            action_label="Previously confirmed hostile bot added again",
            target_label=f"{target} (`{target_id}`)",
            response_label=" • ".join(response_parts),
            details=(
                "Used hot/local hostile reputation before any network refresh and "
                "correlated the surrounding OAuth integration install."
            ),
        )

    async def process(
        guild: Any,
        entry: Any,
        actor: Any,
        action_name: str,
        spec: tuple[str, str, str, Optional[int]],
    ) -> None:
        if str(action_name) == "integration_create":
            await _integration_create_guard(guild, entry, actor)
        await original_process(guild, entry, actor, action_name, spec)

    guardian._handle_bot_add = fast_bot_add  # noqa: SLF001
    guardian._process = process  # noqa: SLF001
    setattr(guardian, _GUARDIAN_FLAG, True)
    return True


def install_anti_nuke_reentry_race_runtime(bot: discord.Client) -> bool:
    if bool(getattr(bot, _INSTALL_FLAG, False)):
        return False
    patched = _patch_guardian()
    bot.add_listener(_fast_member_join, "on_member_join")
    setattr(bot, _INSTALL_FLAG, True)
    print(
        "🛡️ AntiNuke hostile re-entry race guard active: "
        "hot reputation path, immediate known-hostile ban, integration correlation/rollback; "
        f"guardian={'patched' if patched else 'ready'}"
    )
    return True


__all__ = [
    "install_anti_nuke_reentry_race_runtime",
]
