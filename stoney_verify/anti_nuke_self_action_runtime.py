from __future__ import annotations

"""Verify that sensitive audit actions attributed to Dank Shield originated here.

The Discord audit log cannot distinguish a legitimate request from this running
process from a request made elsewhere using the same bot identity. This runtime
adds a short-lived, one-time nonce to locally issued audit-sensitive mutations.
A protected audit event attributed to Dank Shield is trusted only when that nonce
is still pending and its action/guild/target evidence matches.
"""

import re
import secrets
import time
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional
from urllib.parse import unquote, urlparse

import discord

from . import anti_nuke

_INSTALL_FLAG = "_dank_antinuke_self_action_runtime_installed"
_HTTP_PATCH_FLAG = "_dank_antinuke_self_action_http_patched"
_WEBHOOK_PATCH_FLAG = "_dank_antinuke_self_action_webhook_patched"
_AUTH_TTL_SECONDS = 120.0
_MAX_PENDING = 4096
_REASON_BASE_LIMIT = 380
_MARKER_RE = re.compile(r"\[DSA:([0-9a-f]{24})\]", re.IGNORECASE)
_API_PREFIX_RE = re.compile(r"^/api/v\d+")

_PROTECTED_ACTIONS = frozenset(
    {
        "guild_update",
        "channel_create", "channel_update", "channel_delete",
        "overwrite_create", "overwrite_update", "overwrite_delete",
        "role_create", "role_update", "role_delete",
        "member_role_update", "member_update",
        "ban", "unban", "kick", "member_prune",
        "invite_create", "invite_delete",
        "webhook_create", "webhook_update", "webhook_delete",
        "emoji_create", "emoji_update", "emoji_delete",
        "sticker_create", "sticker_update", "sticker_delete",
        "integration_delete",
        "stage_instance_create", "stage_instance_update", "stage_instance_delete",
        "scheduled_event_create", "scheduled_event_update", "scheduled_event_delete",
        "thread_create", "thread_update", "thread_delete",
        "app_command_permission_update",
        "soundboard_sound_create", "soundboard_sound_update", "soundboard_sound_delete",
        "automod_rule_create", "automod_rule_update", "automod_rule_delete",
        "message_delete", "message_bulk_delete",
    }
)


@dataclass(frozen=True)
class _RequestSpec:
    actions: frozenset[str]
    guild_id: int = 0
    target_key: str = ""


@dataclass
class _Authorization:
    nonce: str
    actions: frozenset[str]
    guild_id: int
    target_key: str
    created_at: float


_PENDING: dict[str, _Authorization] = {}
_COMPROMISE_GUILDS: set[int] = set()


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
    return text.rsplit(".", 1)[-1] if "." in text else text


def _route_path(route: Any) -> str:
    try:
        raw = str(urlparse(str(getattr(route, "url", "") or "")).path or "")
    except Exception:
        raw = ""
    if raw:
        return _API_PREFIX_RE.sub("", raw)
    return str(getattr(route, "path", "") or "")


def _id_key(value: Any) -> str:
    item = _safe_int(value, 0)
    return f"id:{item}" if item > 0 else ""


def _code_key(value: Any) -> str:
    text = unquote(str(value or "")).strip()
    return f"code:{text}" if text else ""


def _guild_for_channel(bot: discord.Client, channel_id: int) -> int:
    cid = _safe_int(channel_id, 0)
    if cid <= 0:
        return 0
    getter = getattr(bot, "get_channel", None)
    if callable(getter):
        try:
            found = getter(cid)
        except Exception:
            found = None
        guild = getattr(found, "guild", None)
        gid = _safe_int(getattr(guild, "id", 0), 0)
        if gid > 0:
            return gid
    for guild in list(getattr(bot, "guilds", []) or []):
        for name in ("get_channel_or_thread", "get_channel", "get_thread"):
            resolver = getattr(guild, name, None)
            if not callable(resolver):
                continue
            try:
                if resolver(cid) is not None:
                    return _safe_int(getattr(guild, "id", 0), 0)
            except Exception:
                continue
    return 0


def _spec(actions: Iterable[str], guild_id: int = 0, target_key: str = "") -> _RequestSpec:
    return _RequestSpec(
        frozenset(str(item).strip().lower() for item in actions if str(item).strip()),
        max(0, _safe_int(guild_id, 0)),
        str(target_key or ""),
    )


