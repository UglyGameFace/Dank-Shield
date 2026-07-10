from __future__ import annotations

"""Public /dank cleanup command family.

Boring/professional command strategy:
- No top-level cleanup command spam.
- Cleanup admin utilities live under /dank cleanup ...
- The old channel_cleanup_admin module can remain as a legacy fallback, but it
  does not need to be loaded for normal public startup.
"""

from typing import Any, Dict, List, Optional

import discord
from discord import app_commands

from ..globals import now_utc
from .common import _staff_check, reply_once, safe_defer
from .public_setup_group import dank_group

try:
    from ..channel_cleanup import ensure_channel_cleanup_worker_started
except Exception:
    async def ensure_channel_cleanup_worker_started() -> bool:  # type: ignore
        return False

try:
    from .channel_cleanup_admin import (
        _configured_cleanup_channel_ids,
        _configured_hours_map,
        _default_cleanup_hours,
        _default_cleanup_limit,
        _default_include_pins,
        _format_cleanup_summary,
        _purge_channel_messages,
        _resolve_text_channel_by_id,
        _worker_running,
    )
except Exception:
    _configured_cleanup_channel_ids = None  # type: ignore
    _configured_hours_map = None  # type: ignore
    _default_cleanup_hours = None  # type: ignore
    _default_cleanup_limit = None  # type: ignore
    _default_include_pins = None  # type: ignore
    _format_cleanup_summary = None  # type: ignore
    _purge_channel_messages = None  # type: ignore
    _resolve_text_channel_by_id = None  # type: ignore
    _worker_running = None  # type: ignore


_REGISTERED = False

cleanup_group = app_commands.Group(
    name="cleanup",
    description="Channel cleanup and purge tools.",
)


# ============================================================
# Helpers
# ============================================================

def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return default
        return int(str(value).strip())
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        raw = str(value or "").strip().lower()
        if raw in {"1", "true", "yes", "y", "on"}:
            return True
        if raw in {"0", "false", "no", "n", "off"}:
            return False
        return default
    except Exception:
        return default


def _truncate(value: Any, limit: int = 1800) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _helpers_ready() -> bool:
    return bool(
        _configured_cleanup_channel_ids
        and _configured_hours_map
        and _default_cleanup_hours
        and _default_cleanup_limit
        and _default_include_pins
        and _format_cleanup_summary
        and _purge_channel_messages
        and _resolve_text_channel_by_id
    )


async def _staff_only(interaction: discord.Interaction) -> bool:
    if _staff_check(interaction):
        return True
    await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
    return False


async def _guild_or_reply(interaction: discord.Interaction) -> Optional[discord.Guild]:
    guild = interaction.guild
    if guild is None:
        await reply_once(interaction, {"content": "❌ This command must be used inside a server.", "ephemeral": True})
        return None
    return guild




# ============================================================
# User-targeted purge helpers
# ============================================================

async def _require_manage_messages_native(interaction: discord.Interaction) -> bool:
    """Require real Discord Manage Messages/Admin, not staff role alone."""

    member = interaction.user
    if not isinstance(member, discord.Member):
        await reply_once(interaction, {"content": "❌ This command must be used by a server member.", "ephemeral": True})
        return False
    perms = getattr(member, "guild_permissions", None)
    if bool(getattr(perms, "administrator", False)) or bool(getattr(perms, "manage_messages", False)):
        return True
    await reply_once(interaction, {"content": "❌ You need Discord **Manage Messages** to purge user messages.", "ephemeral": True})
    return False


def _cleanup_parse_user_id(user: Optional[discord.User], user_id: Optional[str]) -> tuple[int, Optional[str]]:
    picked = int(getattr(user, "id", 0) or 0) if user is not None else 0
    raw = str(user_id or "").strip()
    cleaned = raw.replace("<@", "").replace("!", "").replace(">", "").strip()
    typed = int(cleaned) if cleaned.isdigit() else 0
    if picked and typed and picked != typed:
        return 0, "The selected user and typed user_id do not match."
    target_id = picked or typed
    if target_id <= 0:
        return 0, "Pick a user or provide a numeric user_id."
    return int(target_id), None


def _cleanup_user_purge_can_read(perms: discord.Permissions) -> bool:
    return bool(getattr(perms, "view_channel", False) and getattr(perms, "read_message_history", False))


