from __future__ import annotations

from typing import Any, Dict, List, Tuple

import discord
from discord import app_commands

from .common import _staff_check

try:
    from ..runtime_jobs import runtime_job_stats
except Exception:
    def runtime_job_stats() -> Dict[str, dict]:
        return {}


_REGISTERED = False


def _fmt_int(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except Exception:
        return "0"


def _health_icon(stats: Dict[str, Any]) -> str:
    try:
        if int(stats.get("dropped", 0) or 0) > 0:
            return "🔴"
        if int(stats.get("timed_out", 0) or 0) > 0:
            return "🟠"
        if int(stats.get("failed", 0) or 0) > 0:
            return "🟡"
        if int(stats.get("queue_size", 0) or 0) > 0:
            return "🔵"
    except Exception:
        pass
    return "🟢"


def _sort_job_rows(snapshot: Dict[str, dict]) -> List[Tuple[str, Dict[str, Any]]]:
    rows = list(snapshot.items())

    def score(item: Tuple[str, Dict[str, Any]]) -> tuple[int, int, int, int]:
        _key, stats = item
        try:
            return (
                int(stats.get("dropped", 0) or 0),
                int(stats.get("timed_out", 0) or 0),
                int(stats.get("failed", 0) or 0),
                int(stats.get("queue_size", 0) or 0),
            )
        except Exception:
            return (0, 0, 0, 0)

    rows.sort(key=score, reverse=True)
    return rows


def _build_status_embed(*, guild_only: bool, guild_id: int | None = None) -> discord.Embed:
    snapshot = runtime_job_stats()

    if guild_only and guild_id:
        gid = str(guild_id)
        snapshot = {
            key: value
            for key, value in snapshot.items()
            if key.endswith(f":{gid}") or key == gid
        }

    embed = discord.Embed(
        title="⚙️ Runtime Job Queues",
        description=(
            "Bounded background queues used to keep Discord interactions and gateway events responsive.\n"
            "Dropped optional jobs are better than freezing the bot."
        ),
        color=discord.Color.blurple(),
    )

    if not snapshot:
        embed.add_field(
            name="Status",
            value="`No runtime jobs have been queued yet since this restart.`",
            inline=False,
        )
        return embed

    totals = {
        "queues": len(snapshot),
        "queue_size": 0,
        "enqueued": 0,
        "completed": 0,
        "failed": 0,
        "timed_out": 0,
        "dropped": 0,
        "running": 0,
    }

    for stats in snapshot.values():
        for key in totals:
            if key == "queues":
                continue
            try:
                totals[key] += int(stats.get(key, 0) or 0)
            except Exception:
                pass

    embed.add_field(
        name="Totals",
        value=(
            f"Queues: `{_fmt_int(totals['queues'])}`\n"
            f"Queued now: `{_fmt_int(totals['queue_size'])}`\n"
            f"Running: `{_fmt_int(totals['running'])}`\n"
            f"Enqueued: `{_fmt_int(totals['enqueued'])}`\n"
            f"Completed: `{_fmt_int(totals['completed'])}`\n"
            f"Timed out: `{_fmt_int(totals['timed_out'])}`\n"
            f"Failed: `{_fmt_int(totals['failed'])}`\n"
            f"Dropped: `{_fmt_int(totals['dropped'])}`"
        ),
        inline=False,
    )

    lines: List[str] = []
    for key, stats in _sort_job_rows(snapshot)[:15]:
        icon = _health_icon(stats)
        last_error = str(stats.get("last_error") or "").strip()
        last_label = str(stats.get("last_label") or "").strip()
        last_elapsed = int(stats.get("last_elapsed_ms", 0) or 0)

        line = (
            f"{icon} `{key}` q=`{_fmt_int(stats.get('queue_size'))}` "
            f"run=`{_fmt_int(stats.get('running'))}` "
            f"done=`{_fmt_int(stats.get('completed'))}` "
            f"to=`{_fmt_int(stats.get('timed_out'))}` "
            f"fail=`{_fmt_int(stats.get('failed'))}` "
            f"drop=`{_fmt_int(stats.get('dropped'))}` "
            f"last=`{last_elapsed}ms`"
        )
        if last_label:
            line += f"\n↳ {last_label[:90]}"
        if last_error:
            line += f"\n↳ ⚠️ {last_error[:120]}"
        lines.append(line)

    embed.add_field(
        name="Queues",
        value="\n".join(lines)[:3900] if lines else "`No queues.`",
        inline=False,
    )

    if len(snapshot) > 15:
        embed.set_footer(text=f"Showing 15 of {len(snapshot)} queues. Highest-risk queues listed first.")

    return embed


def register_runtime_jobs_admin_commands(bot: Any, tree: Any) -> None:
    global _REGISTERED
    if _REGISTERED:
        return

    @tree.command(
        name="runtime_jobs_status",
        description="(Staff) Show background queue health for scale hardening.",
    )
    @app_commands.describe(
        guild_only="Only show queues for this server",
    )
    async def runtime_jobs_status(
        interaction: discord.Interaction,
        guild_only: bool = True,
    ):
        if not _staff_check(interaction):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

        guild_id = int(interaction.guild.id) if interaction.guild else None
        embed = _build_status_embed(guild_only=bool(guild_only), guild_id=guild_id)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    _REGISTERED = True
    try:
        print("✅ commands_ext.runtime_jobs_admin: registered runtime job admin commands")
    except Exception:
        pass


__all__ = ["register_runtime_jobs_admin_commands"]