def _request_spec(bot: discord.Client, route: Any, kwargs: Mapping[str, Any]) -> Optional[_RequestSpec]:
    method = str(getattr(route, "method", "") or "").strip().upper()
    path = _route_path(route)
    payload = kwargs.get("json")
    if not isinstance(payload, Mapping):
        payload = {}

    m = re.fullmatch(r"/guilds/(\d+)", path)
    if m and method == "PATCH":
        gid = int(m.group(1))
        return _spec(("guild_update",), gid, _id_key(gid))

    m = re.fullmatch(r"/guilds/(\d+)/channels", path)
    if m:
        gid = int(m.group(1))
        if method == "POST":
            return _spec(("channel_create",), gid)
        if method == "PATCH":
            return _spec(("channel_update",), gid)

    m = re.fullmatch(r"/channels/(\d+)", path)
    if m and method in {"PATCH", "DELETE"}:
        cid = int(m.group(1))
        gid = _guild_for_channel(bot, cid)
        actions = ("channel_update", "thread_update") if method == "PATCH" else ("channel_delete", "thread_delete")
        return _spec(actions, gid, _id_key(cid))

    m = re.fullmatch(r"/channels/(\d+)/permissions/(\d+)", path)
    if m and method in {"PUT", "DELETE"}:
        cid = int(m.group(1))
        gid = _guild_for_channel(bot, cid)
        actions = ("overwrite_delete",) if method == "DELETE" else ("overwrite_create", "overwrite_update")
        return _spec(actions, gid, _id_key(cid))

    m = re.fullmatch(r"/guilds/(\d+)/roles", path)
    if m:
        gid = int(m.group(1))
        if method == "POST":
            return _spec(("role_create",), gid)
        if method == "PATCH":
            return _spec(("role_update",), gid)

    m = re.fullmatch(r"/guilds/(\d+)/roles/(\d+)", path)
    if m and method in {"PATCH", "DELETE"}:
        gid, rid = int(m.group(1)), int(m.group(2))
        return _spec(("role_update" if method == "PATCH" else "role_delete",), gid, _id_key(rid))

    m = re.fullmatch(r"/guilds/(\d+)/members/(\d+)/roles/(\d+)", path)
    if m and method in {"PUT", "DELETE"}:
        return _spec(("member_role_update",), int(m.group(1)), _id_key(m.group(2)))

    m = re.fullmatch(r"/guilds/(\d+)/members/(\d+)", path)
    if m and method in {"PATCH", "DELETE"}:
        action = "member_update" if method == "PATCH" else "kick"
        return _spec((action,), int(m.group(1)), _id_key(m.group(2)))

    m = re.fullmatch(r"/guilds/(\d+)/bans/(\d+)", path)
    if m and method in {"PUT", "DELETE"}:
        action = "ban" if method == "PUT" else "unban"
        return _spec((action,), int(m.group(1)), _id_key(m.group(2)))

    m = re.fullmatch(r"/guilds/(\d+)/prune", path)
    if m and method == "POST":
        return _spec(("member_prune",), int(m.group(1)))

    m = re.fullmatch(r"/channels/(\d+)/invites", path)
    if m and method == "POST":
        cid = int(m.group(1))
        return _spec(("invite_create",), _guild_for_channel(bot, cid))

    m = re.fullmatch(r"/invites/([^/]+)", path)
    if m and method == "DELETE":
        return _spec(("invite_delete",), 0, _code_key(m.group(1)))

    m = re.fullmatch(r"/channels/(\d+)/webhooks", path)
    if m and method == "POST":
        cid = int(m.group(1))
        return _spec(("webhook_create",), _guild_for_channel(bot, cid))

    m = re.fullmatch(r"/guilds/(\d+)/integrations/(\d+)", path)
    if m and method == "DELETE":
        return _spec(("integration_delete",), int(m.group(1)), _id_key(m.group(2)))

    for resource, prefix in (("emojis", "emoji"), ("stickers", "sticker")):
        collection = re.fullmatch(rf"/guilds/(\d+)/{resource}", path)
        if collection and method == "POST":
            return _spec((f"{prefix}_create",), int(collection.group(1)))
        item = re.fullmatch(rf"/guilds/(\d+)/{resource}/(\d+)", path)
        if item and method in {"PATCH", "DELETE"}:
            action = f"{prefix}_update" if method == "PATCH" else f"{prefix}_delete"
            return _spec((action,), int(item.group(1)), _id_key(item.group(2)))

    m = re.fullmatch(r"/guilds/(\d+)/scheduled-events", path)
    if m and method == "POST":
        return _spec(("scheduled_event_create",), int(m.group(1)))
    m = re.fullmatch(r"/guilds/(\d+)/scheduled-events/(\d+)", path)
    if m and method in {"PATCH", "DELETE"}:
        action = "scheduled_event_update" if method == "PATCH" else "scheduled_event_delete"
        return _spec((action,), int(m.group(1)), _id_key(m.group(2)))

    m = re.fullmatch(r"/guilds/(\d+)/soundboard-sounds", path)
    if m and method == "POST":
        return _spec(("soundboard_sound_create",), int(m.group(1)))
    m = re.fullmatch(r"/guilds/(\d+)/soundboard-sounds/(\d+)", path)
    if m and method in {"PATCH", "DELETE"}:
        action = "soundboard_sound_update" if method == "PATCH" else "soundboard_sound_delete"
        return _spec((action,), int(m.group(1)), _id_key(m.group(2)))

    m = re.fullmatch(r"/guilds/(\d+)/auto-moderation/rules", path)
    if m and method == "POST":
        return _spec(("automod_rule_create",), int(m.group(1)))
    m = re.fullmatch(r"/guilds/(\d+)/auto-moderation/rules/(\d+)", path)
    if m and method in {"PATCH", "DELETE"}:
        action = "automod_rule_update" if method == "PATCH" else "automod_rule_delete"
        return _spec((action,), int(m.group(1)), _id_key(m.group(2)))

    m = re.fullmatch(r"/applications/(\d+)/guilds/(\d+)/commands/(\d+)/permissions", path)
    if m and method == "PUT":
        return _spec(("app_command_permission_update",), int(m.group(2)), _id_key(m.group(3)))

    m = re.fullmatch(r"/channels/(\d+)/messages/bulk-delete", path)
    if m and method == "POST":
        cid = int(m.group(1))
        return _spec(("message_bulk_delete",), _guild_for_channel(bot, cid))

    m = re.fullmatch(r"/channels/(\d+)/messages/(\d+)", path)
    if m and method == "DELETE":
        cid = int(m.group(1))
        return _spec(("message_delete",), _guild_for_channel(bot, cid))

    for pattern in (r"/channels/(\d+)/threads", r"/channels/(\d+)/messages/\d+/threads"):
        m = re.fullmatch(pattern, path)
        if m and method == "POST":
            cid = int(m.group(1))
            return _spec(("thread_create",), _guild_for_channel(bot, cid))

    if path == "/stage-instances" and method == "POST":
        cid = _safe_int(payload.get("channel_id"), 0)
        return _spec(("stage_instance_create",), _guild_for_channel(bot, cid), _id_key(cid))
    m = re.fullmatch(r"/stage-instances/(\d+)", path)
    if m and method in {"PATCH", "DELETE"}:
        cid = int(m.group(1))
        action = "stage_instance_update" if method == "PATCH" else "stage_instance_delete"
        return _spec((action,), _guild_for_channel(bot, cid), _id_key(cid))

    return None


