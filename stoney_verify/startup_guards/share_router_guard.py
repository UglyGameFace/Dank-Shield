from __future__ import annotations

"""Per-guild hidden share-channel router.

Purpose:
- let guilds keep fancy/stylized public channel names
- give staff plain hidden share-* channels for mobile share sheets
- route shared links/posts into the fancy target channel
- delete the hidden source message after routing
- keep all config isolated by guild_id

This module intentionally does not hardcode Felix's channel IDs.
Each guild saves routes with /dank share-router.
"""

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

import discord
from discord import app_commands

try:
    from stoney_verify.globals import bot
except Exception:  # pragma: no cover
    bot = None  # type: ignore

try:
    from stoney_verify.commands_ext.public_setup_group import stoney_group
except Exception:  # pragma: no cover
    stoney_group = None  # type: ignore


_INSTALLED = False
_DATA_LOCK = asyncio.Lock()
_RECENT_ROUTE_KEYS: dict[tuple[int, int, str], float] = {}

DEFAULT_SHARE_CHANNELS: tuple[str, ...] = (
    "share-gaming-news",
    "share-deals",
    "share-memes",
    "share-announcements",
)

ROUTES_FILE = Path(
    os.getenv(
        "STONEY_SHARE_ROUTES_FILE",
        str(Path(os.getenv("STONEY_DATA_DIR", "data")) / "share_routes.json"),
    )
)

URL_RE = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)


def _log(message: str) -> None:
    try:
        print(f"🔗 share_router_guard {message}")
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


def _can_setup(interaction: discord.Interaction) -> bool:
    try:
        if interaction.guild is None:
            return False
        perms = getattr(interaction.user, "guild_permissions", None)
        if bool(getattr(perms, "administrator", False)):
            return True
        if bool(getattr(perms, "manage_guild", False)):
            return True
        if bool(getattr(perms, "manage_channels", False)):
            return True
    except Exception:
        pass
    return False


