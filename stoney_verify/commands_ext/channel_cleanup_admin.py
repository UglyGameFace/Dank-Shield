from __future__ import annotations

import asyncio
import os
from datetime import timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

import discord
from discord import app_commands

from ..globals import *  # noqa: F401,F403
from .common import _staff_check


# ============================================================
# Optional bridge to root cleanup worker
# ============================================================
try:
    from ..channel_cleanup import ensure_channel_cleanup_worker_started
except Exception:
    async def ensure_channel_cleanup_worker_started() -> bool:
        return False


# ============================================================
# Registration guard
# ============================================================
_REGISTERED = False


# ============================================================
# Helpers
# ============================================================
def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        if isinstance(value, bool):
            return default
        return int(str(value).strip())
    except Exception:
        return default


def _as_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off"}:
            return False
        return default
    except Exception:
        return default


def _parse_csv_ints(raw: Any) -> List[int]:
    out: List[int] = []
    try:
        if raw is None:
            return out
        text = str(raw).strip()
        if not text:
            return out

        for part in text.split(","):
            n = _as_int(part, 0)
            if n > 0 and n not in out:
                out.append(n)
    except Exception:
        pass
    return out


def _parse_hours_map(raw: Any) -> Dict[int, int]:
    """
    Accepts formats like:
      "123456789:24,987654321:168"
    """
    out: Dict[int, int] = {}
    try:
        text = str(raw or "").strip()
        if not text:
            return out

        for pair in text.split(","):
            if ":" not in pair:
                continue
            left, right = pair.split(":", 1)
            cid = _as_int(left, 0)
            hrs = _as_int(right, 0)
            if cid > 0 and hrs > 0:
                out[cid] = hrs
    except Exception:
        pass
    return out


def _configured_cleanup_channel_ids() -> List[int]:
    """
    Pull configured cleanup targets by ID so channel names can change safely.

    Priority sources:
    - explicit CSV lists
    - specific known per-channel IDs
    """
    ids: List[int] = []

    csv_keys = (
        "CHANNEL_CLEANUP_TARGET_IDS",
        "AUTO_CLEANUP_CHANNEL_IDS",
        "AUTO_PURGE_CHANNEL_IDS",
    )

    single_id_keys = (
        "UNVERIFIED_CHAT_CHANNEL_ID",
        "UNVERIFIED_ONLY_CHAT_CHANNEL_ID",
        "VERIFY_WAIT_CHANNEL_ID",
        "VERIFY_HELP_CHANNEL_ID",
        "UNVERIFIED_CHANNEL_ID",
    )

    for key in csv_keys:
        try:
            for cid in _parse_csv_ints(globals().get(key)):
                if cid not in ids:
                    ids.append(cid)
        except Exception:
            pass

        try:
            for cid in _parse_csv_ints(os.getenv(key, "")):
                if cid not in ids:
                    ids.append(cid)
        except Exception:
            pass

    for key in single_id_keys:
        try:
            cid = _as_int(globals().get(key), 0)
            if cid > 0 and cid not in ids:
                ids.append(cid)
        except Exception:
            pass

        try:
            cid = _as_int(os.getenv(key, ""), 0)
            if cid > 0 and cid not in ids:
                ids.append(cid)
        except Exception:
            pass

    return ids


def _configured_hours_map() -> Dict[int, int]:
    """
    Optional per-channel retention overrides by channel ID.

    Accepted config examples:
      CHANNEL_CLEANUP_HOURS_BY_ID="123:24,456:168"
      AUTO_CLEANUP_HOURS_BY_ID="789:72"
    """
    merged: Dict[int, int] = {}

    keys = (
        "CHANNEL_CLEANUP_HOURS_BY_ID",
        "AUTO_CLEANUP_HOURS_BY_ID",
        "AUTO_PURGE_HOURS_BY_ID",
    )

    for key in keys:
        try:
            merged.update(_parse_hours_map(globals().get(key)))
        except Exception:
            pass

        try:
            merged.update(_parse_hours_map(os.getenv(key, "")))
        except Exception:
            pass

    return merged


def _default_cleanup_hours() -> int:
    for key in (
        "CHANNEL_CLEANUP_DEFAULT_MAX_AGE_HOURS",
        "CHANNEL_CLEANUP_HOURS",
        "AUTO_CLEANUP_HOURS",
        "UNVERIFIED_CHAT_PURGE_HOURS",
    ):
        try:
            hrs = _as_int(globals().get(key), 0)
            if hrs > 0:
                return hrs
        except Exception:
            pass

        try:
            hrs = _as_int(os.getenv(key, ""), 0)
            if hrs > 0:
                return hrs
        except Exception:
            pass

    return 168  # 7 days default