def _prune_pending(now: Optional[float] = None) -> None:
    current = time.monotonic() if now is None else float(now)
    for nonce, auth in list(_PENDING.items()):
        if current - float(auth.created_at) > _AUTH_TTL_SECONDS:
            _PENDING.pop(nonce, None)
    if len(_PENDING) > _MAX_PENDING:
        ordered = sorted(_PENDING.values(), key=lambda item: item.created_at)
        for auth in ordered[: len(_PENDING) - _MAX_PENDING]:
            _PENDING.pop(auth.nonce, None)


def _authorize(spec: _RequestSpec, reason: Any) -> tuple[str, str]:
    _prune_pending()
    nonce = secrets.token_hex(12)
    base = _MARKER_RE.sub("", str(reason or "")).strip()[:_REASON_BASE_LIMIT].strip()
    marker = f"[DSA:{nonce}]"
    stamped = f"{base} {marker}".strip() if base else f"Dank Shield authorized action {marker}"
    _PENDING[nonce] = _Authorization(nonce, spec.actions, spec.guild_id, spec.target_key, time.monotonic())
    return nonce, stamped


def _cancel(nonce: str) -> None:
    _PENDING.pop(str(nonce or "").lower(), None)


def _entry_target_key(entry: Any, action_name: str) -> str:
    target = getattr(entry, "target", None)
    target_id = _safe_int(getattr(target, "id", 0), 0)
    if target_id > 0:
        return _id_key(target_id)
    code = str(getattr(target, "code", "") or "").strip()
    if code:
        return _code_key(code)
    if action_name in {"message_delete", "message_bulk_delete"}:
        extra = getattr(entry, "extra", None)
        channel = getattr(extra, "channel", None)
        channel_id = _safe_int(getattr(channel, "id", 0), 0) or _safe_int(getattr(extra, "channel_id", 0), 0)
        if channel_id > 0:
            return _id_key(channel_id)
    return ""