async def _require_setup(interaction: discord.Interaction) -> bool:
    if _can_setup(interaction):
        return True
    try:
        await interaction.response.send_message(
            "❌ You need **Manage Server** or **Manage Channels** to configure Share Router.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except Exception:
        pass
    return False


def _load_all_unlocked() -> dict[str, Any]:
    try:
        if not ROUTES_FILE.exists():
            return {}
        data = json.loads(ROUTES_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_all_unlocked(data: dict[str, Any]) -> None:
    ROUTES_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = ROUTES_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    tmp.replace(ROUTES_FILE)


async def _guild_routes(guild_id: int) -> list[dict[str, Any]]:
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


async def _save_route(
    guild_id: int,
    *,
    source_channel_id: int,
    target_channel_id: int,
    delete_source: bool,
    created_by_id: int,
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
        target_text = str(int(target_channel_id))
        next_routes: list[dict[str, Any]] = []
        for raw in routes:
            if not isinstance(raw, dict):
                continue
            if str(raw.get("source_channel_id")) == source_text:
                continue
            next_routes.append(dict(raw))

        next_routes.append(
            {
                "source_channel_id": source_text,
                "target_channel_id": target_text,
                "enabled": True,
                "delete_source": bool(delete_source),
                "created_by_id": str(int(created_by_id)),
                "created_at": _utc_iso(),
                "updated_at": _utc_iso(),
            }
        )
        bucket["routes"] = next_routes
        data[key] = bucket
        _save_all_unlocked(data)


async def _remove_route(guild_id: int, source_channel_id: int) -> bool:
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
        bucket["routes"] = [r for r in routes if not (isinstance(r, dict) and str(r.get("source_channel_id")) == source_text)]
        data[key] = bucket
        _save_all_unlocked(data)
        return len(bucket["routes"]) != before


def _message_share_text(message: discord.Message) -> str:
    parts: list[str] = []
    content = _safe_str(getattr(message, "content", ""))
    if content:
        parts.append(content)

    try:
        for embed in list(getattr(message, "embeds", []) or []):
            for attr in ("url", "title", "description"):
                raw = getattr(embed, attr, None)
                text = _safe_str(raw)
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
    try:
        stale = [key for key, saved in _RECENT_ROUTE_KEYS.items() if now - float(saved or 0.0) > 3600.0]
        for key in stale:
            _RECENT_ROUTE_KEYS.pop(key, None)
    except Exception:
        pass


async def _send_modlog(guild: discord.Guild, embed: discord.Embed) -> None:
    try:
        from stoney_verify import spam_guard
        sender = getattr(spam_guard, "_send_modlog_embed", None)
        if callable(sender):
            await sender(guild, embed)
    except Exception:
        pass


def _route_for_source(routes: list[dict[str, Any]], source_channel_id: int) -> Optional[dict[str, Any]]:
    source_text = str(int(source_channel_id))
    for route in routes:
        if not route.get("enabled", True):
            continue
        if str(route.get("source_channel_id")) == source_text:
            return route
    return None


async def _route_message(message: discord.Message) -> None:
    try:
        guild = message.guild
        if guild is None or not isinstance(message.channel, discord.TextChannel):
            return
        if getattr(message.author, "bot", False):
            return

        routes = await _guild_routes(int(guild.id))
        route = _route_for_source(routes, int(message.channel.id))
        if not route:
            return

        target_id = _safe_int(route.get("target_channel_id"), 0)
        target = guild.get_channel(target_id)
        if not isinstance(target, discord.TextChannel):
            return

        text = _message_share_text(message)
        if not text:
            return

        now = time.monotonic()
        _prune_recent(now)
        key_text = _dedupe_key(text)
        dedupe_key = (int(guild.id), int(target.id), key_text)
        duplicate = bool(key_text and dedupe_key in _RECENT_ROUTE_KEYS)
        if key_text:
            _RECENT_ROUTE_KEYS[dedupe_key] = now

        me = guild.me
        if not isinstance(me, discord.Member):
            return
        target_perms = target.permissions_for(me)
        source_perms = message.channel.permissions_for(me)

        if not target_perms.view_channel or not target_perms.send_messages:
            return

        if not duplicate:
            routed = (
                f"{text}\n\n"
                f"↪️ Shared by {message.author.mention} via {message.channel.mention}"
            )
            await target.send(
                routed[:2000],
                allowed_mentions=discord.AllowedMentions.none(),
            )

        if bool(route.get("delete_source", True)) and source_perms.manage_messages:
            try:
                await message.delete(reason="Dank Shield Share Router: routed hidden share message")
            except discord.NotFound:
                pass
            except Exception:
                pass

        embed = discord.Embed(
            title="🔗 Share Router Routed Message" if not duplicate else "🔁 Share Router Duplicate Cleaned",
            color=discord.Color.green() if not duplicate else discord.Color.orange(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Source", value=f"{message.channel.mention} (`{message.channel.id}`)", inline=False)
        embed.add_field(name="Target", value=f"{target.mention} (`{target.id}`)", inline=False)
        embed.add_field(name="Author", value=f"{message.author.mention} (`{message.author.id}`)", inline=False)
        if duplicate:
            embed.add_field(name="Duplicate", value="Already routed recently, so it was not reposted.", inline=False)
        await _send_modlog(guild, embed)

    except Exception as exc:
        _log(f"route failed: {type(exc).__name__}: {exc}")


def _source_privacy_blocker(source: discord.TextChannel) -> str:
    try:
        everyone_perms = source.permissions_for(source.guild.default_role)
        if everyone_perms.view_channel:
            return "@everyone can view the source share channel. Hide it first to prevent leaks."
    except Exception:
        return "Could not verify @everyone visibility for the source channel."
    return ""


def _permission_blockers(source: discord.TextChannel, target: discord.TextChannel, *, delete_source: bool) -> list[str]:
    blockers: list[str] = []
    me = source.guild.me
    if not isinstance(me, discord.Member):
        return ["Bot member could not be resolved."]

    source_perms = source.permissions_for(me)
    target_perms = target.permissions_for(me)

    if not source_perms.view_channel:
        blockers.append(f"Bot cannot view source {source.mention}.")
    if not source_perms.read_message_history:
        blockers.append(f"Bot cannot read source history in {source.mention}.")
    if bool(delete_source) and not source_perms.manage_messages:
        blockers.append(f"Bot needs Manage Messages in {source.mention} to keep the share channel clean.")

    if not target_perms.view_channel:
        blockers.append(f"Bot cannot view target {target.mention}.")
    if not target_perms.send_messages:
        blockers.append(f"Bot cannot send messages in target {target.mention}.")
    if not target_perms.embed_links:
        blockers.append(f"Bot should have Embed Links in target {target.mention} for rich previews.")

    return blockers


async def _routes_embed(guild: discord.Guild) -> discord.Embed:
    routes = await _guild_routes(int(guild.id))
    embed = discord.Embed(
        title="🔗 Dank Shield Share Router",
        description=(
            "Use hidden plain `share-*` channels for mobile sharing, while posts land in your fancy public channels.\n\n"
            "Run `/dank share-router` with **source_channel** and **target_channel** selected to add a route."
        ),
        color=discord.Color.blurple(),
    )
    if not routes:
        embed.add_field(
            name="Saved routes",
            value="None yet. Press **Create Hidden Share Hub**, then add routes with the command picker.",
            inline=False,
        )
        return embed

    lines: list[str] = []
    for route in routes[:20]:
        source = guild.get_channel(_safe_int(route.get("source_channel_id"), 0))
        target = guild.get_channel(_safe_int(route.get("target_channel_id"), 0))
        source_text = source.mention if isinstance(source, discord.TextChannel) else f"`{route.get('source_channel_id')}`"
        target_text = target.mention if isinstance(target, discord.TextChannel) else f"`{route.get('target_channel_id')}`"
        delete_text = "delete source" if route.get("delete_source", True) else "keep source"
        enabled_text = "on" if route.get("enabled", True) else "off"
        lines.append(f"• {source_text} → {target_text} · `{delete_text}` · `{enabled_text}`")
    if len(routes) > 20:
        lines.append(f"…and {len(routes) - 20} more")
    embed.add_field(name="Saved routes", value="\n".join(lines)[:1024], inline=False)
    return embed


async def _config_role_overwrites(guild: discord.Guild, user: discord.abc.User) -> dict[discord.Role | discord.Member, discord.PermissionOverwrite]:
    overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
    }

    me = guild.me
    if isinstance(me, discord.Member):
        overwrites[me] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_messages=True,
            embed_links=True,
        )

    if isinstance(user, discord.Member):
        overwrites[user] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
        )

    try:
        from stoney_verify.guild_config import get_guild_config
        cfg = await get_guild_config(int(guild.id), refresh=False)
    except Exception:
        cfg = None

    role_ids: set[int] = set()
    for key in ("staff_role_id", "server_control_role_id", "control_role_id", "perm_role_id", "vc_staff_role_id"):
        try:
            if hasattr(cfg, "get"):
                raw = cfg.get(key)
            else:
                raw = getattr(cfg, key, None)
            rid = _safe_int(raw, 0)
            if rid > 0:
                role_ids.add(rid)
        except Exception:
            pass

    for rid in role_ids:
        role = guild.get_role(rid)
        if role is not None and not role.is_default():
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
            )

    return overwrites


async def _create_hidden_share_hub(interaction: discord.Interaction) -> None:
    if not await _require_setup(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)

    perms = getattr(interaction.user, "guild_permissions", None)
    if not (bool(getattr(perms, "manage_channels", False)) or bool(getattr(perms, "administrator", False))):
        return await interaction.response.send_message(
            "❌ You need **Manage Channels** to create the hidden share hub.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    await interaction.response.defer(ephemeral=True, thinking=True)

    overwrites = await _config_role_overwrites(guild, interaction.user)
    category = discord.utils.get(guild.categories, name="🔗 SHARE ROUTES")
    created: list[str] = []

    try:
        if category is None:
            category = await guild.create_category(
                "🔗 SHARE ROUTES",
                overwrites=overwrites,
                reason="Dank Shield Share Router hidden share hub",
            )
            created.append(category.name)
        else:
            try:
                await category.edit(overwrites=overwrites, reason="Dank Shield Share Router privacy repair")
            except Exception:
                pass

        existing_names = {str(ch.name).lower(): ch for ch in getattr(category, "channels", []) or []}
        for name in DEFAULT_SHARE_CHANNELS:
            if name.lower() in existing_names:
                continue
            channel = await guild.create_text_channel(
                name,
                category=category,
                reason="Dank Shield Share Router hidden share channel",
            )
            created.append(channel.name)

        embed = await _routes_embed(guild)
        embed.title = "✅ Hidden Share Hub Ready"
        embed.add_field(
            name="Created / verified",
            value="\n".join(f"• `{x}`" for x in created) if created else "Everything already existed. Permissions were checked.",
            inline=False,
        )
        embed.add_field(
            name="Next step",
            value="Use `/dank share-router` and pick a **source_channel** plus its fancy **target_channel**.",
            inline=False,
        )
        await interaction.edit_original_response(embed=embed, view=ShareRouterPanelView())

    except Exception as exc:
        await interaction.edit_original_response(
            content=f"❌ Could not create hidden share hub: `{type(exc).__name__}: {exc}`",
            embed=None,
            view=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class ShareRouterPanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Create Hidden Share Hub", emoji="🔗", style=discord.ButtonStyle.primary, custom_id="dank_share_router:create_hub")
    async def create_hub(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _create_hidden_share_hub(interaction)


async def _share_router_command(
    interaction: discord.Interaction,
    source_channel: Optional[discord.TextChannel] = None,
    target_channel: Optional[discord.TextChannel] = None,
    remove_source: Optional[discord.TextChannel] = None,
    delete_source: bool = True,
) -> None:
    if not await _require_setup(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)

    if remove_source is not None:
        removed = await _remove_route(int(guild.id), int(remove_source.id))
        embed = await _routes_embed(guild)
        embed.title = "🗑️ Share Route Removed" if removed else "No Share Route Found"
        embed.description = f"Removed route for {remove_source.mention}." if removed else f"No saved route uses {remove_source.mention} as a source."
        return await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    if source_channel is None and target_channel is None:
        embed = await _routes_embed(guild)
        return await interaction.response.send_message(
            embed=embed,
            view=ShareRouterPanelView(),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    if source_channel is None or target_channel is None:
        return await interaction.response.send_message(
            "❌ Pick both **source_channel** and **target_channel**, or leave both empty to view routes.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    if source_channel.guild.id != guild.id or target_channel.guild.id != guild.id:
        return await interaction.response.send_message(
            "❌ Source and target must belong to this server.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    if source_channel.id == target_channel.id:
        return await interaction.response.send_message(
            "❌ Source and target cannot be the same channel.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    blockers: list[str] = []
    privacy = _source_privacy_blocker(source_channel)
    if privacy:
        blockers.append(privacy)
    blockers.extend(_permission_blockers(source_channel, target_channel, delete_source=delete_source))

    if blockers:
        embed = discord.Embed(
            title="🚫 Share Route Not Saved",
            description="\n".join(f"• {x}" for x in blockers),
            color=discord.Color.red(),
        )
        embed.add_field(
            name="Safe setup",
            value="Keep the source `share-*` channel hidden from @everyone and give Dank Shield Manage Messages there.",
            inline=False,
        )
        return await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    await _save_route(
        int(guild.id),
        source_channel_id=int(source_channel.id),
        target_channel_id=int(target_channel.id),
        delete_source=bool(delete_source),
        created_by_id=int(interaction.user.id),
    )

    embed = await _routes_embed(guild)
    embed.title = "✅ Share Route Saved"
    embed.description = (
        f"{source_channel.mention} will now route shared posts to {target_channel.mention}.\n\n"
        f"Delete hidden source message: **{'Yes' if delete_source else 'No'}**"
    )
    return await interaction.response.send_message(
        embed=embed,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


def _install_command() -> bool:
    if stoney_group is None:
        _log("stoney_group unavailable; /dank share-router not installed")
        return False

    try:
        existing = {getattr(command, "name", "") for command in getattr(stoney_group, "commands", []) or []}
        if "share-router" in existing:
            return True

        decorated = app_commands.describe(
            source_channel="Hidden plain share-* source channel.",
            target_channel="Fancy public destination channel.",
            remove_source="Remove the route for this source channel.",
            delete_source="Delete the hidden source message after routing.",
        )(_share_router_command)

        try:
            decorated = app_commands.default_permissions(manage_guild=True)(decorated)
        except Exception:
            pass

        stoney_group.command(
            name="share-router",
            description="Configure hidden share channels that route posts into fancy public channels.",
        )(decorated)
        return True
    except Exception as exc:
        _log(f"command install failed: {type(exc).__name__}: {exc}")
        return False


def install() -> bool:
    global _INSTALLED
    _install_command()

    if _INSTALLED:
        return True
    if bot is None:
        _log("bot unavailable; listener not installed")
        return False

    try:
        existing = list((getattr(bot, "extra_events", {}) or {}).get("on_message") or [])
        if not any(getattr(fn, "__name__", "") == "_route_message" and getattr(fn, "__module__", "") == __name__ for fn in existing):
            bot.add_listener(_route_message, "on_message")

        _INSTALLED = True
        _log("active; hidden share-* channels can route posts into fancy public channels")
        return True
    except Exception as exc:
        _log(f"install failed: {type(exc).__name__}: {exc}")
        return False


install()

__all__ = ["install"]