def _cleanup_user_purge_channel_skip(
    channel: discord.TextChannel,
    *,
    actor: discord.Member,
    bot_member: discord.Member,
    dry_run: bool,
) -> Optional[str]:
    actor_perms = channel.permissions_for(actor)
    bot_perms = channel.permissions_for(bot_member)
    if not _cleanup_user_purge_can_read(actor_perms):
        return "actor missing View Channel or Read Message History"
    if not _cleanup_user_purge_can_read(bot_perms):
        return "bot missing View Channel or Read Message History"
    if not dry_run and not bool(getattr(actor_perms, "manage_messages", False) or getattr(actor.guild_permissions, "administrator", False)):
        return "actor missing Manage Messages in this channel"
    if not dry_run and not bool(getattr(bot_perms, "manage_messages", False)):
        return "bot missing Manage Messages in this channel"
    return None


def _cleanup_user_purge_channels(
    guild: discord.Guild,
    *,
    scope: str,
    channel: Optional[discord.TextChannel],
    channel_id: Optional[int] = None,
    current_channel: Any = None,
) -> tuple[list[discord.TextChannel], Optional[str]]:
    clean_scope = str(scope or "channel").strip().lower()
    if clean_scope not in {"channel", "server"}:
        return [], "Scope must be `channel` or `server`."
    if clean_scope == "server":
        return list(getattr(guild, "text_channels", []) or []), None
    target: Any = channel
    if target is None and channel_id:
        target = guild.get_channel(int(channel_id))
    if target is None:
        target = current_channel
    if not isinstance(target, discord.TextChannel):
        return [], "Pick a text channel or run this inside a text channel."
    return [target], None


async def _cleanup_scan_user_messages_in_channel(
    channel: discord.TextChannel,
    *,
    target_user_id: int,
    limit: int,
    include_pinned: bool,
    dry_run: bool,
    reason: str,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "channel_id": int(getattr(channel, "id", 0) or 0),
        "channel_name": str(getattr(channel, "name", "unknown")),
        "matched": 0,
        "would_delete": 0,
        "deleted": 0,
        "failed": 0,
        "skipped_pinned": 0,
        "sample": [],
        "error": "",
    }

    try:
        async for msg in channel.history(limit=int(limit)):
            author = getattr(msg, "author", None)
            if int(getattr(author, "id", 0) or 0) != int(target_user_id):
                continue

            result["matched"] = int(result.get("matched", 0) or 0) + 1
            if bool(getattr(msg, "pinned", False)) and not include_pinned:
                result["skipped_pinned"] = int(result.get("skipped_pinned", 0) or 0) + 1
                continue

            result["would_delete"] = int(result.get("would_delete", 0) or 0) + 1
            if len(result["sample"]) < 3:
                created = getattr(msg, "created_at", None)
                stamp = created.isoformat() if created else "unknown-time"
                content = _truncate(getattr(msg, "content", "") or "[embed/attachment/no text]", 90)
                result["sample"].append(f"#{channel.name} • {stamp} • {content}")

            if dry_run:
                continue

            try:
                await msg.delete()
                result["deleted"] = int(result.get("deleted", 0) or 0) + 1
                if int(result["deleted"]) % 10 == 0:
                    import asyncio
                    await asyncio.sleep(1.0)
            except discord.NotFound:
                result["deleted"] = int(result.get("deleted", 0) or 0) + 1
            except Exception as e:
                result["failed"] = int(result.get("failed", 0) or 0) + 1
                if not result.get("error"):
                    result["error"] = _truncate(e, 240)
    except Exception as e:
        result["error"] = _truncate(e, 240)

    return result


async def _cleanup_execute_user_purge(
    interaction: discord.Interaction,
    *,
    target_user_id: int,
    scope: str,
    channel_id: Optional[int],
    limit_per_channel: int,
    include_pinned: bool,
    dry_run: bool,
) -> tuple[str, int]:
    guild = interaction.guild
    if guild is None or not isinstance(interaction.user, discord.Member) or not isinstance(guild.me, discord.Member):
        return "❌ Guild/member context is missing.", 0

    targets, scope_error = _cleanup_user_purge_channels(
        guild,
        scope=scope,
        channel=None,
        channel_id=channel_id,
        current_channel=interaction.channel,
    )
    if scope_error:
        return f"❌ {scope_error}", 0

    reason = f"Dank Shield user purge by {interaction.user} ({interaction.user.id}) target={target_user_id} scope={scope}"
    results: List[Dict[str, Any]] = []
    for ch in targets:
        if not isinstance(ch, discord.TextChannel):
            continue
        skip = _cleanup_user_purge_channel_skip(
            ch,
            actor=interaction.user,
            bot_member=guild.me,
            dry_run=dry_run,
        )
        if skip:
            results.append({
                "channel_id": int(getattr(ch, "id", 0) or 0),
                "channel_name": str(getattr(ch, "name", "unknown")),
                "matched": 0,
                "would_delete": 0,
                "deleted": 0,
                "failed": 0,
                "skipped_pinned": 0,
                "skipped": skip,
                "sample": [],
            })
            continue
        results.append(await _cleanup_scan_user_messages_in_channel(
            ch,
            target_user_id=target_user_id,
            limit=limit_per_channel,
            include_pinned=include_pinned,
            dry_run=dry_run,
            reason=reason,
        ))

    summary = _cleanup_user_purge_summary(results, dry_run=dry_run, target_user_id=target_user_id, scope=scope)
    would_delete = sum(int(r.get("would_delete", 0) or 0) for r in results)
    return summary, would_delete