def _consume(guild: Any, entry: Any, action_name: str) -> bool:
    _prune_pending()
    reason = str(getattr(entry, "reason", "") or "")
    match = _MARKER_RE.search(reason)
    if not match:
        return False
    nonce = str(match.group(1)).lower()
    auth = _PENDING.get(nonce)
    if auth is None or action_name not in auth.actions:
        return False
    guild_id = _safe_int(getattr(guild, "id", 0), 0)
    if auth.guild_id > 0 and auth.guild_id != guild_id:
        return False
    if auth.target_key:
        target_key = _entry_target_key(entry, action_name)
        if not target_key or target_key != auth.target_key:
            return False
    _PENDING.pop(nonce, None)
    return True


def _bot_id(bot: discord.Client) -> int:
    return _safe_int(getattr(getattr(bot, "user", None), "id", 0), 0)


def _actor_id(entry: Any) -> int:
    actor = getattr(entry, "user", None)
    return _safe_int(getattr(actor, "id", 0), 0) or _safe_int(getattr(entry, "user_id", 0), 0)


async def _warn_owner(bot: discord.Client, guild: Any, action_name: str) -> None:
    owner_id = _safe_int(getattr(guild, "owner_id", 0), 0)
    if owner_id <= 0:
        return
    user = None
    getter = getattr(bot, "get_user", None)
    if callable(getter):
        try:
            user = getter(owner_id)
        except Exception:
            user = None
    if user is None:
        fetcher = getattr(bot, "fetch_user", None)
        if callable(fetcher):
            try:
                user = await fetcher(owner_id)
            except Exception:
                user = None
    sender = getattr(user, "send", None)
    if not callable(sender):
        return
    try:
        await sender(
            "🚨 Dank Shield detected a protected audit action attributed to its own bot identity "
            f"(`{action_name}`) that this running process did not authorize. I left the server "
            "immediately to remove that bot identity's permissions. Rotate the Discord bot "
            "credential before re-adding Dank Shield."
        )
    except Exception:
        pass


async def _unmatched_self_action(bot: discord.Client, guild: Any, entry: Any, action_name: str) -> None:
    guild_id = _safe_int(getattr(guild, "id", 0), 0)
    if guild_id <= 0:
        return
    try:
        settings = await anti_nuke.get_antinuke_settings(guild_id)
    except Exception as exc:
        print(f"🚨 AntiNuke self-action settings check failed guild={guild_id} action={action_name} error={type(exc).__name__}: {exc}")
        return
    if not bool(settings.get("antinuke_enabled")):
        return
    mode = str(settings.get("antinuke_mode") or "contain").strip().lower()
    print(f"🚨 AntiNuke unmatched self-attributed action guild={guild_id} action={action_name} mode={mode}")

    if mode != "contain":
        try:
            await anti_nuke._post_incident(  # noqa: SLF001
                guild,
                title="🚨 AntiNuke Unverified Dank Shield Action",
                actor=getattr(entry, "user", None),
                action_label=f"Unmatched self-attributed audit action: {action_name}",
                target_label=str(getattr(entry, "target", None) or "Unknown"),
                response_label="Alert-only mode: no self-ejection was performed.",
                details="The audit entry did not carry a valid one-time authorization from this running process.",
            )
        except Exception:
            pass
        return

    if guild_id in _COMPROMISE_GUILDS:
        return
    _COMPROMISE_GUILDS.add(guild_id)
    leave = getattr(guild, "leave", None)
    if not callable(leave):
        print(f"🚨 AntiNuke CRITICAL self-ejection unavailable guild={guild_id}")
        return
    try:
        await leave()
        print(f"🛡️ AntiNuke fail-closed self-ejection completed guild={guild_id} action={action_name}")
    except Exception as exc:
        print(f"🚨 AntiNuke CRITICAL self-ejection failed guild={guild_id} action={action_name} error={type(exc).__name__}: {exc}")
        return
    await _warn_owner(bot, guild, action_name)


