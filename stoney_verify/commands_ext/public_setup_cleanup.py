from __future__ import annotations

"""Selective setup cleanup for rushed/clumsy server owners.

This patches /stoney setup -> Recovery / Start Over with cleanup options that
can remove one thing, only setup channels, only setup roles, only empty setup
categories, or all detected bot-created setup items.

Safety rules:
- Exact default setup names only. No guessing custom channels/roles.
- Preview before destructive cleanup.
- Confirmation modal required.
- Ticket-looking channels are skipped.
- Categories are deleted only when empty or containing only detected setup children.
- Saved config references to deleted Discord IDs are cleared.
"""

import asyncio
from dataclasses import dataclass
from typing import Any, Iterable, Optional

import discord

from ..globals import now_utc
from ..guild_config import invalidate_guild_config
from . import public_setup_recovery as recovery
from . import public_setup_solid as solid

_PATCHED = False
_ORIGINAL_RECOVERY_EMBED = None

DEFAULT_ROLE_NAMES = ("Bot Manager", "Support Team", "Unverified", "Verified", "Member")
DEFAULT_CATEGORY_NAMES = ("👋 START HERE", "🎫 ACTIVE TICKETS", "📦 TICKET ARCHIVE", "🛠️ STAFF TOOLS")
DEFAULT_TEXT_CHANNEL_NAMES = (
    "👋・welcome",
    "✅・verify",
    "🎫・support",
    "🎙️・vc-verify-queue",
    "📑・transcripts",
    "🛡️・mod-log",
    "🚪・join-leave-log",
    "📡・bot-status",
)
DEFAULT_VOICE_CHANNEL_NAMES = ("🎙️ Voice Verification",)
PROTECTED_NAME_PARTS = ("ticket-", "closed-", "transcript-")


@dataclass(frozen=True)
class CleanupCandidate:
    kind: str
    object_id: int
    name: str
    mention: str
    can_delete: bool
    reason: str
    blocked_reason: str = ""

    @property
    def value(self) -> str:
        return f"{self.kind}:{self.object_id}"


_KIND_ORDER = {"text_channel": 0, "voice_channel": 1, "category": 2, "role": 3}


def _norm(value: Any) -> str:
    return str(value or "").strip().casefold()


def _short(value: Any, limit: int = 90) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _exact_name(obj: Any, names: Iterable[str]) -> bool:
    return _norm(getattr(obj, "name", "")) in {_norm(x) for x in names}


def _mention(obj: Any) -> str:
    return str(getattr(obj, "mention", None) or f"`{getattr(obj, 'name', getattr(obj, 'id', 'unknown'))}`")


def _bot_member(guild: discord.Guild) -> Optional[discord.Member]:
    try:
        return guild.me
    except Exception:
        return None


def _channel_block_reason(guild: discord.Guild, obj: discord.abc.GuildChannel) -> str:
    me = _bot_member(guild)
    if me is None:
        return "Bot member could not be resolved."
    if not me.guild_permissions.manage_channels:
        return "Bot is missing Manage Channels."
    name = str(getattr(obj, "name", "") or "").lower()
    if any(part in name for part in PROTECTED_NAME_PARTS):
        return "Looks like a ticket/transcript channel, not a setup default."
    return ""


def _role_block_reason(guild: discord.Guild, role: discord.Role) -> str:
    me = _bot_member(guild)
    if me is None:
        return "Bot member could not be resolved."
    if role.is_default():
        return "Cannot delete @everyone."
    if role.managed:
        return "Managed/integration role cannot be deleted."
    if not me.guild_permissions.manage_roles:
        return "Bot is missing Manage Roles."
    try:
        if role >= me.top_role and guild.owner_id != me.id:
            return "Role is above/equal to the bot role. Move Stoney higher first."
    except Exception:
        return "Could not verify bot role hierarchy."
    return ""


def _category_children(category: discord.CategoryChannel) -> list[discord.abc.GuildChannel]:
    try:
        return list(category.channels or [])
    except Exception:
        return []


