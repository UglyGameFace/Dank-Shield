from __future__ import annotations

"""Distinguish local Dank Shield mutations from stolen-token activity.

Discord audit logs identify both this running process and an attacker using a stolen
bot token as the same bot account. The existing AntiNuke correctly trusts its own
identity for legitimate setup/repair work, so identity alone cannot protect that
root boundary.

This runtime adds one-time, short-lived provenance IDs to locally initiated
high-impact REST mutations. Audit events attributed to Dank Shield are trusted only
when a matching live provenance record exists. Missing, stale, mismatched, or
replayed provenance is treated as suspected bot-token compromise and is surfaced
through the existing AntiNuke incident path, with safe rollback where Discord still
exposes reversible state.
"""

import re
import secrets
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Optional

import discord

_INSTALL_FLAG = "_dank_antinuke_self_provenance_installed"
_HTTP_PATCH_FLAG = "_dank_antinuke_self_provenance_http_patched"
_LISTENER_PATCH_FLAG = "_dank_antinuke_self_provenance_listener_patched"
_DISPATCH_PATCH_FLAG = "_dank_antinuke_self_provenance_dispatch_patched"

_MARKER_RE = re.compile(r"\[DS-PROV:([A-Za-z0-9_-]{12,64})\]")
_PENDING_TTL_SECONDS = 45.0
_PENDING_LIMIT = 2048
_OWNER_DM_COOLDOWN_SECONDS = 60.0


@dataclass(frozen=True)
class _Expectation:
    actions: frozenset[str]
    guild_id: Optional[int] = None
    target_id: Optional[int] = None


@dataclass(frozen=True)
class _PendingMutation:
    issued_at: float
    method: str
    path: str
    expectation: _Expectation


_PENDING: dict[str, _PendingMutation] = {}
_OWNER_DM_AT: dict[int, float] = {}

# Surfaces where a self-attributed event without valid provenance is security
# significant. This intentionally extends beyond the current guardian map so newly
# observed Discord administrative events do not inherit a silent self exemption.
_PROTECTED_SELF_ACTIONS = frozenset(
    {
        "guild_update",
        "channel_create",
        "channel_update",
        "channel_delete",
        "overwrite_create",
        "overwrite_update",
        "overwrite_delete",
        "role_create",
        "role_update",
        "role_delete",
        "member_role_update",
        "member_update",
        "member_prune",
        "ban",
        "unban",
        "kick",
        "bot_add",
        "invite_create",
        "invite_update",
        "invite_delete",
        "webhook_create",
        "webhook_update",
        "webhook_delete",
        "emoji_create",
        "emoji_update",
        "emoji_delete",
        "sticker_create",
        "sticker_update",
        "sticker_delete",
        "integration_create",
        "integration_update",
        "integration_delete",
        "scheduled_event_create",
        "scheduled_event_update",
        "scheduled_event_delete",
        "thread_create",
        "thread_update",
        "thread_delete",
        "app_command_permission_update",
        "automod_rule_create",
        "automod_rule_update",
        "automod_rule_delete",
        "message_bulk_delete",
        "soundboard_sound_create",
        "soundboard_sound_update",
        "soundboard_sound_delete",
    }
)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


def _route_value(route: Any, name: str) -> Optional[int]:
    value = _safe_int(getattr(route, name, None), 0)
    return value if value > 0 else None


def _url_id(url: str, segment: str) -> Optional[int]:
    match = re.search(rf"/{re.escape(segment)}/(\d+)(?:/|$)", str(url or ""))
    if not match:
        return None
    value = _safe_int(match.group(1), 0)
    return value if value > 0 else None


