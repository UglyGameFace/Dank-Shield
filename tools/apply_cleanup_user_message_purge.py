from __future__ import annotations

"""Add /dank cleanup user-messages.

Adds a safe, guild-scoped command for purging messages by a specific user either
in one channel or across all readable text channels in the server.

Safety model:
- Staff surface plus native Manage Messages requirement for the actor.
- Bot permission checks per channel.
- Dry-run defaults on.
- Destructive runs require a confirmation phrase based on the target user ID.
- Works for users who left/kicked by accepting a raw user_id.
- Deletes individually so old user messages are not silently skipped by bulk
  delete behavior.

Run from repo root:
    python tools/apply_cleanup_user_message_purge.py
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "stoney_verify/commands_ext/public_cleanup_group.py"

HELPER_MARKER = "# ============================================================\n# /dank cleanup commands\n# ============================================================\n"
COMMAND_MARKER = "\n\n@cleanup_group.command(name=\"invites\", description=\"Scan old messages and remove blocked Discord invites only.\")"

HELPERS = r'''
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

'''

COMMAND = r'''

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
'''


def insert_once(text: str, marker: str, addition: str, token: str) -> str:
    if token in text:
        return text
    if marker not in text:
        raise SystemExit(f"Marker not found for insertion: {marker[:60]!r}")
    return text.replace(marker, addition + marker, 1)


def main() -> None:
    text = TARGET.read_text(encoding="utf-8")
    text = insert_once(text, HELPER_MARKER, HELPERS, "_cleanup_scan_user_messages_in_channel")
    text = insert_once(text, COMMAND_MARKER, COMMAND, "async def cleanup_user_messages")

    required = (
        '@cleanup_group.command(name="user-messages"',
        "_require_manage_messages_native",
        "_cleanup_user_purge_confirm_phrase",
        "PURGE SERVER",
        "user_id=\"Raw Discord user ID",
        "await msg.delete(reason=reason)",
    )
    missing = [token for token in required if token not in text]
    if missing:
        raise SystemExit("User-message purge insertion missing tokens: " + ", ".join(missing))

    TARGET.write_text(text, encoding="utf-8")
    print("✅ Added /dank cleanup user-messages")


if __name__ == "__main__":
    main()
