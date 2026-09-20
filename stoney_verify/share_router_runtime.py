from __future__ import annotations

"""Production Share Router runtime.

The router gives a guild private, non-age-restricted proxy channels that remain
usable from mobile share sheets, then forwards the human's post into a configured
destination the same human is normally allowed to view and post in.

This module owns runtime behavior only. It has no import-time bot mutation and
adds no slash commands. Public configuration UI lives in
commands_ext.public_share_router.
"""

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

import discord

from stoney_verify.share_router_resources import (
    DEFAULT_SHARE_CHANNELS,
    SHARE_ROUTER_CATEGORY_NAME,
    is_share_router_category_name,
    is_share_router_design_resource,
    share_source_key,
)

_DATA_LOCK = asyncio.Lock()
_RECENT_ROUTE_KEYS: dict[tuple[int, int, str], float] = {}
URL_RE = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)

# Keep the historical path so a deployed guild does not have to rebuild routes
# merely because ownership moved out of startup_guards.
ROUTES_FILE = Path(
    os.getenv(
        "DANK_SHARE_ROUTES_FILE",
        str(Path(os.getenv("DANK_DATA_DIR", "data")) / "share_routes.json"),
    )
)


@dataclass(frozen=True)
class RouteHealth:
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.blockers


@dataclass(frozen=True)
class HubRepairResult:
    category_id: int
    created: tuple[str, ...] = ()
    repaired: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def _log(message: str) -> None:
    try:
        print(f"🔗 share_router {message}")
    except Exception:
        pass


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip().strip("<#@!&>")
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _utc_iso() -> str:
    try:
        return discord.utils.utcnow().isoformat()
    except Exception:
        return ""