def _request_expectation(route: Any) -> Optional[_Expectation]:
    """Map a discord.py administrative REST route to expected audit semantics."""

    method = str(getattr(route, "method", "") or "").upper()
    path = str(getattr(route, "path", "") or "")
    url = str(getattr(route, "url", "") or "")
    if method not in {"POST", "PUT", "PATCH", "DELETE"} or not path:
        return None

    guild_id = _route_value(route, "guild_id")
    channel_id = _route_value(route, "channel_id")
    webhook_id = _route_value(route, "webhook_id")

    actions: set[str] = set()
    target_id: Optional[int] = None

    if path == "/guilds/{guild_id}" and method == "PATCH":
        actions.add("guild_update")
        target_id = guild_id
    elif path == "/guilds/{guild_id}/channels" and method == "POST":
        actions.add("channel_create")
    elif path == "/channels/{channel_id}" and method == "PATCH":
        actions.update({"channel_update", "thread_update"})
        target_id = channel_id
    elif path == "/channels/{channel_id}" and method == "DELETE":
        actions.update({"channel_delete", "thread_delete"})
        target_id = channel_id
    elif path.startswith("/channels/{channel_id}/permissions/") and method in {"PUT", "DELETE"}:
        actions.update({"overwrite_create", "overwrite_update", "overwrite_delete"})
        target_id = channel_id
    elif path == "/channels/{channel_id}/webhooks" and method == "POST":
        actions.add("webhook_create")
        target_id = channel_id
    elif path == "/channels/{channel_id}/messages/bulk-delete" and method == "POST":
        actions.add("message_bulk_delete")
        target_id = channel_id
    elif path == "/guilds/{guild_id}/roles" and method == "POST":
        actions.add("role_create")
    elif path == "/guilds/{guild_id}/roles" and method == "PATCH":
        actions.add("role_update")
    elif path.startswith("/guilds/{guild_id}/roles/{role_id}"):
        target_id = _url_id(url, "roles")
        if method == "PATCH":
            actions.add("role_update")
        elif method == "DELETE":
            actions.add("role_delete")
    elif path.startswith("/guilds/{guild_id}/members/{user_id}/roles/{role_id}") and method in {"PUT", "DELETE"}:
        actions.add("member_role_update")
        target_id = _url_id(url, "members")
    elif path.startswith("/guilds/{guild_id}/members/{user_id}"):
        target_id = _url_id(url, "members")
        if method == "PATCH":
            actions.add("member_update")
        elif method == "DELETE":
            actions.add("kick")
    elif path.startswith("/guilds/{guild_id}/bans/{user_id}"):
        target_id = _url_id(url, "bans")
        if method == "PUT":
            actions.add("ban")
        elif method == "DELETE":
            actions.add("unban")
    elif path == "/guilds/{guild_id}/prune" and method == "POST":
        actions.add("member_prune")
    elif path.startswith("/webhooks/{webhook_id}") and "/messages/" not in path:
        target_id = webhook_id
        if method == "PATCH":
            actions.add("webhook_update")
        elif method == "DELETE":
            actions.add("webhook_delete")
    elif path.startswith("/invites/{invite_id}") and method == "DELETE":
        actions.add("invite_delete")
    elif path == "/guilds/{guild_id}/emojis" and method == "POST":
        actions.add("emoji_create")
    elif path.startswith("/guilds/{guild_id}/emojis/{emoji_id}"):
        target_id = _url_id(url, "emojis")
        if method == "PATCH":
            actions.add("emoji_update")
        elif method == "DELETE":
            actions.add("emoji_delete")
    elif path == "/guilds/{guild_id}/stickers" and method == "POST":
        actions.add("sticker_create")
    elif path.startswith("/guilds/{guild_id}/stickers/{sticker_id}"):
        target_id = _url_id(url, "stickers")
        if method == "PATCH":
            actions.add("sticker_update")
        elif method == "DELETE":
            actions.add("sticker_delete")
    elif path.startswith("/guilds/{guild_id}/integrations/{integration_id}"):
        target_id = _url_id(url, "integrations")
        if method == "PATCH":
            actions.add("integration_update")
        elif method == "DELETE":
            actions.add("integration_delete")
    elif path == "/guilds/{guild_id}/scheduled-events" and method == "POST":
        actions.add("scheduled_event_create")
    elif path.startswith("/guilds/{guild_id}/scheduled-events/{event_id}"):
        target_id = _url_id(url, "scheduled-events")
        if method == "PATCH":
            actions.add("scheduled_event_update")
        elif method == "DELETE":
            actions.add("scheduled_event_delete")
    elif path == "/guilds/{guild_id}/auto-moderation/rules" and method == "POST":
        actions.add("automod_rule_create")
    elif path.startswith("/guilds/{guild_id}/auto-moderation/rules/{rule_id}"):
        target_id = _url_id(url, "rules")
        if method == "PATCH":
            actions.add("automod_rule_update")
        elif method == "DELETE":
            actions.add("automod_rule_delete")
    elif "guilds/{guild_id}/commands/{command_id}/permissions" in path and method in {"PUT", "PATCH"}:
        actions.add("app_command_permission_update")
        target_id = _url_id(url, "commands")
    elif path == "/guilds/{guild_id}/soundboard-sounds" and method == "POST":
        actions.add("soundboard_sound_create")
    elif path.startswith("/guilds/{guild_id}/soundboard-sounds/{sound_id}"):
        target_id = _url_id(url, "soundboard-sounds")
        if method == "PATCH":
            actions.add("soundboard_sound_update")
        elif method == "DELETE":
            actions.add("soundboard_sound_delete")

    if not actions:
        return None
    return _Expectation(
        actions=frozenset(actions),
        guild_id=guild_id,
        target_id=target_id,
    )