def _default_child_ids(guild: discord.Guild) -> set[int]:
    ids: set[int] = set()
    for ch in list(getattr(guild, "text_channels", []) or []):
        if _exact_name(ch, DEFAULT_TEXT_CHANNEL_NAMES):
            ids.add(int(ch.id))
    for ch in list(getattr(guild, "voice_channels", []) or []):
        if _exact_name(ch, DEFAULT_VOICE_CHANNEL_NAMES):
            ids.add(int(ch.id))
    return ids


def collect_setup_cleanup_candidates(guild: discord.Guild) -> tuple[list[CleanupCandidate], list[CleanupCandidate]]:
    candidates: list[CleanupCandidate] = []
    skipped: list[CleanupCandidate] = []
    default_child_ids = _default_child_ids(guild)

    for ch in list(getattr(guild, "text_channels", []) or []):
        if not _exact_name(ch, DEFAULT_TEXT_CHANNEL_NAMES):
            continue
        blocked = _channel_block_reason(guild, ch)
        item = CleanupCandidate("text_channel", int(ch.id), str(ch.name), _mention(ch), not blocked, "Exact Stoney default text channel name.", blocked)
        (skipped if blocked else candidates).append(item)

    for ch in list(getattr(guild, "voice_channels", []) or []):
        if not _exact_name(ch, DEFAULT_VOICE_CHANNEL_NAMES):
            continue
        blocked = _channel_block_reason(guild, ch)
        item = CleanupCandidate("voice_channel", int(ch.id), str(ch.name), _mention(ch), not blocked, "Exact Stoney default voice channel name.", blocked)
        (skipped if blocked else candidates).append(item)

    for category in list(getattr(guild, "categories", []) or []):
        if not _exact_name(category, DEFAULT_CATEGORY_NAMES):
            continue
        blocked = _channel_block_reason(guild, category)
        non_default_children = [ch for ch in _category_children(category) if int(ch.id) not in default_child_ids]
        if non_default_children:
            names = ", ".join(f"#{getattr(ch, 'name', ch.id)}" for ch in non_default_children[:5])
            blocked = f"Contains non-default channels: {names}. Move/delete those manually first."
        item = CleanupCandidate("category", int(category.id), str(category.name), f"`{category.name}`", not blocked, "Exact Stoney default category name.", blocked)
        (skipped if blocked else candidates).append(item)

    for role in list(getattr(guild, "roles", []) or []):
        if not _exact_name(role, DEFAULT_ROLE_NAMES):
            continue
        blocked = _role_block_reason(guild, role)
        members = 0
        try:
            members = len(role.members)
        except Exception:
            members = 0
        reason = "Exact Stoney default role name."
        if members:
            reason += f" Assigned to {members} member(s); deleting removes that role from them."
        item = CleanupCandidate("role", int(role.id), str(role.name), _mention(role), not blocked, reason, blocked)
        (skipped if blocked else candidates).append(item)

    candidates.sort(key=lambda x: (_KIND_ORDER.get(x.kind, 99), x.name.casefold()))
    skipped.sort(key=lambda x: (_KIND_ORDER.get(x.kind, 99), x.name.casefold()))
    return candidates, skipped


def _candidate_by_value(guild: discord.Guild, value: str) -> Optional[CleanupCandidate]:
    candidates, _skipped = collect_setup_cleanup_candidates(guild)
    return next((x for x in candidates if x.value == value), None)


def _filter_candidates(guild: discord.Guild, mode: str) -> list[CleanupCandidate]:
    candidates, _skipped = collect_setup_cleanup_candidates(guild)
    if mode == "channels":
        return [x for x in candidates if x.kind in {"text_channel", "voice_channel"}]
    if mode == "roles":
        return [x for x in candidates if x.kind == "role"]
    if mode == "categories":
        return [x for x in candidates if x.kind == "category"]
    if mode == "all":
        return candidates
    return []


def _candidate_lines(items: list[CleanupCandidate], *, skipped: bool = False, limit: int = 1024) -> str:
    if not items:
        return "None"
    lines: list[str] = []
    total = 0
    for item in items:
        kind = item.kind.replace("_", " ").title()
        suffix = item.blocked_reason if skipped else item.reason
        line = f"• **{kind}** {item.mention} — {_short(suffix, 120)}"
        if total + len(line) + 1 > limit:
            lines.append(f"…and {len(items) - len(lines)} more")
            break
        lines.append(line)
        total += len(line) + 1
    return "\n".join(lines)