async def _audit_guard(bot: discord.Client, entry: Any) -> None:
    guild = getattr(entry, "guild", None)
    if guild is None:
        return
    action_name = _action_name(entry)
    if action_name not in _PROTECTED_ACTIONS:
        return
    if _bot_id(bot) <= 0 or _actor_id(entry) != _bot_id(bot):
        return
    if _consume(guild, entry, action_name):
        return
    await _unmatched_self_action(bot, guild, entry, action_name)


def _patch_http(bot: discord.Client) -> bool:
    http = getattr(bot, "http", None)
    if http is None or bool(getattr(http, _HTTP_PATCH_FLAG, False)):
        return False
    original = getattr(http, "request", None)
    if not callable(original):
        return False

    async def guarded_request(route: Any, *args: Any, **kwargs: Any) -> Any:
        spec = _request_spec(bot, route, kwargs)
        if spec is None:
            return await original(route, *args, **kwargs)
        nonce, reason = _authorize(spec, kwargs.get("reason"))
        kwargs["reason"] = reason
        try:
            return await original(route, *args, **kwargs)
        except Exception:
            _cancel(nonce)
            raise

    setattr(http, "request", guarded_request)
    setattr(http, _HTTP_PATCH_FLAG, True)
    return True


def _patch_webhook_methods(bot: discord.Client) -> bool:
    cls = discord.Webhook
    if bool(getattr(cls, _WEBHOOK_PATCH_FLAG, False)):
        return False
    original_delete = cls.delete
    original_edit = cls.edit

    async def guarded_delete(self: Any, *args: Any, **kwargs: Any) -> Any:
        guild_id = _safe_int(getattr(self, "guild_id", 0), 0)
        spec = _spec(("webhook_delete",), guild_id, _id_key(getattr(self, "id", 0)))
        nonce, reason = _authorize(spec, kwargs.get("reason"))
        kwargs["reason"] = reason
        try:
            return await original_delete(self, *args, **kwargs)
        except Exception:
            _cancel(nonce)
            raise

    async def guarded_edit(self: Any, *args: Any, **kwargs: Any) -> Any:
        guild_id = _safe_int(getattr(self, "guild_id", 0), 0)
        spec = _spec(("webhook_update",), guild_id, _id_key(getattr(self, "id", 0)))
        nonce, reason = _authorize(spec, kwargs.get("reason"))
        kwargs["reason"] = reason
        try:
            return await original_edit(self, *args, **kwargs)
        except Exception:
            _cancel(nonce)
            raise

    cls.delete = guarded_delete
    cls.edit = guarded_edit
    setattr(cls, _WEBHOOK_PATCH_FLAG, True)
    return True


def install_anti_nuke_self_action_runtime(bot: discord.Client) -> bool:
    if bool(getattr(bot, _INSTALL_FLAG, False)):
        return False
    http_patched = _patch_http(bot)
    webhook_patched = _patch_webhook_methods(bot)

    async def audit_listener(entry: Any) -> None:
        await _audit_guard(bot, entry)

    bot.add_listener(audit_listener, "on_audit_log_entry_create")
    setattr(bot, _INSTALL_FLAG, True)
    print(
        "🧾 AntiNuke self-action proof active: protected bot-attributed audit actions require "
        f"one-time local authorization; http={'patched' if http_patched else 'unavailable'}; "
        f"webhook={'patched' if webhook_patched else 'already active'}"
    )
    return True


__all__ = ["install_anti_nuke_self_action_runtime"]