def _load_all_unlocked() -> dict[str, Any]:
    try:
        if not ROUTES_FILE.exists():
            return {}
        data = json.loads(ROUTES_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_all_unlocked(data: Mapping[str, Any]) -> None:
    ROUTES_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = ROUTES_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(dict(data), indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(ROUTES_FILE)


async def guild_routes(guild_id: int) -> list[dict[str, Any]]:
    async with _DATA_LOCK:
        data = _load_all_unlocked()
        bucket = data.get(str(int(guild_id))) or {}
        routes = bucket.get("routes") if isinstance(bucket, dict) else []
        if not isinstance(routes, list):
            return []

        clean: list[dict[str, Any]] = []
        for raw in routes:
            if not isinstance(raw, dict):
                continue
            source_id = _safe_int(raw.get("source_channel_id"), 0)
            target_id = _safe_int(raw.get("target_channel_id"), 0)
            if source_id <= 0 or target_id <= 0:
                continue
            item = dict(raw)
            item["source_channel_id"] = str(source_id)
            item["target_channel_id"] = str(target_id)
            item["enabled"] = bool(item.get("enabled", True))
            item["delete_source"] = bool(item.get("delete_source", True))
            clean.append(item)
        return clean


async def save_route(
    guild_id: int,
    *,
    source_channel_id: int,
    target_channel_id: int,
    delete_source: bool = True,
    created_by_id: int = 0,
) -> None:
    async with _DATA_LOCK:
        data = _load_all_unlocked()
        key = str(int(guild_id))
        bucket = data.get(key)
        if not isinstance(bucket, dict):
            bucket = {}
        routes = bucket.get("routes")
        if not isinstance(routes, list):
            routes = []

        source_text = str(int(source_channel_id))
        next_routes = [
            dict(raw)
            for raw in routes
            if isinstance(raw, dict) and str(raw.get("source_channel_id")) != source_text
        ]
        now = _utc_iso()
        next_routes.append(
            {
                "source_channel_id": source_text,
                "target_channel_id": str(int(target_channel_id)),
                "enabled": True,
                "delete_source": bool(delete_source),
                "created_by_id": str(int(created_by_id or 0)),
                "created_at": now,
                "updated_at": now,
            }
        )
        bucket["routes"] = next_routes
        data[key] = bucket
        _save_all_unlocked(data)


async def remove_route(guild_id: int, source_channel_id: int) -> bool:
    async with _DATA_LOCK:
        data = _load_all_unlocked()
        key = str(int(guild_id))
        bucket = data.get(key)
        if not isinstance(bucket, dict):
            return False
        routes = bucket.get("routes")
        if not isinstance(routes, list):
            return False

        before = len(routes)
        source_text = str(int(source_channel_id))
        bucket["routes"] = [
            raw
            for raw in routes
            if not (isinstance(raw, dict) and str(raw.get("source_channel_id")) == source_text)
        ]
        data[key] = bucket
        _save_all_unlocked(data)
        return len(bucket["routes"]) != before


def route_for_source(routes: list[dict[str, Any]], source_channel_id: int) -> Optional[dict[str, Any]]:
    source_text = str(int(source_channel_id))
    for route in routes:
        if not route.get("enabled", True):
            continue
        if str(route.get("source_channel_id")) == source_text:
            return route
    return None


def source_privacy_blocker(source: discord.TextChannel) -> str:
    try:
        everyone_perms = source.permissions_for(source.guild.default_role)
        if everyone_perms.view_channel:
            return "@everyone can view the proxy source. Hide it and grant only the intended role(s) access."
    except Exception:
        return "Could not verify @everyone visibility for the proxy source."
    return ""


def source_age_blocker(source: discord.TextChannel) -> str:
    try:
        if bool(source.is_nsfw()):
            return "The proxy source itself is age-restricted. Keep the proxy non-age-restricted or it cannot solve the mobile share-sheet problem."
    except Exception:
        return "Could not verify whether the proxy source is age-restricted."
    return ""


def route_permission_blockers(
    source: discord.TextChannel,
    target: discord.TextChannel,
    *,
    delete_source: bool,
) -> list[str]:
    blockers: list[str] = []
    me = source.guild.me
    if not isinstance(me, discord.Member):
        return ["Dank Shield's server member could not be resolved."]

    source_perms = source.permissions_for(me)
    target_perms = target.permissions_for(me)

    if not source_perms.view_channel:
        blockers.append(f"Dank Shield cannot view source {source.mention}.")
    if not source_perms.read_message_history:
        blockers.append(f"Dank Shield cannot read source history in {source.mention}.")
    if bool(delete_source) and not source_perms.manage_messages:
        blockers.append(f"Dank Shield needs Manage Messages in {source.mention} to clean routed posts.")
    if not target_perms.view_channel:
        blockers.append(f"Dank Shield cannot view target {target.mention}.")
    if not target_perms.send_messages:
        blockers.append(f"Dank Shield cannot send messages in target {target.mention}.")
    return blockers


def route_health(guild: discord.Guild, route: Mapping[str, Any]) -> RouteHealth:
    blockers: list[str] = []
    warnings: list[str] = []
    source_id = _safe_int(route.get("source_channel_id"), 0)
    target_id = _safe_int(route.get("target_channel_id"), 0)
    source = guild.get_channel(source_id)
    target = guild.get_channel(target_id)

    if not isinstance(source, discord.TextChannel):
        blockers.append(f"Source channel {source_id or 'unknown'} is missing.")
    if not isinstance(target, discord.TextChannel):
        blockers.append(f"Target channel {target_id or 'unknown'} is missing.")
    if blockers:
        return RouteHealth(tuple(blockers), tuple(warnings))

    age_blocker = source_age_blocker(source)
    if age_blocker:
        blockers.append(age_blocker)
    privacy = source_privacy_blocker(source)
    if privacy:
        blockers.append(privacy)
    blockers.extend(
        route_permission_blockers(
            source,
            target,
            delete_source=bool(route.get("delete_source", True)),
        )
    )

    try:
        me = guild.me
        if isinstance(me, discord.Member) and not target.permissions_for(me).embed_links:
            warnings.append(f"Dank Shield lacks Embed Links in {target.mention}; URLs can route but previews may be reduced.")
    except Exception:
        warnings.append("Could not verify destination Embed Links permission.")

    if not is_share_router_design_resource(source):
        warnings.append("This is a legacy/custom proxy source outside the canonical Share Routes hub.")

    return RouteHealth(tuple(dict.fromkeys(blockers)), tuple(dict.fromkeys(warnings)))


def _message_share_text(message: discord.Message) -> str:
    parts: list[str] = []
    content = _safe_str(getattr(message, "content", ""))
    if content:
        parts.append(content)

    try:
        for embed in list(getattr(message, "embeds", []) or []):
            for attr in ("url", "title", "description"):
                text = _safe_str(getattr(embed, attr, None))
                if text and text not in parts:
                    parts.append(text)
    except Exception:
        pass

    try:
        for attachment in list(getattr(message, "attachments", []) or []):
            url = _safe_str(getattr(attachment, "url", ""))
            if url and url not in parts:
                parts.append(url)
    except Exception:
        pass

    return "\n".join(parts).strip()


def _dedupe_key(text: str) -> str:
    urls = URL_RE.findall(text or "")
    if urls:
        return urls[0].strip().lower().rstrip(".,)")
    return re.sub(r"\s+", " ", (text or "").strip().lower())[:180]


def _prune_recent(now: float) -> None:
    stale = [key for key, saved in _RECENT_ROUTE_KEYS.items() if now - float(saved or 0.0) > 3600.0]
    for key in stale:
        _RECENT_ROUTE_KEYS.pop(key, None)


async def _send_modlog(guild: discord.Guild, embed: discord.Embed) -> None:
    try:
        from stoney_verify import spam_guard

        sender = getattr(spam_guard, "_send_modlog_embed", None)
        if callable(sender):
            await sender(guild, embed)
    except Exception:
        pass


async def _route_rejection_log(
    message: discord.Message,
    *,
    target: Optional[discord.TextChannel],
    reason: str,
) -> None:
    try:
        embed = discord.Embed(
            title="⚠️ Share Router Blocked a Route",
            description=reason[:4000],
            color=discord.Color.orange(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Source", value=f"{message.channel.mention} ({message.channel.id})", inline=False)
        if target is not None:
            embed.add_field(name="Target", value=f"{target.mention} ({target.id})", inline=False)
        embed.add_field(name="Author", value=f"{message.author.mention} ({message.author.id})", inline=False)
        await _send_modlog(message.guild, embed)
    except Exception:
        pass


async def route_message(message: discord.Message) -> None:
    """Route one human message when its channel is a configured proxy source."""

    try:
        guild = message.guild
        if guild is None or not isinstance(message.channel, discord.TextChannel):
            return
        if getattr(message.author, "bot", False):
            return

        routes = await guild_routes(int(guild.id))
        route = route_for_source(routes, int(message.channel.id))
        if route is None:
            return

        age_blocker = source_age_blocker(message.channel)
        if age_blocker:
            await _route_rejection_log(message, target=None, reason=age_blocker)
            return
        privacy_blocker = source_privacy_blocker(message.channel)
        if privacy_blocker:
            await _route_rejection_log(message, target=None, reason=privacy_blocker)
            return

        target_id = _safe_int(route.get("target_channel_id"), 0)
        target = guild.get_channel(target_id)
        if not isinstance(target, discord.TextChannel):
            await _route_rejection_log(message, target=None, reason="The saved destination channel no longer exists.")
            return

        author = message.author
        if not isinstance(author, discord.Member):
            await _route_rejection_log(message, target=target, reason="The sender could not be resolved as a server member.")
            return

        # Never use the bot to bypass normal Discord channel permissions. This
        # does not attempt to infer account age; Discord still controls viewing
        # of age-restricted destinations on the client/account side.
        author_perms = target.permissions_for(author)
        if not author_perms.view_channel or not author_perms.send_messages:
            await _route_rejection_log(
                message,
                target=target,
                reason="The sender does not have normal View Channel + Send Messages permission in the destination.",
            )
            return

        me = guild.me
        if not isinstance(me, discord.Member):
            return
        target_perms = target.permissions_for(me)
        source_perms = message.channel.permissions_for(me)
        if not target_perms.view_channel or not target_perms.send_messages:
            await _route_rejection_log(
                message,
                target=target,
                reason="Dank Shield cannot view/send in the configured destination.",
            )
            return

        text = _message_share_text(message)
        if not text:
            return

        now = time.monotonic()
        _prune_recent(now)
        key_text = _dedupe_key(text)
        dedupe = (int(guild.id), int(target.id), key_text)
        duplicate = bool(key_text and dedupe in _RECENT_ROUTE_KEYS)
        if key_text:
            _RECENT_ROUTE_KEYS[dedupe] = now

        if not duplicate:
            routed = f"{text}\n\n↪️ Shared by {message.author.mention} via Dank Shield Share Router"
            await target.send(
                routed[:2000],
                allowed_mentions=discord.AllowedMentions.none(),
            )

        if bool(route.get("delete_source", True)) and source_perms.manage_messages:
            try:
                await message.delete(reason="Dank Shield Share Router: proxy message routed")
            except discord.NotFound:
                pass
            except Exception:
                pass

        embed = discord.Embed(
            title="🔗 Share Router Routed Message" if not duplicate else "🔁 Share Router Duplicate Cleaned",
            color=discord.Color.green() if not duplicate else discord.Color.orange(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Source", value=f"{message.channel.mention} ({message.channel.id})", inline=False)
        embed.add_field(name="Target", value=f"{target.mention} ({target.id})", inline=False)
        embed.add_field(name="Author", value=f"{message.author.mention} ({message.author.id})", inline=False)
        if duplicate:
            embed.add_field(name="Duplicate", value="Already routed recently, so it was not reposted.", inline=False)
        await _send_modlog(guild, embed)

    except Exception as exc:
        _log(f"route failed: {type(exc).__name__}: {exc}")


def _merged_overwrite(
    existing: Optional[discord.PermissionOverwrite],
    **updates: Optional[bool],
) -> discord.PermissionOverwrite:
    try:
        allow, deny = existing.pair() if existing is not None else (discord.Permissions.none(), discord.Permissions.none())
        merged = discord.PermissionOverwrite.from_pair(allow, deny)
    except Exception:
        merged = discord.PermissionOverwrite()

    for name, value in updates.items():
        try:
            setattr(merged, name, value)
        except Exception:
            pass
    return merged


def _permission_overwrite_map(
    guild: discord.Guild,
    user: discord.abc.User,
    *,
    current: Optional[Mapping[Any, discord.PermissionOverwrite]] = None,
) -> dict[Any, discord.PermissionOverwrite]:
    overwrites: dict[Any, discord.PermissionOverwrite] = dict(current or {})
    overwrites[guild.default_role] = _merged_overwrite(
        overwrites.get(guild.default_role),
        view_channel=False,
    )

    me = guild.me
    if isinstance(me, discord.Member):
        overwrites[me] = _merged_overwrite(
            overwrites.get(me),
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_messages=True,
            embed_links=True,
        )

    if isinstance(user, discord.Member):
        overwrites[user] = _merged_overwrite(
            overwrites.get(user),
            view_channel=True,
            send_messages=True,
            read_message_history=True,
        )
    return overwrites


async def _add_configured_staff_overwrites(
    guild: discord.Guild,
    overwrites: dict[Any, discord.PermissionOverwrite],
) -> dict[Any, discord.PermissionOverwrite]:
    try:
        from stoney_verify.guild_config import get_guild_config

        cfg = await get_guild_config(int(guild.id), refresh=False)
    except Exception:
        cfg = None

    role_ids: set[int] = set()
    for key in ("staff_role_id", "server_control_role_id", "control_role_id", "perm_role_id", "vc_staff_role_id"):
        try:
            raw = cfg.get(key) if hasattr(cfg, "get") else getattr(cfg, key, None)
            rid = _safe_int(raw, 0)
            if rid > 0:
                role_ids.add(rid)
        except Exception:
            pass

    for rid in role_ids:
        role = guild.get_role(rid)
        if role is not None and not role.is_default():
            overwrites[role] = _merged_overwrite(
                overwrites.get(role),
                view_channel=True,
                send_messages=True,
                read_message_history=True,
            )
    return overwrites


def find_share_router_categories(guild: discord.Guild) -> list[discord.CategoryChannel]:
    return [
        category
        for category in list(getattr(guild, "categories", []) or [])
        if isinstance(category, discord.CategoryChannel)
        and is_share_router_category_name(getattr(category, "name", ""))
    ]


def find_share_router_category(guild: discord.Guild) -> Optional[discord.CategoryChannel]:
    categories = find_share_router_categories(guild)
    if not categories:
        return None

    def score(category: discord.CategoryChannel) -> tuple[int, int]:
        canonical_children = sum(
            1
            for channel in list(getattr(category, "channels", []) or [])
            if isinstance(channel, discord.TextChannel)
            and share_source_key(getattr(channel, "name", "")) is not None
        )
        exact_name = 1 if str(getattr(category, "name", "")) == SHARE_ROUTER_CATEGORY_NAME else 0
        return canonical_children, exact_name

    # Prefer the hub that already owns the proxy children. This avoids choosing
    # an empty duplicate merely because its category name happens to be exact.
    return max(categories, key=score)


def find_share_source_channel(
    category: discord.CategoryChannel,
    source_name: str,
) -> Optional[discord.TextChannel]:
    wanted = share_source_key(source_name)
    if wanted is None:
        return None

    matches = [
        channel
        for channel in list(getattr(category, "channels", []) or [])
        if isinstance(channel, discord.TextChannel) and share_source_key(getattr(channel, "name", "")) == wanted
    ]
    if not matches:
        return None
    exact = next((channel for channel in matches if str(channel.name) == wanted), None)
    return exact or matches[0]


async def create_or_repair_hidden_share_hub(
    guild: discord.Guild,
    actor: discord.abc.User,
) -> HubRepairResult:
    """Create or repair one canonical hub without deleting duplicate resources."""

    created: list[str] = []
    repaired: list[str] = []
    warnings: list[str] = []

    categories = find_share_router_categories(guild)
    category = find_share_router_category(guild)

    if category is None:
        overwrites = _permission_overwrite_map(guild, actor)
        overwrites = await _add_configured_staff_overwrites(guild, overwrites)
        category = await guild.create_category(
            SHARE_ROUTER_CATEGORY_NAME,
            overwrites=overwrites,
            reason="Dank Shield Share Router proxy hub",
        )
        created.append(SHARE_ROUTER_CATEGORY_NAME)
    else:
        current_overwrites = dict(getattr(category, "overwrites", {}) or {})
        overwrites = _permission_overwrite_map(guild, actor, current=current_overwrites)
        overwrites = await _add_configured_staff_overwrites(guild, overwrites)
        changes: dict[str, Any] = {"overwrites": overwrites}
        if str(category.name) != SHARE_ROUTER_CATEGORY_NAME:
            changes["name"] = SHARE_ROUTER_CATEGORY_NAME
            repaired.append(f"category -> {SHARE_ROUTER_CATEGORY_NAME}")
        await category.edit(
            **changes,
            reason="Dank Shield Share Router privacy/name repair",
        )

    if len(categories) > 1:
        extras = [str(item.name) for item in categories if int(item.id) != int(category.id)]
        warnings.append(
            "Multiple Share Router-like categories exist. Nothing was deleted automatically: "
            + ", ".join(extras[:5])
        )

    for canonical in DEFAULT_SHARE_CHANNELS:
        matches = [
            channel
            for channel in list(getattr(category, "channels", []) or [])
            if isinstance(channel, discord.TextChannel)
            and share_source_key(getattr(channel, "name", "")) == canonical
        ]
        channel = find_share_source_channel(category, canonical)

        if channel is None:
            source_overwrites = _permission_overwrite_map(guild, actor)
            source_overwrites = await _add_configured_staff_overwrites(guild, source_overwrites)
            channel = await guild.create_text_channel(
                canonical,
                category=category,
                nsfw=False,
                overwrites=source_overwrites,
                reason="Dank Shield Share Router proxy source",
            )
            created.append(canonical)
        else:
            current_source_overwrites = dict(getattr(channel, "overwrites", {}) or {})
            source_overwrites = _permission_overwrite_map(
                guild,
                actor,
                current=current_source_overwrites,
            )
            source_overwrites = await _add_configured_staff_overwrites(guild, source_overwrites)
            edits: dict[str, Any] = {"overwrites": source_overwrites}
            old_name = str(channel.name)
            if old_name != canonical:
                edits["name"] = canonical
                repaired.append(f"{old_name} -> {canonical}")
            try:
                if bool(channel.is_nsfw()):
                    edits["nsfw"] = False
                    repaired.append(f"{canonical} -> non-age-restricted proxy")
            except Exception:
                warnings.append(f"Could not verify age-restriction state for {canonical}.")
            await channel.edit(
                **edits,
                reason="Dank Shield Share Router canonical proxy privacy/name repair",
            )

        if len(matches) > 1:
            extras = [str(item.name) for item in matches if int(item.id) != int(channel.id)]
            warnings.append(
                f"Multiple channels match {canonical}. Nothing was deleted automatically: "
                + ", ".join(extras[:5])
            )

    return HubRepairResult(
        category_id=int(category.id),
        created=tuple(created),
        repaired=tuple(repaired),
        warnings=tuple(warnings),
    )


def ensure_share_router_runtime(bot: Any) -> bool:
    """Install the one production on_message listener idempotently."""

    try:
        existing = list((getattr(bot, "extra_events", {}) or {}).get("on_message") or [])
        for fn in existing:
            if (
                getattr(fn, "__name__", "") == "route_message"
                and getattr(fn, "__module__", "") == __name__
            ):
                return True
        bot.add_listener(route_message, "on_message")
        _log("active; private share proxies can route into configured destinations")
        return True
    except Exception as exc:
        _log(f"runtime install failed: {type(exc).__name__}: {exc}")
        return False


__all__ = [
    "HubRepairResult",
    "ROUTES_FILE",
    "RouteHealth",
    "create_or_repair_hidden_share_hub",
    "ensure_share_router_runtime",
    "find_share_router_categories",
    "find_share_router_category",
    "find_share_source_channel",
    "guild_routes",
    "remove_route",
    "route_for_source",
    "route_health",
    "route_message",
    "route_permission_blockers",
    "save_route",
    "source_age_blocker",
    "source_privacy_blocker",
]