def _get_object(guild: discord.Guild, item: CleanupCandidate) -> Any:
    if item.kind == "role":
        return guild.get_role(item.object_id)
    return guild.get_channel(item.object_id)


def _still_safe(guild: discord.Guild, item: CleanupCandidate) -> tuple[bool, str]:
    fresh = _candidate_by_value(guild, item.value)
    if fresh is None:
        return False, "It no longer matches a safe cleanup candidate. Refresh the preview."
    if not fresh.can_delete:
        return False, fresh.blocked_reason or "It is blocked from cleanup."
    return True, ""


async def _clear_deleted_config_refs(guild: discord.Guild, user: discord.abc.User, deleted_ids: set[int]) -> str:
    if not deleted_ids:
        return ""
    gid = int(guild.id)

    def _sync() -> str:
        snapshot = recovery._current_snapshot_sync(gid, user)
        config = recovery._mapping(snapshot.get("config"))
        patch: dict[str, Any] = {}
        for key, value in config.items():
            try:
                if int(value) in deleted_ids:
                    patch[key] = None
            except Exception:
                continue
        if not patch:
            recovery._write_config_patch_sync(gid, {"setup_cleanup_checked_at": recovery._now_iso()}, snapshot)
            return ""
        recovery._write_config_patch_sync(gid, patch, snapshot)
        return f"Cleared saved setup references: {', '.join(f'`{x}`' for x in sorted(patch))}."

    try:
        message = await asyncio.to_thread(_sync)
        invalidate_guild_config(gid)
        return message
    except Exception as e:
        return f"⚠️ Could not clear saved config references: `{type(e).__name__}: {_short(e, 180)}`"


async def _delete_candidates(guild: discord.Guild, user: discord.abc.User, items: list[CleanupCandidate]) -> tuple[str, bool]:
    if not items:
        return "Nothing matched that cleanup choice.", True

    deleted: list[str] = []
    failed: list[str] = []
    deleted_ids: set[int] = set()
    reason = f"Stoney selective setup cleanup requested by {user} ({getattr(user, 'id', 'unknown')})"

    ordered = sorted(items, key=lambda x: (_KIND_ORDER.get(x.kind, 99), x.name.casefold()))
    for item in ordered:
        safe, why = _still_safe(guild, item)
        if not safe:
            failed.append(f"{item.kind.replace('_', ' ')} `{item.name}` skipped: {why}")
            continue
        obj = _get_object(guild, item)
        if obj is None:
            continue
        if item.kind == "category":
            children = _category_children(obj)
            if children:
                names = ", ".join(f"#{getattr(ch, 'name', ch.id)}" for ch in children[:5])
                failed.append(f"category `{item.name}` still has channels: {names}")
                continue
        try:
            await obj.delete(reason=reason)
            deleted.append(f"{item.kind.replace('_', ' ')}: {item.name}")
            deleted_ids.add(item.object_id)
        except Exception as e:
            failed.append(f"{item.kind.replace('_', ' ')} `{item.name}`: {type(e).__name__}: {_short(e, 100)}")

    config_msg = await _clear_deleted_config_refs(guild, user, deleted_ids)
    lines: list[str] = []
    if deleted:
        lines.append("✅ Deleted:\n" + "\n".join(f"• {x}" for x in deleted[:20]))
        if len(deleted) > 20:
            lines.append(f"…and {len(deleted) - 20} more")
    else:
        lines.append("ℹ️ No Discord items were deleted.")
    if config_msg:
        lines.append(config_msg)
    if failed:
        lines.append("🚫 Skipped/failed:\n" + "\n".join(f"• {x}" for x in failed[:15]))
    return "\n\n".join(lines)[:3500], not failed


async def _delete_full_setup(guild: discord.Guild, user: discord.abc.User) -> tuple[str, bool]:
    # Save snapshot + clear setup/menu first, then delete exact-name defaults.
    reset_msg, reset_ok = await recovery._reset_saved_setup(guild, user, include_menu=True)
    items = _filter_candidates(guild, "all")
    delete_msg, delete_ok = await _delete_candidates(guild, user, items)
    return f"{reset_msg}\n\n{delete_msg}", bool(reset_ok and delete_ok)