def _default_cleanup_limit() -> int:
    for key in (
        "CHANNEL_CLEANUP_MAX_DELETE_PER_RUN",
        "AUTO_CLEANUP_MAX_DELETE_PER_RUN",
        "AUTO_PURGE_MAX_DELETE_PER_RUN",
    ):
        try:
            n = _as_int(globals().get(key), 0)
            if n > 0:
                return n
        except Exception:
            pass

        try:
            n = _as_int(os.getenv(key, ""), 0)
            if n > 0:
                return n
        except Exception:
            pass

    return 250


def _default_include_pins() -> bool:
    for key in (
        "CHANNEL_CLEANUP_INCLUDE_PINNED",
        "AUTO_CLEANUP_INCLUDE_PINNED",
    ):
        try:
            return _as_bool(globals().get(key), False)
        except Exception:
            pass

        try:
            return _as_bool(os.getenv(key, ""), False)
        except Exception:
            pass

    return False


def _worker_running(bot_obj: Any) -> bool:
    try:
        task = getattr(bot_obj, "_channel_cleanup_worker_task", None)
        return bool(task and not task.done())
    except Exception:
        return False


async def _resolve_text_channel_by_id(
    guild: discord.Guild,
    channel_id: int,
) -> Optional[discord.TextChannel]:
    try:
        ch = guild.get_channel(int(channel_id))
        if isinstance(ch, discord.TextChannel):
            return ch
    except Exception:
        pass

    try:
        ch = await guild.fetch_channel(int(channel_id))
        if isinstance(ch, discord.TextChannel):
            return ch
    except Exception:
        pass

    return None


def _can_manage_messages(
    me: Optional[discord.Member],
    channel: discord.TextChannel,
) -> Tuple[bool, str]:
    try:
        if me is None:
            return False, "Bot member missing."
        perms = channel.permissions_for(me)
        if not perms.view_channel:
            return False, f"Missing View Channel in {channel.mention}."
        if not perms.read_message_history:
            return False, f"Missing Read Message History in {channel.mention}."
        if not perms.manage_messages:
            return False, f"Missing Manage Messages in {channel.mention}."
        return True, ""
    except Exception as e:
        return False, f"Permission check failed: {e}"


async def _collect_messages_for_cleanup(
    channel: discord.TextChannel,
    *,
    amount: Optional[int],
    older_than_hours: Optional[int],
    include_pinned: bool,
) -> List[discord.Message]:
    """
    Collect candidate messages to delete.

    Rules:
    - if older_than_hours is set, only delete messages older than that cutoff
    - if amount is set, stop after collecting that many eligible messages
    - pinned messages are skipped unless include_pinned=True
    """
    collected: List[discord.Message] = []

    cutoff = None
    if older_than_hours is not None and int(older_than_hours) > 0:
        cutoff = now_utc() - timedelta(hours=int(older_than_hours))

    try:
        search_limit = 5000
        if amount is not None and amount > 0:
            search_limit = min(5000, max(200, int(amount) * 8))

        async for msg in channel.history(limit=search_limit, oldest_first=False):
            try:
                if msg.pinned and not include_pinned:
                    continue

                if cutoff is not None and msg.created_at > cutoff:
                    continue

                collected.append(msg)

                if amount is not None and amount > 0 and len(collected) >= int(amount):
                    break
            except Exception:
                continue
    except Exception:
        pass

    return collected


async def _delete_messages(
    channel: discord.TextChannel,
    messages: List[discord.Message],
    *,
    dry_run: bool,
    reason: str,
) -> Dict[str, int]:
    result = {
        "matched": len(messages),
        "deleted": 0,
        "bulk_deleted": 0,
        "single_deleted": 0,
        "failed": 0,
        "young_candidates": 0,
        "old_candidates": 0,
    }

    if dry_run or not messages:
        return result

    now = now_utc()
    young: List[discord.Message] = []
    old: List[discord.Message] = []

    for msg in messages:
        try:
            age_seconds = (now - msg.created_at).total_seconds()
            if age_seconds < (14 * 24 * 60 * 60):
                young.append(msg)
            else:
                old.append(msg)
        except Exception:
            old.append(msg)

    result["young_candidates"] = len(young)
    result["old_candidates"] = len(old)

    # Bulk delete newer messages in chunks of 100
    for i in range(0, len(young), 100):
        chunk = young[i:i + 100]
        if not chunk:
            continue
        try:
            await channel.delete_messages(chunk, reason=reason)
            result["deleted"] += len(chunk)
            result["bulk_deleted"] += len(chunk)
            await asyncio.sleep(0.6)
        except Exception:
            # fall back to singles
            for msg in chunk:
                try:
                    await msg.delete()
                    result["deleted"] += 1
                    result["single_deleted"] += 1
                    await asyncio.sleep(0.35)
                except Exception:
                    result["failed"] += 1

    # Older than 14 days must be single deleted
    for idx, msg in enumerate(old, start=1):
        try:
            await msg.delete()
            result["deleted"] += 1
            result["single_deleted"] += 1
            if idx % 5 == 0:
                await asyncio.sleep(1.0)
            else:
                await asyncio.sleep(0.45)
        except Exception:
            result["failed"] += 1

    return result


