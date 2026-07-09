from __future__ import annotations

"""Add user-targeted purge to the existing /dank cleanup purge flow.

This intentionally does NOT add a separate /dank cleanup user-messages command.
The regular purge command gains optional user/user_id + scope fields, then shows
a preview with a button confirmation. No typed confirmation phrase.

Run from repo root:
    python tools/apply_cleanup_user_message_purge.py
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "stoney_verify/commands_ext/public_cleanup_group.py"

COMMANDS_MARKER = "# ============================================================\n# /dank cleanup commands\n# ============================================================\n"
PURGE_START = '@cleanup_group.command(name="purge"'
INVITES_START = '@cleanup_group.command(name="invites"'

HELPERS = r'''
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

'''

NEW_PURGE = r'''
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
'''


def remove_old_user_messages_command(text: str) -> str:
    start = text.find('@cleanup_group.command(name="user-messages"')
    if start < 0:
        return text
    end = text.find('\n\n@cleanup_group.command(name="invites"', start)
    if end < 0:
        raise SystemExit("Found user-messages command but could not locate following invites command")
    return text[:start] + text[end + 2:]


def remove_old_user_helpers(text: str) -> str:
    start = text.find("# User message purge helpers")
    if start < 0:
        return text
    section_start = text.rfind("# ============================================================", 0, start)
    section_end = text.find(COMMANDS_MARKER, start)
    if section_start < 0 or section_end < 0:
        raise SystemExit("Found old user-message helpers but could not remove section safely")
    return text[:section_start] + text[section_end:]


def replace_purge_command(text: str) -> str:
    start = text.find(PURGE_START)
    end = text.find(INVITES_START, start)
    if start < 0 or end < 0:
        raise SystemExit("Could not locate cleanup purge command block")
    return text[:start] + NEW_PURGE + "\n\n" + text[end:]


def main() -> None:
    text = TARGET.read_text(encoding="utf-8")
    text = remove_old_user_messages_command(text)
    text = remove_old_user_helpers(text)
    if "_cleanup_scan_user_messages_in_channel" not in text:
        if COMMANDS_MARKER not in text:
            raise SystemExit("Could not find /dank cleanup commands marker")
        text = text.replace(COMMANDS_MARKER, HELPERS + COMMANDS_MARKER, 1)
    text = replace_purge_command(text)

    forbidden = (
        '@cleanup_group.command(name="user-messages"',
        "confirm_phrase",
        "PURGE SERVER",
        "Confirmation required before deleting user messages",
    )
    remaining = [token for token in forbidden if token in text]
    if remaining:
        raise SystemExit("Old clunky user purge flow remains: " + ", ".join(remaining))

    required = (
        '@cleanup_group.command(name="purge"',
        "user=\"Optional user whose messages should be targeted\"",
        "user_id=\"Raw Discord user ID",
        "CleanupUserPurgeConfirmView",
        "No typed confirmation needed",
        "Delete Across Server",
        "Delete From Channel",
        "await msg.delete()",
    )
    missing = [token for token in required if token not in text]
    if missing:
        raise SystemExit("Button-confirm user purge missing tokens: " + ", ".join(missing))

    TARGET.write_text(text, encoding="utf-8")
    print("✅ /dank cleanup purge now supports user-targeted preview + button delete")


if __name__ == "__main__":
    main()