async def build_cleanup_preview_embed(guild: discord.Guild, *, title: str = "🧹 Setup Cleanup Preview") -> discord.Embed:
    candidates, skipped = collect_setup_cleanup_candidates(guild)
    embed = discord.Embed(
        title=title,
        description=(
            "This finds Discord items that match Stoney's default fresh-server setup names.\n"
            "Use selective cleanup when you only want to remove one thing or one type of thing."
        ),
        color=discord.Color.orange(),
        timestamp=now_utc(),
    )
    embed.add_field(name=f"Can Remove ({len(candidates)})", value=_candidate_lines(candidates), inline=False)
    embed.add_field(name=f"Skipped / Manual Review ({len(skipped)})", value=_candidate_lines(skipped, skipped=True), inline=False)
    embed.add_field(
        name="Cleanup Options",
        value=(
            "• **Remove One Thing** = choose one exact channel/category/role.\n"
            "• **Remove Setup Channels** = only default setup text/voice channels.\n"
            "• **Remove Setup Roles** = only default setup roles.\n"
            "• **Remove Empty Setup Categories** = only safe empty default categories.\n"
            "• **Delete All Bot-Created Setup Items** = full detected setup cleanup."
        ),
        inline=False,
    )
    return embed


class ConfirmDeleteModal(discord.ui.Modal):
    def __init__(self, *, mode: str, selected_value: str = "") -> None:
        self.mode = mode
        self.selected_value = selected_value
        title = "Confirm Cleanup"
        expected = "DELETE SETUP" if mode == "all" else "REMOVE"
        self.expected = expected
        super().__init__(title=title)
        self.confirm = discord.ui.TextInput(label=f"Type {expected} to continue", placeholder=expected, min_length=len(expected), max_length=20)
        self.add_item(self.confirm)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        typed = str(self.confirm.value or "").strip().upper()
        if typed != self.expected:
            return await interaction.response.send_message(f"🚫 Cancelled. You typed `{typed or 'nothing'}` instead of `{self.expected}`.", ephemeral=True)
        await solid._safe_defer_modal(interaction)
        if self.mode == "one":
            item = _candidate_by_value(guild, self.selected_value)
            message, ok = await _delete_candidates(guild, interaction.user, [item] if item else [])
        elif self.mode == "all":
            message, ok = await _delete_full_setup(guild, interaction.user)
        else:
            message, ok = await _delete_candidates(guild, interaction.user, _filter_candidates(guild, self.mode))
        embed = await patched_recovery_embed(guild, title="✅ Cleanup Complete" if ok else "⚠️ Cleanup Finished With Issues")
        embed.color = discord.Color.green() if ok else discord.Color.orange()
        embed.add_field(name="Result", value=message[:1024], inline=False)
        if len(message) > 1024:
            embed.add_field(name="More Details", value=message[1024:2048], inline=False)
        embed.add_field(name="Next Step", value="Run Health Check. If you removed setup items, rerun Fresh Server or Existing Server setup.", inline=False)
        try:
            await interaction.followup.send(embed=embed, view=PatchedRecoveryCenterView(), ephemeral=True)
        except Exception:
            await solid._edit_or_followup(interaction, embed=embed, view=PatchedRecoveryCenterView())