async def _purge_channel_messages(
    channel: discord.TextChannel,
    *,
    amount: Optional[int],
    older_than_hours: Optional[int],
    include_pinned: bool,
    dry_run: bool,
    bot_member: Optional[discord.Member],
) -> Dict[str, Any]:
    ok, why = _can_manage_messages(bot_member, channel)
    if not ok:
        return {
            "channel_id": int(channel.id),
            "channel_name": str(channel.name),
            "matched": 0,
            "deleted": 0,
            "failed": 0,
            "error": why,
        }

    messages = await _collect_messages_for_cleanup(
        channel,
        amount=amount,
        older_than_hours=older_than_hours,
        include_pinned=include_pinned,
    )

    stats = await _delete_messages(
        channel,
        messages,
        dry_run=dry_run,
        reason="Staff-triggered channel cleanup",
    )

    return {
        "channel_id": int(channel.id),
        "channel_name": str(channel.name),
        "matched": int(stats.get("matched", 0)),
        "deleted": int(stats.get("deleted", 0)),
        "bulk_deleted": int(stats.get("bulk_deleted", 0)),
        "single_deleted": int(stats.get("single_deleted", 0)),
        "young_candidates": int(stats.get("young_candidates", 0)),
        "old_candidates": int(stats.get("old_candidates", 0)),
        "failed": int(stats.get("failed", 0)),
        "error": "",
    }


def _format_cleanup_summary(results: List[Dict[str, Any]], *, dry_run: bool) -> str:
    if not results:
        return "ℹ️ No cleanup results."

    total_matched = sum(int(r.get("matched", 0)) for r in results)
    total_deleted = sum(int(r.get("deleted", 0)) for r in results)
    total_failed = sum(int(r.get("failed", 0)) for r in results)

    lines = [
        f"{'🧪 Dry run' if dry_run else '🧹 Cleanup complete'}",
        f"Channels: `{len(results)}`",
        f"Matched: `{total_matched}`",
        f"Deleted: `{total_deleted}`",
        f"Failed: `{total_failed}`",
        "",
    ]

    for r in results[:15]:
        cid = int(r.get("channel_id", 0) or 0)
        cname = str(r.get("channel_name", "unknown"))
        err = str(r.get("error") or "").strip()
        if err:
            lines.append(f"- <#{cid}> (`{cname}`): ❌ {err}")
        else:
            lines.append(
                f"- <#{cid}> (`{cname}`): "
                f"matched `{int(r.get('matched', 0))}`, "
                f"deleted `{int(r.get('deleted', 0))}`, "
                f"failed `{int(r.get('failed', 0))}`"
            )

    if len(results) > 15:
        lines.append(f"- ...and `{len(results) - 15}` more channel(s)")

    return "\n".join(lines)