def _prune_pending() -> None:
    now = time.monotonic()
    stale = [
        token
        for token, record in _PENDING.items()
        if now - record.issued_at > _PENDING_TTL_SECONDS
    ]
    for token in stale:
        _PENDING.pop(token, None)
    if len(_PENDING) <= _PENDING_LIMIT:
        return
    ordered = sorted(_PENDING.items(), key=lambda item: item[1].issued_at)
    for token, _record in ordered[: len(_PENDING) - _PENDING_LIMIT]:
        _PENDING.pop(token, None)


def _new_token() -> str:
    return secrets.token_urlsafe(12)


def _strip_markers(value: str) -> str:
    return _MARKER_RE.sub("", str(value or "")).strip()


def _decorate_reason(reason: Any, token: str) -> str:
    marker = f"[DS-PROV:{token}]"
    base = _strip_markers(str(reason or "").strip())
    if not base:
        base = "Dank Shield authorized administrative action"
    max_base = max(0, 510 - len(marker) - 1)
    return f"{base[:max_base].rstrip()} {marker}".strip()


def _marker_token(entry: Any) -> Optional[str]:
    reason = str(getattr(entry, "reason", "") or "")
    matches = list(_MARKER_RE.finditer(reason))
    if not matches:
        return None
    return str(matches[-1].group(1))


def _entry_target_id(entry: Any) -> Optional[int]:
    value = _safe_int(getattr(getattr(entry, "target", None), "id", 0), 0)
    return value if value > 0 else None


def _consume_provenance(
    guild: Any,
    entry: Any,
    action_name: str,
) -> tuple[bool, str]:
    """Consume one matching authorization. Every presented live token is one-shot."""

    _prune_pending()
    token = _marker_token(entry)
    if not token:
        return False, "missing provenance"

    record = _PENDING.pop(token, None)
    if record is None:
        return False, "unknown, expired, or replayed provenance"
    if time.monotonic() - record.issued_at > _PENDING_TTL_SECONDS:
        return False, "expired provenance"

    expected = record.expectation
    if str(action_name or "") not in expected.actions:
        return False, (
            "provenance action mismatch: expected "
            + "/".join(sorted(expected.actions))
            + f", observed {action_name or 'unknown'}"
        )

    guild_id = _safe_int(getattr(guild, "id", 0), 0)
    if expected.guild_id and guild_id and int(expected.guild_id) != guild_id:
        return False, "provenance guild mismatch"

    found_target = _entry_target_id(entry)
    if expected.target_id and found_target and int(expected.target_id) != found_target:
        return False, "provenance target mismatch"

    return True, "valid local provenance"


def _is_self_actor(bot: Any, actor: Any) -> bool:
    bot_user = getattr(bot, "user", None)
    actor_id = _safe_int(getattr(actor, "id", 0), 0)
    bot_id = _safe_int(getattr(bot_user, "id", 0), 0)
    return actor_id > 0 and bot_id > 0 and actor_id == bot_id


def _patch_http_request(bot: Any) -> bool:
    http = getattr(bot, "http", None)
    if http is None or bool(getattr(http, _HTTP_PATCH_FLAG, False)):
        return False
    original = getattr(http, "request", None)
    if not callable(original):
        return False

    async def provenance_request(route: Any, *args: Any, **kwargs: Any) -> Any:
        expectation = _request_expectation(route)
        if expectation is None:
            return await original(route, *args, **kwargs)

        _prune_pending()
        token = _new_token()
        method = str(getattr(route, "method", "") or "").upper()
        path = str(getattr(route, "path", "") or "")
        _PENDING[token] = _PendingMutation(
            issued_at=time.monotonic(),
            method=method,
            path=path,
            expectation=expectation,
        )
        call_kwargs = dict(kwargs)
        call_kwargs["reason"] = _decorate_reason(call_kwargs.get("reason"), token)
        try:
            return await original(route, *args, **call_kwargs)
        except BaseException:
            _PENDING.pop(token, None)
            raise

    try:
        setattr(http, "request", provenance_request)
        setattr(http, _HTTP_PATCH_FLAG, True)
    except Exception:
        return False
    return True


