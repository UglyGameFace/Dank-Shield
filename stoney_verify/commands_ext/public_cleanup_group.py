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
# User message purge helpers
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
    await reply_once(
        interaction,
        {"content": "❌ You need Discord **Manage Messages** to purge user messages.", "ephemeral": True},
    )
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


def _cleanup_user_purge_confirm_phrase(*, target_user_id: int, scope: str) -> str:
    clean_scope = str(scope or "channel").strip().lower()
    if clean_scope == "server":
        return f"PURGE SERVER {int(target_user_id)}"
    return f"PURGE {int(target_user_id)}"


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
    current_channel: Any,
) -> tuple[list[discord.TextChannel], Optional[str]]:
    clean_scope = str(scope or "channel").strip().lower()
    if clean_scope not in {"channel", "server"}:
        return [], "Scope must be `channel` or `server`."
    if clean_scope == "server":
        return list(getattr(guild, "text_channels", []) or []), None
    target = channel or current_channel
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
                await msg.delete(reason=reason)
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


def _cleanup_user_purge_summary(
    results: List[Dict[str, Any]],
    *,
    dry_run: bool,
    target_user_id: int,
    scope: str,
    confirm_phrase: str,
) -> str:
    checked = len(results)
    matched = sum(int(r.get("matched", 0) or 0) for r in results)
    would_delete = sum(int(r.get("would_delete", 0) or 0) for r in results)
    deleted = sum(int(r.get("deleted", 0) or 0) for r in results)
    failed = sum(int(r.get("failed", 0) or 0) for r in results)
    pinned = sum(int(r.get("skipped_pinned", 0) or 0) for r in results)
    skipped = sum(1 for r in results if r.get("skipped"))

    mode = "DRY RUN" if dry_run else "DELETED"
    lines = [
        f"🧹 User message purge — **{mode}**",
        f"Target: `<@{int(target_user_id)}>` (`{int(target_user_id)}`)",
        f"Scope: `{scope}`",
        f"Channels checked: `{checked}` • skipped: `{skipped}`",
        f"Matched user messages: `{matched}`",
        f"Would delete: `{would_delete}` • deleted: `{deleted}` • failed: `{failed}` • pinned skipped: `{pinned}`",
    ]

    if dry_run:
        lines.append(f"\nTo actually delete, rerun with `dry_run:false` and `confirm:{confirm_phrase}`")

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


@cleanup_group.command(name="purge", description="Purge messages from a chosen text channel right now.")
@app_commands.describe(
    channel="Channel to purge. Defaults to the current channel.",
    amount="Max number of matching messages to delete",
    older_than_hours="Only delete messages older than this many hours",
    include_pinned="Also delete pinned messages",
    dry_run="Preview only, do not delete anything",
)
async def cleanup_purge(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
    amount: Optional[app_commands.Range[int, 1, 5000]] = None,
    older_than_hours: Optional[app_commands.Range[int, 1, 8760]] = None,
    include_pinned: Optional[bool] = False,
    dry_run: Optional[bool] = False,
) -> None:
    if not await _staff_only(interaction):
        return
    guild = await _guild_or_reply(interaction)
    if guild is None:
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



@cleanup_group.command(name="user-messages", description="Preview or purge messages from one user in one channel or the whole server.")
@app_commands.describe(
    user="User to purge. Use user_id instead if they already left.",
    user_id="Raw Discord user ID, useful after the user left/kicked",
    scope="Purge one channel or every accessible text channel in this server",
    channel="Channel to purge when scope is channel. Defaults to current channel.",
    limit_per_channel="How many recent messages to scan per channel",
    include_pinned="Also delete pinned messages from this user",
    dry_run="Preview only. Defaults true for safety.",
    confirm="Required for deleting. Use the exact phrase shown by dry run.",
)
@app_commands.choices(
    scope=[
        app_commands.Choice(name="One channel", value="channel"),
        app_commands.Choice(name="Whole server", value="server"),
    ]
)
async def cleanup_user_messages(
    interaction: discord.Interaction,
    user: Optional[discord.User] = None,
    user_id: Optional[str] = None,
    scope: Optional[str] = "channel",
    channel: Optional[discord.TextChannel] = None,
    limit_per_channel: Optional[app_commands.Range[int, 1, 100000]] = 5000,
    include_pinned: Optional[bool] = False,
    dry_run: Optional[bool] = True,
    confirm: Optional[str] = None,
) -> None:
    if not await _staff_only(interaction):
        return
    if not await _require_manage_messages_native(interaction):
        return
    guild = await _guild_or_reply(interaction)
    if guild is None:
        return
    if not isinstance(interaction.user, discord.Member):
        return await reply_once(interaction, {"content": "❌ This command must be used by a server member.", "ephemeral": True})
    bot_member = guild.me
    if not isinstance(bot_member, discord.Member):
        return await reply_once(interaction, {"content": "❌ Bot member could not be resolved in this server.", "ephemeral": True})

    target_user_id, parse_error = _cleanup_parse_user_id(user, user_id)
    if parse_error:
        return await reply_once(interaction, {"content": f"❌ {parse_error}", "ephemeral": True})
    if target_user_id == int(getattr(interaction.client.user, "id", 0) or 0):
        return await reply_once(interaction, {"content": "❌ I will not purge my own messages with this tool.", "ephemeral": True})

    clean_scope = str(scope or "channel").strip().lower()
    targets, scope_error = _cleanup_user_purge_channels(
        guild,
        scope=clean_scope,
        channel=channel,
        current_channel=interaction.channel,
    )
    if scope_error:
        return await reply_once(interaction, {"content": f"❌ {scope_error}", "ephemeral": True})

    scan_limit = int(limit_per_channel or 5000)
    actual_dry_run = bool(dry_run if dry_run is not None else True)
    confirm_phrase = _cleanup_user_purge_confirm_phrase(target_user_id=target_user_id, scope=clean_scope)
    if not actual_dry_run and str(confirm or "").strip() != confirm_phrase:
        return await reply_once(
            interaction,
            {
                "content": (
                    "❌ Confirmation required before deleting user messages.\n"
                    f"Run a dry run first, then rerun with `confirm:{confirm_phrase}`."
                ),
                "ephemeral": True,
            },
        )

    await safe_defer(interaction, ephemeral=True)

    reason = f"Dank Shield user-message purge by {interaction.user} ({interaction.user.id}) target={target_user_id} scope={clean_scope}"
    results: List[Dict[str, Any]] = []
    for ch in targets:
        if not isinstance(ch, discord.TextChannel):
            continue
        skip = _cleanup_user_purge_channel_skip(
            ch,
            actor=interaction.user,
            bot_member=bot_member,
            dry_run=actual_dry_run,
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

        result = await _cleanup_scan_user_messages_in_channel(
            ch,
            target_user_id=target_user_id,
            limit=scan_limit,
            include_pinned=bool(include_pinned),
            dry_run=actual_dry_run,
            reason=reason,
        )
        results.append(result)

    summary = _cleanup_user_purge_summary(
        results,
        dry_run=actual_dry_run,
        target_user_id=target_user_id,
        scope=clean_scope,
        confirm_phrase=confirm_phrase,
    )
    await interaction.followup.send(summary, ephemeral=True)


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