def _cleanup_user_purge_summary(
    results: List[Dict[str, Any]],
    *,
    dry_run: bool,
    target_user_id: int,
    scope: str,
) -> str:
    checked = len(results)
    matched = sum(int(r.get("matched", 0) or 0) for r in results)
    would_delete = sum(int(r.get("would_delete", 0) or 0) for r in results)
    deleted = sum(int(r.get("deleted", 0) or 0) for r in results)
    failed = sum(int(r.get("failed", 0) or 0) for r in results)
    pinned = sum(int(r.get("skipped_pinned", 0) or 0) for r in results)
    skipped = sum(1 for r in results if r.get("skipped"))
    mode = "PREVIEW" if dry_run else "DELETED"
    lines = [
        f"🧹 User purge — **{mode}**",
        f"Target: `<@{int(target_user_id)}>` (`{int(target_user_id)}`)",
        f"Scope: `{scope}`",
        f"Channels checked: `{checked}` • skipped: `{skipped}`",
        f"Matched user messages: `{matched}`",
        f"Would delete: `{would_delete}` • deleted: `{deleted}` • failed: `{failed}` • pinned skipped: `{pinned}`",
    ]
    if dry_run:
        lines.append("\nReview this preview, then use the button below. No typed confirmation needed.")

    samples: List[str] = []
    for result in results:
        if result.get("skipped") and len(samples) < 8:
            samples.append(f"⚠️ #{result.get('channel_name', 'unknown')}: {result.get('skipped')}")
            continue
        for sample in list(result.get("sample") or []):
            if len(samples) >= 8:
                break
            samples.append(f"🧪 {sample}" if dry_run else f"🗑️ {sample}")
        if result.get("error") and len(samples) < 8:
            samples.append(f"❌ #{result.get('channel_name', 'unknown')}: {result.get('error')}")
    if samples:
        lines.append("\nSamples / notes:")
        lines.extend(samples[:8])
    return _truncate("\n".join(lines), 1900)