def _compromise_proxy() -> Any:
    return SimpleNamespace(id=-1, roles=[], mention="Dank Shield token session")


async def _resolve_member(guild: Any, entry: Any) -> Any:
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
    return None


async def _rollback_member_roles(guild: Any, entry: Any, gateway: Any) -> str:
    target = await _resolve_member(guild, entry)
    if target is None:
        return "member-role rollback unavailable: target member missing"
    added, removed = gateway._role_diff(entry)  # noqa: SLF001
    me = getattr(guild, "me", None)
    bot_top = getattr(me, "top_role", None)
    if bot_top is None:
        return "member-role rollback unavailable: Dank Shield hierarchy unresolved"

    def manageable(role: Any) -> bool:
        try:
            return (
                isinstance(role, discord.Role)
                and not bool(getattr(role, "managed", False))
                and bool(role < bot_top)
            )
        except Exception:
            return False

    to_remove = [role for role in added if manageable(role)]
    to_restore = [role for role in removed if manageable(role)]
    notes: list[str] = []
    try:
        if to_remove:
            await target.remove_roles(
                *to_remove,
                reason="Dank Shield AntiNuke rollback: suspected bot-token compromise",
            )
            notes.append(f"removed {len(to_remove)} unauthorized role grant(s)")
        if to_restore:
            await target.add_roles(
                *to_restore,
                reason="Dank Shield AntiNuke rollback: suspected bot-token compromise",
            )
            notes.append(f"restored {len(to_restore)} removed role(s)")
    except Exception as exc:
        return f"member-role rollback failed safely: {type(exc).__name__}"
    return "; ".join(notes) if notes else "member-role rollback had no manageable role changes"


async def _rollback_timeout(guild: Any, entry: Any) -> str:
    target = await _resolve_member(guild, entry)
    before = getattr(entry, "before", None)
    if target is None or before is None:
        return "timeout rollback unavailable: target or audit before-state missing"
    previous = None
    for name in ("timed_out_until", "communication_disabled_until"):
        if hasattr(before, name):
            previous = getattr(before, name)
            break
    editor = getattr(target, "edit", None)
    if not callable(editor):
        return "timeout rollback unavailable: member edit API missing"
    try:
        await editor(
            timed_out_until=previous,
            reason="Dank Shield AntiNuke rollback: suspected bot-token compromise",
        )
        return "restored member timeout state"
    except Exception as exc:
        return f"timeout rollback failed safely: {type(exc).__name__}"


async def _rollback_generic_created_target(entry: Any, action_name: str) -> str:
    if action_name not in {
        "role_create",
        "emoji_create",
        "sticker_create",
        "scheduled_event_create",
        "thread_create",
        "soundboard_sound_create",
    }:
        return ""
    target = getattr(entry, "target", None)
    deleter = getattr(target, "delete", None)
    if target is None or not callable(deleter):
        return f"{action_name} rollback unavailable: created target could not be deleted"
    try:
        await deleter(
            reason="Dank Shield AntiNuke rollback: suspected bot-token compromise"
        )
        return f"deleted unauthorized {action_name.replace('_create', '').replace('_', ' ')}"
    except Exception as exc:
        return f"{action_name} rollback failed safely: {type(exc).__name__}"


async def _rollback_compromise_action(
    guild: Any,
    entry: Any,
    action_name: str,
    *,
    guardian: Any,
    gateway: Any,
) -> str:
    proxy = _compromise_proxy()
    notes: list[str] = []

    try:
        if action_name in {"channel_create", "webhook_create"}:
            note = await guardian._rollback_untrusted_creation(  # noqa: SLF001
                guild, entry, proxy, action_name
            )
            if note:
                notes.append(note)
        elif action_name in {"overwrite_create", "overwrite_update", "overwrite_delete"}:
            note = await guardian._rollback_untrusted_overwrite(  # noqa: SLF001
                guild, entry, proxy, action_name
            )
            if note:
                notes.append(note)
        elif action_name in {"automod_rule_create", "automod_rule_update", "automod_rule_delete"}:
            note = await guardian._rollback_untrusted_automod(  # noqa: SLF001
                guild, entry, proxy, action_name
            )
            if note:
                notes.append(note)
        elif action_name == "member_role_update":
            notes.append(await _rollback_member_roles(guild, entry, gateway))
        elif action_name == "member_update":
            notes.append(await _rollback_timeout(guild, entry))
        elif action_name == "bot_add":
            target = getattr(entry, "target", None)
            if target is not None:
                await guild.kick(
                    target,
                    reason="Dank Shield AntiNuke rollback: suspected bot-token compromise",
                )
                notes.append("removed bot added by unprovenanced Dank Shield identity")
        elif action_name == "ban":
            target = getattr(entry, "target", None)
            if target is not None:
                await guild.unban(
                    target,
                    reason="Dank Shield AntiNuke rollback: suspected bot-token compromise",
                )
                notes.append("reversed unauthorized member ban")
        else:
            note = await _rollback_generic_created_target(entry, action_name)
            if note:
                notes.append(note)
    except Exception as exc:
        notes.append(f"rollback failed safely: {type(exc).__name__}")

    if not notes:
        return "No safe automatic rollback exists for this Discord action."
    return " | ".join(note for note in notes if note)