class RemoveOneSelect(discord.ui.Select):
    def __init__(self, candidates: list[CleanupCandidate]) -> None:
        options: list[discord.SelectOption] = []
        for item in candidates[:25]:
            label = f"{item.kind.replace('_', ' ').title()}: {item.name}"
            options.append(discord.SelectOption(label=_short(label, 95), description=_short(item.reason, 100), value=item.value))
        if not options:
            options.append(discord.SelectOption(label="No removable setup items found", value="__none__", description="Go back or refresh."))
        super().__init__(placeholder="Choose exactly one setup item to remove", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        value = str(self.values[0])
        if value == "__none__":
            return await interaction.response.send_message("No removable setup items were found.", ephemeral=True)
        item = _candidate_by_value(guild, value)
        if item is None:
            return await interaction.response.send_message("That item is no longer removable. Refresh and try again.", ephemeral=True)
        embed = discord.Embed(
            title="Confirm Remove One Thing",
            description=f"Selected: **{item.kind.replace('_', ' ').title()}** {item.mention}\n\n{item.reason}",
            color=discord.Color.orange(),
            timestamp=now_utc(),
        )
        embed.add_field(name="Confirmation", value="Press the button, then type `REMOVE`.", inline=False)
        await interaction.response.edit_message(embed=embed, view=ConfirmOneView(item.value))


class RemoveOneView(solid.BackToSetupView):
    def __init__(self, candidates: list[CleanupCandidate]) -> None:
        super().__init__()
        self.add_item(RemoveOneSelect(candidates))


class ConfirmOneView(solid.BackToSetupView):
    def __init__(self, selected_value: str) -> None:
        super().__init__()
        self.selected_value = selected_value

    @discord.ui.button(label="Remove This One Thing", emoji="🗑️", style=discord.ButtonStyle.danger, custom_id="stoney_cleanup:remove_one_confirm", row=0)
    async def remove_one(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        await interaction.response.send_modal(ConfirmDeleteModal(mode="one", selected_value=self.selected_value))

    @discord.ui.button(label="Back to Cleanup Preview", emoji="🔎", style=discord.ButtonStyle.secondary, custom_id="stoney_cleanup:back_preview", row=1)
    async def back_preview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await solid._safe_defer_update(interaction)
        embed = await build_cleanup_preview_embed(guild)
        await solid._edit_or_followup(interaction, embed=embed, view=CleanupPreviewView())


class CleanupPreviewView(solid.BackToSetupView):
    @discord.ui.button(label="Remove One Thing", emoji="🎯", style=discord.ButtonStyle.primary, custom_id="stoney_cleanup:one", row=0)
    async def remove_one(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        candidates, _skipped = collect_setup_cleanup_candidates(guild)
        embed = discord.Embed(title="🎯 Remove One Thing", description="Choose exactly one setup item to remove.", color=discord.Color.orange(), timestamp=now_utc())
        await interaction.response.edit_message(embed=embed, view=RemoveOneView(candidates))

    @discord.ui.button(label="Remove Setup Channels", emoji="🧹", style=discord.ButtonStyle.secondary, custom_id="stoney_cleanup:channels", row=0)
    async def remove_channels(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _ask_confirm_type(interaction, "channels")

    @discord.ui.button(label="Remove Setup Roles", emoji="🧹", style=discord.ButtonStyle.secondary, custom_id="stoney_cleanup:roles", row=1)
    async def remove_roles(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _ask_confirm_type(interaction, "roles")

    @discord.ui.button(label="Remove Empty Setup Categories", emoji="🧹", style=discord.ButtonStyle.secondary, custom_id="stoney_cleanup:categories", row=1)
    async def remove_categories(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _ask_confirm_type(interaction, "categories")

    @discord.ui.button(label="Delete All Bot-Created Setup Items", emoji="🧨", style=discord.ButtonStyle.danger, custom_id="stoney_cleanup:all", row=2)
    async def remove_all(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        await interaction.response.send_modal(ConfirmDeleteModal(mode="all"))

    @discord.ui.button(label="Back to Recovery", emoji="🛟", style=discord.ButtonStyle.secondary, custom_id="stoney_cleanup:back_recovery", row=3)
    async def back_recovery(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await solid._safe_defer_update(interaction)
        embed = await patched_recovery_embed(guild)
        await solid._edit_or_followup(interaction, embed=embed, view=PatchedRecoveryCenterView())


async def _ask_confirm_type(interaction: discord.Interaction, mode: str) -> None:
    if not await solid._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
    items = _filter_candidates(guild, mode)
    label = {"channels": "setup channels", "roles": "setup roles", "categories": "empty setup categories"}.get(mode, mode)
    if not items:
        return await interaction.response.send_message(f"No removable {label} found.", ephemeral=True)
    embed = discord.Embed(title=f"Confirm Remove {label.title()}", description=_candidate_lines(items), color=discord.Color.orange(), timestamp=now_utc())
    embed.add_field(name="Confirmation", value="Press continue, then type `REMOVE`.", inline=False)
    await interaction.response.edit_message(embed=embed, view=ConfirmTypeView(mode, label))


class ConfirmTypeView(solid.BackToSetupView):
    def __init__(self, mode: str, label: str) -> None:
        super().__init__()
        self.mode = mode
        self.label = label

    @discord.ui.button(label="Continue", emoji="🗑️", style=discord.ButtonStyle.danger, custom_id="stoney_cleanup:type_continue", row=0)
    async def continue_remove(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        await interaction.response.send_modal(ConfirmDeleteModal(mode=self.mode))


class PatchedRecoveryCenterView(solid.BackToSetupView):
    @discord.ui.button(label="Safe Start Over", emoji="🛟", style=discord.ButtonStyle.danger, custom_id="stoney_recovery:start_over", row=0)
    async def start_over(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        await interaction.response.send_modal(recovery.ConfirmRecoveryModal(action="start_over"))

    @discord.ui.button(label="Preview / Selective Cleanup", emoji="🔎", style=discord.ButtonStyle.primary, custom_id="stoney_cleanup:preview", row=0)
    async def preview_cleanup(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await solid._safe_defer_update(interaction)
        embed = await build_cleanup_preview_embed(guild)
        await solid._edit_or_followup(interaction, embed=embed, view=CleanupPreviewView())

    @discord.ui.button(label="Reset Saved Channels/Roles", emoji="🧽", style=discord.ButtonStyle.secondary, custom_id="stoney_recovery:reset_config", row=1)
    async def reset_config(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        await interaction.response.send_modal(recovery.ConfirmRecoveryModal(action="reset_config"))

    @discord.ui.button(label="Reset Ticket Menu Only", emoji="🧾", style=discord.ButtonStyle.secondary, custom_id="stoney_recovery:reset_menu", row=1)
    async def reset_menu(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        await interaction.response.send_modal(recovery.ConfirmRecoveryModal(action="reset_menu"))

    @discord.ui.button(label="Restore Last Reset", emoji="↩️", style=discord.ButtonStyle.primary, custom_id="stoney_recovery:restore", row=2)
    async def restore(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        await interaction.response.send_modal(recovery.ConfirmRecoveryModal(action="restore"))

    @discord.ui.button(label="Rebuild Recommended Menu", emoji="🧱", style=discord.ButtonStyle.success, custom_id="stoney_recovery:rebuild_menu", row=2)
    async def rebuild_menu(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await solid._safe_defer_update(interaction)
        message, ok = await recovery._rebuild_recommended_menu(guild)
        embed = await patched_recovery_embed(guild, title="✅ Recovery Action Complete" if ok else "🚫 Recovery Action Failed")
        embed.color = discord.Color.green() if ok else discord.Color.red()
        embed.add_field(name="Result", value=message[:1024], inline=False)
        await solid._edit_or_followup(interaction, embed=embed, view=PatchedRecoveryCenterView())


async def patched_recovery_embed(guild: discord.Guild, *, title: str = "🛟 Setup Recovery Center") -> discord.Embed:
    builder = _ORIGINAL_RECOVERY_EMBED or recovery._build_recovery_embed
    embed = await builder(guild, title=title)
    candidates, skipped = collect_setup_cleanup_candidates(guild)
    embed.add_field(
        name="Selective Discord Cleanup",
        value=(
            f"Detected `{len(candidates)}` removable default setup item(s) and `{len(skipped)}` skipped/manual-review item(s).\n"
            "Use **Preview / Selective Cleanup** to remove one item, only channels, only roles, only empty setup categories, or all detected setup items."
        ),
        inline=False,
    )
    return embed


def _patch() -> None:
    global _PATCHED, _ORIGINAL_RECOVERY_EMBED
    if _ORIGINAL_RECOVERY_EMBED is None:
        _ORIGINAL_RECOVERY_EMBED = getattr(recovery, "_build_recovery_embed", None)
    recovery._build_recovery_embed = patched_recovery_embed
    recovery.RecoveryCenterView = PatchedRecoveryCenterView
    _PATCHED = True


_patch()


def register_public_setup_cleanup_commands(bot: Any, tree: Any) -> None:
    _ = bot, tree
    _patch()
    print("✅ public_setup_cleanup: selective setup cleanup tools active")


__all__ = ["register_public_setup_cleanup_commands", "collect_setup_cleanup_candidates"]
