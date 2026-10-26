from __future__ import annotations

"""One durable, interactive LFG message per guild, never a per-user public menu.

Uses the existing CAS-backed guild config and Community Hub service. No new
slash commands, tables, or user-presence scraping. One bounded refresh worker
edits the same Discord message only when its public content actually changes.
"""

import asyncio
import hashlib
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import discord

from . import community_hub_service as hub
from .guild_config import get_guild_config, compare_and_swap_guild_config_key

CONFIG_KEY = "community_hub_public_lfg_panel_v1"
JOINABLE = frozenset(("open", "forming", "ready", "active"))
MAX_DISPLAY = 5
REFRESH_SECONDS = 60
TICK_SECONDS = 15
MAX_GUILDS_PER_TICK = 4
_MANUAL_REFRESH_GAP = 30
_TASK: asyncio.Task | None = None
_VIEW_REGISTERED = False
_DIRTY: set[int] = set()
_RENDERED: dict[tuple[int, int], str] = {}
_LAST_MANUAL: dict[int, float] = {}
_CURSOR = 0


def _id(value: Any) -> int:
    try:
        raw = str(value or "").strip()
        return int(raw) if raw.isdecimal() and 0 < len(raw) <= 20 and int(raw) > 0 else 0
    except (TypeError, ValueError):
        return 0


def _record(cfg: Any) -> dict[str, Any] | None:
    if not isinstance(cfg, dict):
        return None
    row = cfg.get(CONFIG_KEY)
    if not isinstance(row, dict):
        return None
    if row.get("state") != "active":
        return None
    cid, mid = _id(row.get("channel_id")), _id(row.get("message_id"))
    if not cid or not mid:
        return None
    return {"channel_id": cid, "message_id": mid, "state": "active"}


def _is_unavailable(cfg: Any) -> bool:
    return str(cfg.get("source", "")).startswith("unavailable:")


def _safe_game(raw: Any) -> str:
    text = " ".join(
        "".join(ch if ch.isprintable() else " " for ch in str(raw or ""))
        .replace("@", "＠").split()
    )[:80]
    return discord.utils.escape_markdown(text) or "Gaming group"


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except ValueError:
        return None


def _eligible(session: dict[str, Any], settings: dict[str, Any], now: datetime) -> bool:
    if session.get("privacy") != "public" or session.get("state") not in JOINABLE:
        return False
    created = _parse_time(session.get("created_at"))
    active = _parse_time(session.get("last_activity_at"))
    if not created or not active:
        return False
    try:
        idle = max(300, int(settings.get("idle_timeout_seconds") or 1800))
        lifetime = max(3600, int(settings.get("max_session_lifetime_seconds") or 43200))
    except (TypeError, ValueError):
        return False
    return now < created + timedelta(seconds=lifetime) and now < active + timedelta(seconds=idle)


def board_embed(
    guild_name: str, sessions: list[dict[str, Any]],
    settings: dict[str, Any], *, unavailable: bool = False,
) -> discord.Embed:
    title = _safe_game(guild_name)[:70]
    embed = discord.Embed(
        title="🎮 " + title + " • Live Looking for Group",
        color=discord.Color.blurple(),
        description=(
            "Find teammates and gaming servers **beyond this Discord**, alongside groups hosted here. "
            "**Find Players** opens a private combined search with outside groups, "
            "approved Public Gaming Network communities and this server. "
            "This public panel stays in the channel."
        ),
    )
    if unavailable or not settings.get("enabled", True) or settings.get("maintenance_mode"):
        embed.add_field(
            name="🟠 Temporarily unavailable",
            value="Community Hub isn't available right now. Your original public board and buttons remain in place.",
            inline=False,
        )
        embed.set_footer(text="Dank Shield • persistent public panel")
        return embed

    now = datetime.now(timezone.utc)
    public = [s for s in sessions if _eligible(s, settings, now)]
    # Show only approved public, joinable sessions. No host/member identifiers,
    # private notes, private threads, invite links or raw presence information.
    for session in public[:MAX_DISPLAY]:
        capacity = max(1, min(100, _id(session.get("capacity")) or 6))
        status = {"open": "Open", "forming": "Forming", "ready": "Ready",
                  "active": "Playing"}.get(str(session.get("state")), "Open")
        embed.add_field(
            name="🎮 " + _safe_game(session.get("game_name"))[:88],
            value=f"{status} • Up to {capacity} players • Tap **Find Players** to view or join.",
            inline=False,
        )
    if not public:
        embed.add_field(
            name="🏠 No sessions hosted here yet",
            value="Tap **Find Players** to browse outside gaming groups and approved public servers now, "
                  "or start a group here. This section only tracks groups hosted in this server.",
            inline=False,
        )
    elif len(public) > MAX_DISPLAY:
        embed.add_field(
            name="More groups",
            value=f"{len(public)-MAX_DISPLAY} more open groups. Use **Find Players** for the full list.",
            inline=False,
        )
    embed.set_footer(text="Outside-first Find Players • local groups refresh automatically • private browsing")
    return embed