async def _warn_owner(guild: Any, action_name: str, reason: str) -> str:
    guild_id = _safe_int(getattr(guild, "id", 0), 0)
    now = time.monotonic()
    last = float(_OWNER_DM_AT.get(guild_id, 0.0) or 0.0)
    if guild_id > 0 and now - last < _OWNER_DM_COOLDOWN_SECONDS:
        return "owner warning already sent recently"

    owner = getattr(guild, "owner", None)
    if owner is None:
        owner_id = _safe_int(getattr(guild, "owner_id", 0), 0)
        getter = getattr(guild, "get_member", None)
        if callable(getter) and owner_id > 0:
            try:
                owner = getter(owner_id)
            except Exception:
                owner = None
        if owner is None and owner_id > 0:
            fetcher = getattr(guild, "fetch_member", None)
            if callable(fetcher):
                try:
                    owner = await fetcher(owner_id)
                except Exception:
                    owner = None

    sender = getattr(owner, "send", None)
    if not callable(sender):
        return "owner DM unavailable"
    try:
        await sender(
            "🚨 **Dank Shield AntiNuke: possible bot-token compromise**\n"
            f"Discord reported `{action_name}` as performed by Dank Shield, but this "
            "running process has no matching one-time authorization for it. "
            f"Reason: {reason}.\n\n"
            "Treat the bot token as compromised: rotate/reset the token in the Discord "
            "Developer Portal and redeploy Dank Shield. The bot cannot rotate its own token."
        )
        if guild_id > 0:
            _OWNER_DM_AT[guild_id] = now
        return "guild owner warned by DM"
    except Exception as exc:
        return f"owner DM failed: {type(exc).__name__}"


async def _handle_suspected_compromise(
    bot: Any,
    guild: Any,
    entry: Any,
    action_name: str,
    provenance_reason: str,
    *,
    anti_nuke: Any,
    guardian: Any,
    gateway: Any,
) -> None:
    try:
        anti_nuke._consume_audit_entry(entry)  # noqa: SLF001
    except Exception:
        pass

    rollback = await _rollback_compromise_action(
        guild,
        entry,
        action_name,
        guardian=guardian,
        gateway=gateway,
    )
    owner_warning = await _warn_owner(guild, action_name, provenance_reason)
    target = getattr(entry, "target", None)
    target_id = _safe_int(getattr(target, "id", 0), 0)
    target_label = (
        f"{target} (`{target_id}`)" if target_id > 0 else str(target or "Unknown")
    )

    print(
        "🚨 CRITICAL AntiNuke suspected bot-token compromise "
        f"guild={getattr(guild, 'id', 'unknown')} action={action_name} "
        f"target={target_id or 'unknown'} provenance={provenance_reason}"
    )

    await anti_nuke._post_incident(  # noqa: SLF001
        guild,
        title="🚨 AntiNuke Bot-Token Compromise Suspected",
        actor=getattr(entry, "user", None) or getattr(bot, "user", None),
        action_label=f"Unprovenanced self-attributed action: {action_name}",
        target_label=target_label,
        response_label=f"{rollback} {owner_warning}.",
        count_label="first strike • Dank Shield identity trust boundary",
        details=(
            "Discord attributed this administrative action to Dank Shield, but the "
            "running process did not issue a matching live one-time provenance ID. "
            "This can indicate use of a stolen bot token. Rotate the Discord bot token "
            "immediately. A bot cannot revoke or rotate its own token."
        ),
    )