# ============================================================
# Command registration
# ============================================================
def register_channel_cleanup_admin_commands(bot: Any, tree: Any) -> None:
    global _REGISTERED

    if _REGISTERED:
        return

    @tree.command(
        name="channel_cleanup_status",
        description="(Staff) Show configured cleanup channels and cleanup worker status.",
    )
    async def channel_cleanup_status(interaction: discord.Interaction):
        if not _staff_check(interaction):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ Invalid context.", ephemeral=True)

        ids = _configured_cleanup_channel_ids()
        hours_map = _configured_hours_map()
        default_hours = _default_cleanup_hours()
        default_limit = _default_cleanup_limit()
        include_pins = _default_include_pins()

        embed = discord.Embed(
            title="🧹 Channel Cleanup Status",
            color=discord.Color.blurple(),
            timestamp=now_utc(),
        )

        embed.add_field(
            name="Worker",
            value=(
                f"Running: `{_worker_running(bot)}`\n"
                f"Default hours: `{default_hours}`\n"
                f"Default limit/run: `{default_limit}`\n"
                f"Include pinned by default: `{include_pins}`"
            ),
            inline=False,
        )

        if not ids:
            embed.add_field(
                name="Configured Channels",
                value="`None configured by ID yet.`",
                inline=False,
            )
        else:
            lines: List[str] = []
            seen: Set[int] = set()

            for cid in ids:
                if cid in seen:
                    continue
                seen.add(cid)

                ch = guild.get_channel(cid)
                hours = int(hours_map.get(cid, default_hours))

                if isinstance(ch, discord.TextChannel):
                    lines.append(f"<#{cid}> • `{ch.name}` • keep last `{hours}h`")
                else:
                    lines.append(f"`{cid}` • unresolved in this guild • keep last `{hours}h`")

            embed.add_field(
                name=f"Configured Channels ({len(lines)})",
                value="\n".join(lines[:20]) if lines else "`None`",
                inline=False,
            )

            if len(lines) > 20:
                embed.set_footer(text=f"Showing first 20 of {len(lines)} configured channels")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @tree.command(
        name="run_channel_cleanup",
        description="(Staff) Run cleanup now across all configured cleanup channels.",
    )
    @app_commands.describe(
        older_than_hours="Override message age cutoff for all configured channels",
        limit_per_channel="Max messages to delete per channel this run",
        include_pinned="Also delete pinned messages",
        dry_run="Preview only, do not delete anything",
        start_worker="Also ensure the background cleanup worker is started",
    )
    async def run_channel_cleanup(
        interaction: discord.Interaction,
        older_than_hours: Optional[app_commands.Range[int, 1, 8760]] = None,
        limit_per_channel: Optional[app_commands.Range[int, 1, 5000]] = None,
        include_pinned: Optional[bool] = None,
        dry_run: Optional[bool] = False,
        start_worker: Optional[bool] = False,
    ):
        if not _staff_check(interaction):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ Invalid context.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        if start_worker:
            try:
                started = await ensure_channel_cleanup_worker_started()
                print(f"🧹 Manual request started cleanup worker: started={started}")
            except Exception as e:
                print(f"⚠️ Failed to start cleanup worker from slash command: {repr(e)}")

        ids = _configured_cleanup_channel_ids()
        if not ids:
            return await interaction.followup.send(
                "❌ No configured cleanup channel IDs were found.",
                ephemeral=True,
            )

        hours_map = _configured_hours_map()
        default_hours = _default_cleanup_hours()
        use_limit = int(limit_per_channel or _default_cleanup_limit())
        use_include_pinned = bool(include_pinned if include_pinned is not None else _default_include_pins())

        me = guild.me
        results: List[Dict[str, Any]] = []

        for cid in ids:
            ch = await _resolve_text_channel_by_id(guild, int(cid))
            if not isinstance(ch, discord.TextChannel):
                results.append({
                    "channel_id": int(cid),
                    "channel_name": "unresolved",
                    "matched": 0,
                    "deleted": 0,
                    "failed": 0,
                    "error": "Channel not found or not a text channel.",
                })
                continue

            channel_hours = int(older_than_hours or hours_map.get(int(cid), default_hours))

            try:
                result = await _purge_channel_messages(
                    ch,
                    amount=use_limit,
                    older_than_hours=channel_hours,
                    include_pinned=use_include_pinned,
                    dry_run=bool(dry_run),
                    bot_member=me,
                )
                results.append(result)
            except Exception as e:
                results.append({
                    "channel_id": int(cid),
                    "channel_name": str(ch.name),
                    "matched": 0,
                    "deleted": 0,
                    "failed": 0,
                    "error": str(e),
                })

        await interaction.followup.send(
            _format_cleanup_summary(results, dry_run=bool(dry_run)),
            ephemeral=True,
        )

    @tree.command(
        name="purge_channel_messages",
        description="(Staff) Purge messages from a chosen text channel right now.",
    )
    @app_commands.describe(
        channel="Channel to purge (leave empty to use current channel)",
        amount="Max number of matching messages to delete",
        older_than_hours="Only delete messages older than this many hours",
        include_pinned="Also delete pinned messages",
        dry_run="Preview only, do not delete anything",
    )
    async def purge_channel_messages(
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
        amount: Optional[app_commands.Range[int, 1, 5000]] = None,
        older_than_hours: Optional[app_commands.Range[int, 1, 8760]] = None,
        include_pinned: Optional[bool] = False,
        dry_run: Optional[bool] = False,
    ):
        if not _staff_check(interaction):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ Invalid context.", ephemeral=True)

        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            return await interaction.response.send_message(
                "❌ You must run this in a text channel or provide one.",
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True)

        use_amount = int(amount or _default_cleanup_limit())
        use_hours = int(older_than_hours) if older_than_hours else None
        use_include_pinned = bool(include_pinned)

        result = await _purge_channel_messages(
            target,
            amount=use_amount,
            older_than_hours=use_hours,
            include_pinned=use_include_pinned,
            dry_run=bool(dry_run),
            bot_member=guild.me,
        )

        await interaction.followup.send(
            _format_cleanup_summary([result], dry_run=bool(dry_run)),
            ephemeral=True,
        )

    _REGISTERED = True
    try:
        print("✅ commands_ext.channel_cleanup_admin: registered channel cleanup admin commands")
    except Exception:
        pass


__all__ = [
    "register_channel_cleanup_admin_commands",
]