class CleanupUserPurgeConfirmView(discord.ui.View):
    def __init__(
        self,
        *,
        target_user_id: int,
        scope: str,
        channel_id: Optional[int],
        limit_per_channel: int,
        include_pinned: bool,
        can_delete: bool,
    ) -> None:
        super().__init__(timeout=300)
        self.target_user_id = int(target_user_id)
        self.scope = str(scope or "channel").strip().lower()
        self.channel_id = int(channel_id or 0) or None
        self.limit_per_channel = int(limit_per_channel or 5000)
        self.include_pinned = bool(include_pinned)
        self.delete_matched.disabled = not bool(can_delete)
        self.delete_matched.label = "Delete Across Server" if self.scope == "server" else "Delete From Channel"

    @discord.ui.button(label="Delete Matched Messages", emoji="🧹", style=discord.ButtonStyle.danger, custom_id="dank_cleanup:user_purge:delete", row=0)
    async def delete_matched(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _staff_only(interaction):
            return
        if not await _require_manage_messages_native(interaction):
            return
        try:
            for child in self.children:
                child.disabled = True  # type: ignore[attr-defined]
            if not interaction.response.is_done():
                await interaction.response.edit_message(view=self)
        except Exception:
            if not interaction.response.is_done():
                await safe_defer(interaction, ephemeral=True)
        if not interaction.response.is_done():
            await safe_defer(interaction, ephemeral=True)

        summary, _count = await _cleanup_execute_user_purge(
            interaction,
            target_user_id=self.target_user_id,
            scope=self.scope,
            channel_id=self.channel_id,
            limit_per_channel=self.limit_per_channel,
            include_pinned=self.include_pinned,
            dry_run=False,
        )
        await interaction.followup.send(summary, ephemeral=True)

    @discord.ui.button(label="Cancel", emoji="✖️", style=discord.ButtonStyle.secondary, custom_id="dank_cleanup:user_purge:cancel", row=0)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        try:
            for child in self.children:
                child.disabled = True  # type: ignore[attr-defined]
            await interaction.response.edit_message(content="Cancelled user purge preview.", view=self)
        except Exception:
            await reply_once(interaction, {"content": "Cancelled user purge preview.", "ephemeral": True})

# ============================================================
# /dank cleanup commands
# ============================================================

@cleanup_group.command(name="status", description="Show configured cleanup channels and cleanup worker status.")
async def cleanup_status(interaction: discord.Interaction) -> None:
    if not await _staff_only(interaction):
        return
    guild = await _guild_or_reply(interaction)
    if guild is None:
        return

    if not _helpers_ready():
        return await reply_once(interaction, {"content": "❌ Cleanup helpers are unavailable.", "ephemeral": True})

    ids = _configured_cleanup_channel_ids()  # type: ignore[misc]
    hours_map = _configured_hours_map()  # type: ignore[misc]
    default_hours = _default_cleanup_hours()  # type: ignore[misc]
    default_limit = _default_cleanup_limit()  # type: ignore[misc]
    include_pins = _default_include_pins()  # type: ignore[misc]

    embed = discord.Embed(
        title="🧹 Channel Cleanup Status",
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.add_field(
        name="Worker",
        value=(
            f"Running: `{_worker_running(interaction.client) if _worker_running else False}`\n"
            f"Default hours: `{default_hours}`\n"
            f"Default limit/run: `{default_limit}`\n"
            f"Include pinned by default: `{include_pins}`"
        ),
        inline=False,
    )

    if not ids:
        embed.add_field(name="Configured Channels", value="`None configured by ID yet.`", inline=False)
    else:
        lines: List[str] = []
        seen: set[int] = set()
        for cid in ids:
            cid_int = _safe_int(cid, 0)
            if cid_int <= 0 or cid_int in seen:
                continue
            seen.add(cid_int)
            ch = guild.get_channel(cid_int)
            hours = int(hours_map.get(cid_int, default_hours))
            if isinstance(ch, discord.TextChannel):
                lines.append(f"<#{cid_int}> • `{ch.name}` • keep last `{hours}h`")
            else:
                lines.append(f"`{cid_int}` • unresolved in this guild • keep last `{hours}h`")
        embed.add_field(name=f"Configured Channels ({len(lines)})", value="\n".join(lines[:20]) if lines else "`None`", inline=False)
        if len(lines) > 20:
            embed.set_footer(text=f"Showing first 20 of {len(lines)} configured channels")

    await reply_once(interaction, {"embed": embed, "ephemeral": True})


@cleanup_group.command(name="run", description="Run cleanup now across all configured cleanup channels.")
@app_commands.describe(
    older_than_hours="Override message age cutoff for all configured channels",
    limit_per_channel="Max messages to delete per channel this run",
    include_pinned="Also delete pinned messages",
    dry_run="Preview only, do not delete anything",
    start_worker="Also ensure the background cleanup worker is started",
)
async def cleanup_run(
    interaction: discord.Interaction,
    older_than_hours: Optional[app_commands.Range[int, 1, 8760]] = None,
    limit_per_channel: Optional[app_commands.Range[int, 1, 5000]] = None,
    include_pinned: Optional[bool] = None,
    dry_run: Optional[bool] = False,
    start_worker: Optional[bool] = False,
) -> None:
    if not await _staff_only(interaction):
        return
    guild = await _guild_or_reply(interaction)
    if guild is None:
        return

    if not _helpers_ready():
        return await reply_once(interaction, {"content": "❌ Cleanup helpers are unavailable.", "ephemeral": True})

    await safe_defer(interaction, ephemeral=True)

    if start_worker:
        try:
            started = await ensure_channel_cleanup_worker_started()
            try:
                print(f"🧹 Manual /dank cleanup run started cleanup worker: started={started}")
            except Exception:
                pass
        except Exception:
            pass

    ids = _configured_cleanup_channel_ids()  # type: ignore[misc]
    if not ids:
        return await interaction.followup.send("❌ No configured cleanup channel IDs were found.", ephemeral=True)

    hours_map = _configured_hours_map()  # type: ignore[misc]
    default_hours = _default_cleanup_hours()  # type: ignore[misc]
    use_limit = int(limit_per_channel or _default_cleanup_limit())  # type: ignore[misc]
    use_include_pinned = bool(include_pinned if include_pinned is not None else _default_include_pins())  # type: ignore[misc]

    results: List[Dict[str, Any]] = []
    for cid in ids:
        cid_int = _safe_int(cid, 0)
        if cid_int <= 0:
            continue
        ch = await _resolve_text_channel_by_id(guild, cid_int)  # type: ignore[misc]
        if not isinstance(ch, discord.TextChannel):
            results.append({
                "channel_id": cid_int,
                "channel_name": "unresolved",
                "matched": 0,
                "deleted": 0,
                "failed": 0,
                "error": "Channel not found or not a text channel.",
            })
            continue

        channel_hours = int(older_than_hours or hours_map.get(cid_int, default_hours))
        try:
            result = await _purge_channel_messages(  # type: ignore[misc]
                ch,
                amount=use_limit,
                older_than_hours=channel_hours,
                include_pinned=use_include_pinned,
                dry_run=bool(dry_run),
                bot_member=guild.me,
            )
            results.append(result)
        except Exception as e:
            results.append({
                "channel_id": cid_int,
                "channel_name": str(getattr(ch, "name", "unknown")),
                "matched": 0,
                "deleted": 0,
                "failed": 0,
                "error": _truncate(e, 300),
            })

    await interaction.followup.send(_truncate(_format_cleanup_summary(results, dry_run=bool(dry_run)), 1900), ephemeral=True)  # type: ignore[misc]




@cleanup_group.command(name="purge", description="Purge messages from a channel, or preview/delete one user's messages.")
@app_commands.describe(
    channel="Channel to purge. Defaults to the current channel.",
    amount="Max number of matching messages to delete or scan per channel",
    older_than_hours="Only delete messages older than this many hours. Ignored for user-target purge.",
    include_pinned="Also delete pinned messages",
    dry_run="For normal purge: preview only. User-target purge always previews first.",
    user="Optional user whose messages should be targeted",
    user_id="Raw Discord user ID, useful after the user left/kicked",
    scope="For user-target purge: this channel or the whole server",
)
@app_commands.choices(
    scope=[
        app_commands.Choice(name="This channel", value="channel"),
        app_commands.Choice(name="Whole server", value="server"),
    ]
)
async def cleanup_purge(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
    amount: Optional[app_commands.Range[int, 1, 100000]] = None,
    older_than_hours: Optional[app_commands.Range[int, 1, 8760]] = None,
    include_pinned: Optional[bool] = False,
    dry_run: Optional[bool] = False,
    user: Optional[discord.User] = None,
    user_id: Optional[str] = None,
    scope: Optional[str] = "channel",
) -> None:
    if not await _staff_only(interaction):
        return
    guild = await _guild_or_reply(interaction)
    if guild is None:
        return

    target_user_id, parse_error = _cleanup_parse_user_id(user, user_id) if (user is not None or str(user_id or "").strip()) else (0, None)
    if parse_error:
        return await reply_once(interaction, {"content": f"❌ {parse_error}", "ephemeral": True})

    if target_user_id:
        if not await _require_manage_messages_native(interaction):
            return
        if not isinstance(guild.me, discord.Member):
            return await reply_once(interaction, {"content": "❌ Bot member could not be resolved in this server.", "ephemeral": True})
        clean_scope = str(scope or "channel").strip().lower()
        targets, scope_error = _cleanup_user_purge_channels(
            guild,
            scope=clean_scope,
            channel=channel,
            current_channel=interaction.channel,
        )
        if scope_error:
            return await reply_once(interaction, {"content": f"❌ {scope_error}", "ephemeral": True})
        preview_channel_id = int(getattr(targets[0], "id", 0) or 0) if clean_scope == "channel" and targets else None
        await safe_defer(interaction, ephemeral=True)
        summary, count = await _cleanup_execute_user_purge(
            interaction,
            target_user_id=target_user_id,
            scope=clean_scope,
            channel_id=preview_channel_id,
            limit_per_channel=int(amount or 5000),
            include_pinned=bool(include_pinned),
            dry_run=True,
        )
        await interaction.followup.send(
            summary,
            ephemeral=True,
            view=CleanupUserPurgeConfirmView(
                target_user_id=target_user_id,
                scope=clean_scope,
                channel_id=preview_channel_id,
                limit_per_channel=int(amount or 5000),
                include_pinned=bool(include_pinned),
                can_delete=count > 0,
            ),
        )
        return

    if not _helpers_ready():
        return await reply_once(interaction, {"content": "❌ Cleanup helpers are unavailable.", "ephemeral": True})

    target = channel or interaction.channel
    if not isinstance(target, discord.TextChannel):
        return await reply_once(interaction, {"content": "❌ You must run this in a text channel or provide one.", "ephemeral": True})

    await safe_defer(interaction, ephemeral=True)

    use_amount = int(amount or _default_cleanup_limit())  # type: ignore[misc]
    use_hours = int(older_than_hours) if older_than_hours else None

    try:
        result = await _purge_channel_messages(  # type: ignore[misc]
            target,
            amount=use_amount,
            older_than_hours=use_hours,
            include_pinned=bool(include_pinned),
            dry_run=bool(dry_run),
            bot_member=guild.me,
        )
        await interaction.followup.send(_truncate(_format_cleanup_summary([result], dry_run=bool(dry_run)), 1900), ephemeral=True)  # type: ignore[misc]
    except Exception as e:
        await interaction.followup.send(f"❌ Purge failed: `{_truncate(e, 300)}`", ephemeral=True)


@cleanup_group.command(name="invites", description="Scan old messages and remove blocked Discord invites only.")
@app_commands.describe(
    channel="Channel to scan. Defaults to the current channel.",
    amount="Recent messages to check per channel",
    all_text_channels="Scan every text channel the bot can read",
    dry_run="Preview only without deleting",
)
async def cleanup_invites(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
    amount: Optional[app_commands.Range[int, 1, 1000]] = 500,
    all_text_channels: Optional[bool] = False,
    dry_run: Optional[bool] = True,
) -> None:
    if not await _staff_only(interaction):
        return

    guild = await _guild_or_reply(interaction)
    if guild is None:
        return

    await safe_defer(interaction, ephemeral=True)

    bot_member = guild.me
    if bot_member is None:
        return await interaction.followup.send("❌ Bot member could not be resolved in this server.", ephemeral=True)

    from stoney_verify.invite_policy_engine import (
        decide_invite_message,
        delete_message_if_allowed,
        extract_invite_codes_from_message,
    )

    use_amount = int(amount or 500)
    targets: list[discord.TextChannel] = []

    if all_text_channels:
        targets = list(getattr(guild, "text_channels", []) or [])
    else:
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            return await interaction.followup.send("❌ Pick a text channel or run this inside a text channel.", ephemeral=True)
        targets = [target]

    checked_channels = 0
    checked_messages = 0
    invite_messages = 0
    would_delete = 0
    deleted = 0
    failed = 0
    allowed = 0
    skipped_perm = 0
    sample_lines: list[str] = []

    for ch in targets:
        if not isinstance(ch, discord.TextChannel):
            continue

        perms = ch.permissions_for(bot_member)
        if not perms.view_channel or not perms.read_message_history:
            skipped_perm += 1
            sample_lines.append(f"⚠️ #{ch.name}: missing View Channel or Read Message History")
            continue
        if not dry_run and not perms.manage_messages:
            skipped_perm += 1
            sample_lines.append(f"⚠️ #{ch.name}: missing Manage Messages")
            continue

        checked_channels += 1

        try:
            async for msg in ch.history(limit=use_amount):
                checked_messages += 1

                try:
                    if getattr(msg, "author", None) and getattr(interaction.client, "user", None):
                        if int(getattr(msg.author, "id", 0) or 0) == int(getattr(interaction.client.user, "id", 0) or 0):
                            continue
                except Exception:
                    pass

                codes = extract_invite_codes_from_message(msg)
                if not codes:
                    continue

                invite_messages += 1
                decision = await decide_invite_message(
                    msg,
                    source="history_invite_cleanup",
                    refresh_policy=True,
                )

                if decision.should_delete:
                    would_delete += 1
                    if dry_run:
                        if len(sample_lines) < 12:
                            sample_lines.append(
                                f"🧪 #{ch.name}: would delete `{','.join(decision.codes[:3])}` "
                                f"rule={decision.rule_id}"
                            )
                        continue

                    ok = await delete_message_if_allowed(msg, decision)
                    if ok:
                        deleted += 1
                        if len(sample_lines) < 12:
                            sample_lines.append(
                                f"🗑️ #{ch.name}: deleted `{','.join(decision.codes[:3])}` "
                                f"rule={decision.rule_id}"
                            )
                    else:
                        failed += 1
                        if len(sample_lines) < 12:
                            sample_lines.append(
                                f"❌ #{ch.name}: failed `{','.join(decision.codes[:3])}` "
                                f"error={decision.delete_error or 'unknown'}"
                            )
                else:
                    allowed += 1
                    if len(sample_lines) < 12:
                        sample_lines.append(
                            f"✅ #{ch.name}: allowed `{','.join(decision.codes[:3])}` "
                            f"rule={decision.rule_id}"
                        )

        except Exception as e:
            failed += 1
            if len(sample_lines) < 12:
                sample_lines.append(f"❌ #{ch.name}: scan failed {type(e).__name__}: {_truncate(e, 180)}")

    mode = "DRY RUN" if dry_run else "DELETED"
    summary = (
        f"🧹 Invite history cleanup — {mode}\n"
        f"Channels checked: `{checked_channels}`\n"
        f"Messages checked: `{checked_messages}`\n"
        f"Messages with Discord invites: `{invite_messages}`\n"
        f"Would delete: `{would_delete}`\n"
        f"Deleted: `{deleted}`\n"
        f"Allowed/internal/exempt: `{allowed}`\n"
        f"Failed: `{failed}`\n"
        f"Skipped permission: `{skipped_perm}`"
    )

    if sample_lines:
        summary += "\n\n" + "\n".join(sample_lines[:12])

    try:
        print(
            "🧹 invite_history_cleanup "
            f"guild={guild.id} dry_run={dry_run} all_channels={all_text_channels} "
            f"channels={checked_channels} messages={checked_messages} "
            f"invite_messages={invite_messages} would_delete={would_delete} "
            f"deleted={deleted} failed={failed} skipped_perm={skipped_perm}"
        )
    except Exception:
        pass

    await interaction.followup.send(_truncate(summary, 1900), ephemeral=True)


# ============================================================
# Registration
# ============================================================

def register_public_cleanup_group_commands(bot: Any, tree: Any) -> None:
    global _REGISTERED
    _ = bot, tree
    if _REGISTERED:
        return

    try:
        if dank_group.get_command("cleanup") is None:
            dank_group.add_command(cleanup_group)
            print("✅ public_cleanup_group: attached /dank cleanup commands")
        else:
            print("✅ public_cleanup_group: /dank cleanup already attached")
    except Exception as e:
        print(f"⚠️ public_cleanup_group failed attaching /dank cleanup: {repr(e)}")
        raise

    _REGISTERED = True


__all__ = ["register_public_cleanup_group_commands", "cleanup_group"]


# ============================================================
# DM spam / DM raider report flow
# ============================================================

_DM_RAIDER_REPORT_COUNTS = globals().get("_DM_RAIDER_REPORT_COUNTS", {})


def _dm_raider_report_key(guild_id, target_user_id):
    return (int(guild_id), int(target_user_id))


def _dm_raider_increment_report(guild_id, target_user_id):
    key = _dm_raider_report_key(guild_id, target_user_id)
    _DM_RAIDER_REPORT_COUNTS[key] = int(_DM_RAIDER_REPORT_COUNTS.get(key, 0) or 0) + 1
    return int(_DM_RAIDER_REPORT_COUNTS[key])


def _dm_raider_staff_channel(guild, fallback):
    try:
        from stoney_verify.modlog import _get_modlog_channel
        ch = _get_modlog_channel(guild)
        if isinstance(ch, discord.TextChannel):
            return ch
    except Exception:
        pass
    return fallback if isinstance(fallback, discord.TextChannel) else None


def _dm_raider_can_staff_action(member):
    try:
        perms = getattr(member, "guild_permissions", None)
        return bool(
            getattr(perms, "administrator", False)
            or getattr(perms, "ban_members", False)
            or getattr(perms, "manage_messages", False)
        )
    except Exception:
        return False


class DmRaiderReportActionView(discord.ui.View):
    def __init__(self, *, target_user_id, report_count):
        super().__init__(timeout=900)
        self.target_user_id = int(target_user_id)
        self.report_count = int(report_count)

    async def interaction_check(self, interaction):
        if not _dm_raider_can_staff_action(interaction.user):
            await interaction.response.send_message(
                "❌ Staff action required. You need Ban Members, Manage Messages, or Administrator.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Ban by ID", emoji="🔨", style=discord.ButtonStyle.danger, custom_id="dank:dm_report:v1:ban_by_id")
    async def ban_by_id(self, interaction, button):
        _ = button
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ Guild context missing.", ephemeral=True)

        member = interaction.user
        perms = getattr(member, "guild_permissions", None)
        if not (getattr(perms, "administrator", False) or getattr(perms, "ban_members", False)):
            return await interaction.response.send_message("❌ You need **Ban Members** to ban by ID.", ephemeral=True)

        try:
            await guild.ban(
                discord.Object(id=int(self.target_user_id)),
                reason=f"Dank Shield DM spam report action by {interaction.user} ({interaction.user.id}); reports={self.report_count}",
                delete_message_days=0,
            )
            await interaction.response.send_message(
                f"🔨 Banned `<@{self.target_user_id}>` by user ID from DM spam report.",
                ephemeral=True,
            )
        except discord.Forbidden:
            await interaction.response.send_message("❌ Ban failed: I need Ban Members and a role high enough to ban this user.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Ban failed: `{type(e).__name__}: {e}`", ephemeral=True)

    @discord.ui.button(label="Purge User Messages", emoji="🧹", style=discord.ButtonStyle.secondary, custom_id="dank:dm_report:v1:purge_hint")
    async def purge_hint(self, interaction, button):
        _ = button
        await interaction.response.send_message(
            "Run a fresh purge preview, then use its delete button:\n"
            f"`/dank cleanup purge user_id:{self.target_user_id} scope:Whole server dry_run:true`\n\n"
            "This keeps DM reports separate from message deletion so the bot does not silently nuke channels.",
            ephemeral=True,
        )


@cleanup_group.command(name="report-dm-spam", description="Report a member/user who DM-spammed or sent NSFW/scam DMs.")
@app_commands.describe(
    target_user_id="Raw Discord user ID of the suspected DM spammer/raider",
    evidence="Short note: what happened, screenshot link, or what users reported",
)
async def cleanup_report_dm_spam(interaction: discord.Interaction, target_user_id: str, evidence: str = ""):
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This only works inside a server.", ephemeral=True)

    raw = str(target_user_id or "").replace("<@", "").replace("!", "").replace(">", "").strip()
    if not raw.isdigit():
        return await interaction.response.send_message("❌ Provide a raw numeric Discord user ID.", ephemeral=True)

    target_id = int(raw)
    if target_id <= 0:
        return await interaction.response.send_message("❌ Invalid user ID.", ephemeral=True)

    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    report_count = _dm_raider_increment_report(int(guild.id), target_id)
    staff_channel = _dm_raider_staff_channel(guild, interaction.channel)

    embed = discord.Embed(
        title="🚩 DM Raider Report",
        description=(
            "A member/staff report says this user may be sending unwanted DMs, NSFW images, scams, or raid spam.\n\n"
            "**Important:** Dank Shield cannot read private DMs. This is report-based evidence, not private-message surveillance."
        ),
        color=discord.Color.orange(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Target", value=f"<@{target_id}> (`{target_id}`)", inline=False)
    embed.add_field(name="Reporter", value=f"{interaction.user.mention} (`{interaction.user.id}`)", inline=False)
    embed.add_field(name="Report count this runtime", value=f"`{report_count}`", inline=True)
    embed.add_field(name="Official Bot", value="Unknown from report alone", inline=True)
    embed.add_field(name="DM Raider Report Risk", value="Report evidence exists — staff review required", inline=False)
    embed.add_field(
        name="Evidence / note",
        value=(str(evidence or "No note provided.")[:900]),
        inline=False,
    )
    embed.add_field(
        name="Recommended next steps",
        value=(
            "1. Review screenshot/user reports.\n"
            "2. Ban by ID if confirmed.\n"
            "3. Run user-message purge if they also posted in-server.\n"
            "4. Check invite/source reputation if multiple reports came from the same invite."
        ),
        inline=False,
    )
    embed.set_footer(text="Dank Shield DM report flow • report evidence, not DM reading")

    if staff_channel is None:
        return await interaction.followup.send("❌ No staff/modlog channel was available for the DM report.", ephemeral=True)

    try:
        await staff_channel.send(embed=embed, view=DmRaiderReportActionView(target_user_id=target_id, report_count=report_count))
    except Exception as e:
        return await interaction.followup.send(f"❌ Could not post DM report: `{type(e).__name__}: {e}`", ephemeral=True)

    await interaction.followup.send(
        f"✅ DM spam report sent to {staff_channel.mention}. Staff can ban by ID or start a purge from that card.",
        ephemeral=True,
    )