async def _evaluate_self_entry(
    bot: Any,
    guild: Any,
    entry: Any,
    action_name: str,
    *,
    anti_nuke: Any,
    guardian: Any,
    gateway: Any,
) -> bool:
    actor = getattr(entry, "user", None)
    if not _is_self_actor(bot, actor):
        return False

    settings = await anti_nuke.get_antinuke_settings(int(guild.id))
    if not bool(settings.get("antinuke_enabled")):
        return False

    valid, reason = _consume_provenance(guild, entry, action_name)
    if valid:
        try:
            anti_nuke._consume_audit_entry(entry)  # noqa: SLF001
        except Exception:
            pass
        return True

    if action_name not in _PROTECTED_SELF_ACTIONS:
        return False

    await _handle_suspected_compromise(
        bot,
        guild,
        entry,
        action_name,
        reason,
        anti_nuke=anti_nuke,
        guardian=guardian,
        gateway=gateway,
    )
    return True


def _patch_sparse_dispatch(bot: Any, anti_nuke: Any, guardian: Any, gateway: Any, incident: Any) -> bool:
    if bool(getattr(incident, _DISPATCH_PATCH_FLAG, False)):
        return False
    original = incident._dispatch_recovered  # noqa: SLF001

    async def provenance_dispatch(guild: Any, entry: Any, actor: Any, action_name: str) -> None:
        if _is_self_actor(bot, actor):
            proxy_entry = guardian._EntryProxy(entry, actor)  # noqa: SLF001
            handled = await _evaluate_self_entry(
                bot,
                guild,
                proxy_entry,
                action_name,
                anti_nuke=anti_nuke,
                guardian=guardian,
                gateway=gateway,
            )
            if handled:
                return
        await original(guild, entry, actor, action_name)

    incident._dispatch_recovered = provenance_dispatch  # noqa: SLF001
    setattr(incident, _DISPATCH_PATCH_FLAG, True)
    return True


def _patch_audit_listener(bot: Any, anti_nuke: Any, guardian: Any, gateway: Any, incident: Any) -> bool:
    if bool(getattr(bot, _LISTENER_PATCH_FLAG, False)):
        return False
    remover = getattr(bot, "remove_listener", None)
    adder = getattr(bot, "add_listener", None)
    if not callable(remover) or not callable(adder):
        return False

    original = incident._on_audit_log_entry_create  # noqa: SLF001

    async def provenance_listener(entry: Any) -> None:
        guild = getattr(entry, "guild", None)
        if guild is None:
            await original(entry)
            return
        actor = await guardian._resolve_actor(guild, entry)  # noqa: SLF001
        if actor is None:
            await original(entry)
            return
        if _is_self_actor(bot, actor):
            proxy_entry = guardian._EntryProxy(entry, actor)  # noqa: SLF001
            action_name = guardian._action_name(proxy_entry)  # noqa: SLF001
            handled = await _evaluate_self_entry(
                bot,
                guild,
                proxy_entry,
                action_name,
                anti_nuke=anti_nuke,
                guardian=guardian,
                gateway=gateway,
            )
            if handled:
                return
        await original(entry)

    try:
        remover(original, "on_audit_log_entry_create")
        adder(provenance_listener, "on_audit_log_entry_create")
    except Exception:
        return False
    setattr(bot, _LISTENER_PATCH_FLAG, True)
    return True


def install_anti_nuke_self_provenance_runtime(bot: discord.Client) -> bool:
    """Install one-time self-action provenance after all other AntiNuke layers."""

    if bool(getattr(bot, _INSTALL_FLAG, False)):
        return False

    from . import anti_nuke
    from . import anti_nuke_gateway_runtime as gateway
    from . import anti_nuke_guardian_runtime as guardian
    from . import anti_nuke_incident_runtime as incident

    http_patched = _patch_http_request(bot)
    dispatch_patched = _patch_sparse_dispatch(
        bot, anti_nuke, guardian, gateway, incident
    )
    listener_patched = _patch_audit_listener(
        bot, anti_nuke, guardian, gateway, incident
    )

    if not http_patched or not listener_patched:
        print(
            "🚨 AntiNuke self-provenance guard incomplete: "
            f"http={'patched' if http_patched else 'FAILED'} "
            f"listener={'patched' if listener_patched else 'FAILED'} "
            f"sparse_dispatch={'patched' if dispatch_patched else 'already active/FAILED'}"
        )
        return False

    setattr(bot, _INSTALL_FLAG, True)
    print(
        "🛡️ AntiNuke self-provenance guard active: local administrative mutations "
        "use one-time audit provenance; unprovenanced Dank Shield actions are treated "
        "as suspected bot-token compromise"
    )
    return True


__all__ = ["install_anti_nuke_self_provenance_runtime"]