def _fingerprint(embed: discord.Embed) -> str:
    stable = json.dumps(embed.to_dict(), sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()


def _channel_permissions(guild: Any, channel: Any) -> bool:
    if not isinstance(channel, discord.TextChannel):
        return False
    me = getattr(guild, "me", None)
    if me is None:
        return False
    p = channel.permissions_for(me)
    return bool(p.view_channel and p.send_messages and p.embed_links and p.read_message_history)


async def _current_snapshot(guild: Any) -> discord.Embed:
    try:
        settings = await hub.get_settings(int(guild.id))
        if not settings.get("enabled", True) or settings.get("maintenance_mode"):
            return board_embed(guild.name, [], settings)
        rows = await hub.list_active_sessions(int(guild.id), limit=50)
        return board_embed(guild.name, rows, settings)
    except (hub.CommunityHubError, ValueError, TypeError):
        return board_embed(guild.name, [], {}, unavailable=True)


async def refresh_one(guild: Any, *, force: bool = False) -> str:
    cfg = await get_guild_config(int(guild.id))
    if _is_unavailable(cfg):
        return "config-unavailable"
    record = _record(cfg)
    if not record:
        return "not-configured"
    channel = guild.get_channel(record["channel_id"])
    if not _channel_permissions(guild, channel):
        return "channel-or-permissions-missing"
    embed = await _current_snapshot(guild)
    key = (int(guild.id), record["message_id"])
    checksum = _fingerprint(embed)
    if not force and _RENDERED.get(key) == checksum:
        return "unchanged"
    try:
        message = await channel.fetch_message(record["message_id"])
        if not force and message.embeds and _fingerprint(message.embeds[0]) == checksum:
            _RENDERED[key] = checksum
            return "unchanged"
        await message.edit(embed=embed, view=PublicLFGBoardView(),
                           allowed_mentions=discord.AllowedMentions.none())
        _RENDERED[key] = checksum
        return "updated"
    except discord.NotFound:
        # Do not silently generate duplicate public panels after channel moves.
        return "message-missing-repost-from-staff"
    except (discord.Forbidden, discord.HTTPException):
        return "discord-unavailable"


async def install_public_board(guild: Any, channel: Any) -> str:
    if not _channel_permissions(guild, channel):
        raise ValueError("Select a text channel with View, Send, Embed Links and Read History permissions.")
    gid = int(guild.id)
    for _ in range(3):
        cfg = await get_guild_config(gid, refresh=True)
        if _is_unavailable(cfg):
            raise RuntimeError("Server settings unavailable. No public board was posted.")
        old = cfg.get(CONFIG_KEY)
        current = _record(cfg)
        if current and current["channel_id"] == int(channel.id):
            status = await refresh_one(guild, force=True)
            if status == "updated" or status == "unchanged":
                return "Existing public LFG panel refreshed in this channel."
        # Cross-process reservation prevents duplicate posts by two staff.
        if isinstance(old, dict) and old.get("state") == "installing":
            started = _parse_time(old.get("started_at"))
            if started and datetime.now(timezone.utc) - started < timedelta(minutes=3):
                raise ValueError("Another staff member is publishing the panel. Reopen this screen shortly.")
        reservation = {
            "state": "installing", "channel_id": str(channel.id),
            "nonce": uuid.uuid4().hex,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        won, _ = await compare_and_swap_guild_config_key(
            gid, CONFIG_KEY, expected=old, value=reservation,
            source="community_hub_public_panel_install",
        )
        if not won:
            continue
        message = None
        try:
            embed = await _current_snapshot(guild)
            message = await channel.send(
                embed=embed, view=PublicLFGBoardView(),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            final = {
                "state": "active", "channel_id": str(channel.id),
                "message_id": str(message.id),
            }
            saved, _ = await compare_and_swap_guild_config_key(
                gid, CONFIG_KEY, expected=reservation, value=final,
                source="community_hub_public_panel_publish",
            )
            if not saved:
                raise RuntimeError("Another setup changed the panel. The new message will be rolled back.")
            _RENDERED[(gid, int(message.id))] = _fingerprint(embed)
            # Remove prior panel only AFTER the new one is durable, and only
            # if that exact message is ours. Never delete arbitrary channel posts.
            if current and (current["channel_id"], current["message_id"]) != (int(channel.id), int(message.id)):
                try:
                    previous = guild.get_channel(current["channel_id"])
                    if isinstance(previous, discord.TextChannel):
                        old_message = await previous.fetch_message(current["message_id"])
                        if old_message.author.id == guild.me.id:
                            await old_message.delete()
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass
            return f"Public LFG panel posted in {channel.mention}. Members can use it immediately."
        except Exception:
            if message is not None:
                try:
                    await message.delete()
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass
            try:
                await compare_and_swap_guild_config_key(
                    gid, CONFIG_KEY, expected=reservation, value=old,
                    source="community_hub_public_panel_rollback",
                )
            except Exception:
                pass
            raise
    raise RuntimeError("Another moderator changed the setup. Reopen and try again.")


async def remove_public_board(guild: Any) -> bool:
    cfg = await get_guild_config(int(guild.id), refresh=True)
    if _is_unavailable(cfg):
        raise RuntimeError("Guild settings unavailable. No deletion attempted.")
    old = cfg.get(CONFIG_KEY)
    record = _record(cfg)
    if not record:
        return False
    ok, _ = await compare_and_swap_guild_config_key(
        int(guild.id), CONFIG_KEY, expected=old, value=None,
        source="community_hub_public_panel_remove",
    )
    if not ok:
        raise RuntimeError("Panel settings changed. Reopen and try again.")
    # Disable its interaction authorization first, then delete only our post.
    channel = guild.get_channel(record["channel_id"])
    if isinstance(channel, discord.TextChannel):
        try:
            message = await channel.fetch_message(record["message_id"])
            if getattr(getattr(message, "author", None), "id", None) == guild.me.id:
                await message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass
    _RENDERED.pop((int(guild.id), record["message_id"]), None)
    return True


def mark_dirty(guild_id: Any) -> None:
    gid = _id(guild_id)
    if gid and len(_DIRTY) < 1000:
        _DIRTY.add(gid)


class PublicLFGBoardView(discord.ui.View):
    """Fixed IDs + no timeout: one registered view works after restarts."""
    def __init__(self):
        super().__init__(timeout=None)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if (interaction.guild is None or bool(getattr(interaction.user, "bot", False))
                or interaction.message is None):
            await interaction.response.send_message(
                "Use this public board inside its Discord server.", ephemeral=True)
            return False
        # Fail closed for copies of the panel or staff-disabled panels.
        try:
            cfg = await get_guild_config(int(interaction.guild.id))
            record = _record(cfg)
            if _is_unavailable(cfg) or not record or (
                record["channel_id"] != int(interaction.channel_id or 0)
                or record["message_id"] != int(interaction.message.id)
            ):
                await interaction.response.send_message(
                    "This panel was replaced. Use the current LFG panel or /dank → Community Hub.",
                    ephemeral=True)
                return False
        except Exception:
            await interaction.response.send_message(
                "Panel verification is unavailable. Try again shortly.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Find Players", emoji="🔎", style=discord.ButtonStyle.primary,
                       custom_id="dank:lfg:board:find:v1", row=0)
    async def find(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .commands_ext.public_community_hub import _open_unified_player_discovery
        await interaction.response.defer(thinking=True, ephemeral=True)
        await _open_unified_player_discovery(interaction, int(interaction.user.id))

    @discord.ui.button(label="Create Group", emoji="➕", style=discord.ButtonStyle.success,
                       custom_id="dank:lfg:board:create:v1", row=0)
    async def create(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .commands_ext.public_community_hub import StartSessionModal
        await interaction.response.send_modal(StartSessionModal())

    @discord.ui.button(label="My Groups", emoji="👥", style=discord.ButtonStyle.secondary,
                       custom_id="dank:lfg:board:mine:v1", row=0)
    async def mine(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .commands_ext.public_community_hub import (
            _my_groups_embed, MyGroupsView, _edit_private_original, _error_text,
            CommunityHubView,
        )
        await interaction.response.defer(thinking=True, ephemeral=True)
        owner = int(interaction.user.id)
        try:
            sessions = await hub.list_user_sessions(interaction.guild.id, owner, active_only=True, limit=25)
            await _edit_private_original(
                interaction, embed=_my_groups_embed(sessions),
                view=MyGroupsView(owner, sessions),
            )
        except hub.CommunityHubError as exc:
            await _edit_private_original(interaction,
                content=_error_text(exc), view=CommunityHubView(owner))

    @discord.ui.button(label="More Outside Groups", emoji="🌍", style=discord.ButtonStyle.secondary,
                       custom_id="dank:lfg:board:outside:v1", row=1)
    async def outside(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .commands_ext.public_community_hub import _open_atlas_groups
        await interaction.response.defer(thinking=True, ephemeral=True)
        await _open_atlas_groups(interaction, int(interaction.user.id))

    @discord.ui.button(label="Public Servers", emoji="🧭", style=discord.ButtonStyle.secondary,
                       custom_id="dank:lfg:board:network:v1", row=1)
    async def network(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .commands_ext.public_community_hub import _open_public_lfg_network
        await interaction.response.defer(thinking=True, ephemeral=True)
        await _open_public_lfg_network(interaction, int(interaction.user.id))

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary,
                       custom_id="dank:lfg:board:refresh:v1", row=1)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        now = time.monotonic()
        gid = int(interaction.guild.id)
        if now - _LAST_MANUAL.get(gid, 0) < _MANUAL_REFRESH_GAP:
            return await interaction.response.send_message(
                "This live board was just checked. It updates automatically.", ephemeral=True)
        _LAST_MANUAL[gid] = now
        await interaction.response.defer(thinking=True, ephemeral=True)
        status = await refresh_one(interaction.guild, force=True)
        await interaction.followup.send(
            "Board refreshed." if status in {"updated", "unchanged"}
            else f"Board couldn't refresh ({status}). Staff can check the panel setup.",
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
        )


async def _worker(bot: Any) -> None:
    global _CURSOR
    await bot.wait_until_ready()
    print("🎮 Public LFG board worker started (persistent controls + bounded refresh)")
    last_check: dict[int, float] = {}
    while not bot.is_closed():
        try:
            guilds = list(getattr(bot, "guilds", []))
            if guilds:
                selected = []
                # Event-driven guilds first, while preserving a bounded normal sweep.
                for gid in list(_DIRTY)[:MAX_GUILDS_PER_TICK]:
                    _DIRTY.discard(gid)
                    guild = bot.get_guild(gid)
                    if guild:
                        selected.append(guild)
                remaining = MAX_GUILDS_PER_TICK - len(selected)
                if remaining > 0:
                    for offset in range(remaining):
                        selected.append(guilds[(_CURSOR+offset) % len(guilds)])
                    _CURSOR = (_CURSOR + remaining) % len(guilds)
                seen: set[int] = set()
                for guild in selected:
                    gid = int(guild.id)
                    if gid in seen:
                        continue
                    seen.add(gid)
                    now = time.monotonic()
                    if now - last_check.get(gid, 0) < REFRESH_SECONDS:
                        continue
                    last_check[gid] = now
                    try:
                        result = await refresh_one(guild)
                        if result not in {"unchanged","updated","not-configured"}:
                            print(f"⚠️ public_lfg_board guild={gid} status={result}")
                    except Exception as exc:
                        print(f"⚠️ public_lfg_board guild={gid} err={type(exc).__name__}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"⚠️ public_lfg_board sweep err={type(exc).__name__}")
        await asyncio.sleep(TICK_SECONDS)


def start_public_board_worker(bot: Any) -> asyncio.Task:
    global _TASK, _VIEW_REGISTERED
    if not _VIEW_REGISTERED:
        bot.add_view(PublicLFGBoardView())
        _VIEW_REGISTERED = True
    if _TASK is None or _TASK.done():
        _TASK = asyncio.create_task(_worker(bot), name="community_public_lfg_board")
    return _TASK
