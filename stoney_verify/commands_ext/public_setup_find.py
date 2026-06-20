from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Optional

import discord
from discord import app_commands

from .common import safe_defer
from .public_setup_config_writer import apply_public_setup_writer_patch, upsert_guild_config
from .public_setup_group import (
    _channel_value,
    _config_embed,
    _require_setup_permission,
    _role_value,
    _utc_iso,
    get_guild_config,
    invalidate_guild_config,
    dank_group,
)


@dataclass(frozen=True)
class SetupTarget:
    key: str
    label: str
    column: str
    kind: str  # text | category | voice | role
    required_manage_role: bool = False
    require_attach_files: bool = False
    also_write: tuple[tuple[str, str], ...] = ()


TARGETS: dict[str, SetupTarget] = {
    "verify_text": SetupTarget("verify_text", "Verify text channel", "verify_channel_id", "text"),
    "vc_verify": SetupTarget("vc_verify", "VC verify voice channel", "vc_verify_channel_id", "voice"),
    "vc_queue": SetupTarget("vc_queue", "VC queue/status text channel", "vc_verify_queue_channel_id", "text"),
    "unverified_role": SetupTarget("unverified_role", "Unverified role", "unverified_role_id", "role", required_manage_role=True),
    "verified_role": SetupTarget("verified_role", "Verified role", "verified_role_id", "role", required_manage_role=True),
    "resident_role": SetupTarget("resident_role", "Resident/member role", "resident_role_id", "role", required_manage_role=True),
    "open_ticket_category": SetupTarget("open_ticket_category", "Open ticket category", "ticket_category_id", "category"),
    "archive_ticket_category": SetupTarget("archive_ticket_category", "Archive/closed ticket category", "ticket_archive_category_id", "category"),
    "staff_role": SetupTarget("staff_role", "Ticket staff role", "staff_role_id", "role", also_write=(("vc_staff_role_id", "same"),)),
    "transcripts": SetupTarget("transcripts", "Transcript text channel", "transcripts_channel_id", "text", require_attach_files=True),
    "modlog": SetupTarget("modlog", "Modlog channel", "modlog_channel_id", "text"),
    "raidlog": SetupTarget("raidlog", "Raid/security log channel", "raidlog_channel_id", "text"),
    "join_exit": SetupTarget("join_exit", "Join/exit log channel", "join_log_channel_id", "text"),
    "force_verify_log": SetupTarget("force_verify_log", "Forced verification log channel", "force_verify_log_channel_id", "text"),
}


_TARGET_CHOICES = [
    app_commands.Choice(name="Verify text channel", value="verify_text"),
    app_commands.Choice(name="VC verify voice channel", value="vc_verify"),
    app_commands.Choice(name="VC queue/status text channel", value="vc_queue"),
    app_commands.Choice(name="Unverified role", value="unverified_role"),
    app_commands.Choice(name="Verified role", value="verified_role"),
    app_commands.Choice(name="Resident/member role", value="resident_role"),
    app_commands.Choice(name="Open ticket category", value="open_ticket_category"),
    app_commands.Choice(name="Archive/closed ticket category", value="archive_ticket_category"),
    app_commands.Choice(name="Ticket staff role", value="staff_role"),
    app_commands.Choice(name="Transcript text channel", value="transcripts"),
    app_commands.Choice(name="Modlog channel", value="modlog"),
    app_commands.Choice(name="Raid/security log channel", value="raidlog"),
    app_commands.Choice(name="Join/exit log channel", value="join_exit"),
    app_commands.Choice(name="Forced verification log channel", value="force_verify_log"),
]


def _clean(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    text = re.sub(r"[^\w\d]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _tokens(value: str) -> list[str]:
    return [part for part in _clean(value).split(" ") if part]


def _score(query: str, obj: Any) -> int:
    q = _clean(query)
    if not q:
        return 0
    raw_name = str(getattr(obj, "name", "") or "")
    name = _clean(raw_name)
    obj_id = str(getattr(obj, "id", "") or "")
    if q == obj_id:
        return 1000
    if obj_id.startswith(q):
        return 950
    if name == q:
        return 900
    if name.startswith(q):
        return 820
    q_tokens = _tokens(q)
    if q_tokens and all(token in name for token in q_tokens):
        return 760
    if q in name:
        return 700
    if q_tokens and any(token in name for token in q_tokens):
        return 450
    return 0


def _iter_candidates(guild: discord.Guild, spec: SetupTarget) -> Iterable[Any]:
    if spec.kind == "role":
        roles = [role for role in guild.roles if not role.is_default()]
        return sorted(roles, key=lambda role: role.position, reverse=True)
    if spec.kind == "category":
        return sorted(guild.categories, key=lambda channel: channel.position)
    if spec.kind == "voice":
        stage_cls = getattr(discord, "StageChannel", None)
        out: list[Any] = []
        for channel in guild.channels:
            if isinstance(channel, discord.VoiceChannel):
                out.append(channel)
            elif stage_cls is not None and isinstance(channel, stage_cls):
                out.append(channel)
        return sorted(out, key=lambda channel: (getattr(channel, "category_id", 0) or 0, getattr(channel, "position", 0)))
    out = [channel for channel in guild.channels if isinstance(channel, discord.TextChannel)]
    return sorted(out, key=lambda channel: (getattr(channel, "category_id", 0) or 0, getattr(channel, "position", 0)))


def _search(guild: discord.Guild, spec: SetupTarget, query: str) -> list[Any]:
    scored: list[tuple[int, str, Any]] = []
    for obj in _iter_candidates(guild, spec):
        score = _score(query, obj)
        if score <= 0:
            continue
        scored.append((score, str(getattr(obj, "name", "") or ""), obj))
    scored.sort(key=lambda item: (-item[0], _clean(item[1]), int(getattr(item[2], "id", 0) or 0)))
    return [obj for _score_value, _name, obj in scored]


def _short(value: str, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _obj_label(obj: Any, spec: SetupTarget) -> str:
    prefix = "@" if spec.kind == "role" else "#"
    if spec.kind == "category":
        prefix = "📁 "
    if spec.kind == "voice":
        prefix = "🔊 "
    return _short(f"{prefix}{getattr(obj, 'name', obj)}", 100)


def _obj_description(obj: Any, spec: SetupTarget) -> str:
    obj_id = str(getattr(obj, "id", "") or "")
    if spec.kind == "role":
        members = len(getattr(obj, "members", []) or [])
        return _short(f"Role ID {obj_id} • {members} member(s)", 100)
    category = getattr(obj, "category", None)
    if category is not None:
        return _short(f"Channel ID {obj_id} • in {getattr(category, 'name', 'category')}", 100)
    return _short(f"ID {obj_id}", 100)


def _mention(obj: Any) -> str:
    mention = getattr(obj, "mention", None)
    if mention:
        return str(mention)
    return f"`{getattr(obj, 'name', obj)}`"


def _resolve_object(guild: discord.Guild, spec: SetupTarget, obj_id: int) -> Optional[Any]:
    if spec.kind == "role":
        role = guild.get_role(int(obj_id))
        return role if role is not None and not role.is_default() else None
    channel = guild.get_channel(int(obj_id))
    if spec.kind == "category":
        return channel if isinstance(channel, discord.CategoryChannel) else None
    if spec.kind == "voice":
        stage_cls = getattr(discord, "StageChannel", None)
        if isinstance(channel, discord.VoiceChannel):
            return channel
        if stage_cls is not None and isinstance(channel, stage_cls):
            return channel
        return None
    return channel if isinstance(channel, discord.TextChannel) else None


def _validate_object(guild: discord.Guild, spec: SetupTarget, obj: Any) -> tuple[list[str], list[str]]:
    blockers: list[str] = []
    warnings: list[str] = []
    me = guild.me
    if me is None:
        blockers.append("Bot member object is not available yet. Restart or try again after the bot is fully ready.")
        return blockers, warnings

    if spec.kind == "role":
        role = obj
        if not isinstance(role, discord.Role):
            blockers.append(f"{spec.label} must be a role.")
            return blockers, warnings
        if role.is_default():
            blockers.append("@everyone cannot be used for this setup value.")
        if role.managed:
            blockers.append(f"{role.mention} is an integration/bot-managed role and cannot be used here.")
        if spec.required_manage_role and me.top_role <= role:
            blockers.append(f"Bot role must be above {role.mention} in the role hierarchy before it can manage that role.")
        elif not spec.required_manage_role and me.top_role <= role:
            warnings.append(f"{role.mention} is above or equal to the bot role. That is okay only if the bot never needs to assign/remove it.")
        return blockers, warnings

    if spec.kind == "category":
        if not isinstance(obj, discord.CategoryChannel):
            blockers.append(f"{spec.label} must be a category.")
            return blockers, warnings
        perms = obj.permissions_for(me)
        if not perms.view_channel:
            blockers.append(f"Bot cannot view category `{obj.name}`.")
        if not perms.manage_channels:
            blockers.append(f"Bot needs Manage Channels in category `{obj.name}` to create/move ticket channels there.")
        return blockers, warnings

    if spec.kind == "voice":
        stage_cls = getattr(discord, "StageChannel", None)
        is_voice = isinstance(obj, discord.VoiceChannel) or (stage_cls is not None and isinstance(obj, stage_cls))
        if not is_voice:
            blockers.append(f"{spec.label} must be a voice/stage channel.")
            return blockers, warnings
        perms = obj.permissions_for(me)
        if not perms.view_channel:
            blockers.append(f"Bot cannot view {_mention(obj)}.")
        if not perms.connect:
            blockers.append(f"Bot needs Connect permission for {_mention(obj)}.")
        if not perms.manage_channels:
            blockers.append(f"Bot needs Manage Channels for {_mention(obj)} so VC verification access can be granted/revoked safely.")
        return blockers, warnings

    if not isinstance(obj, discord.TextChannel):
        blockers.append(f"{spec.label} must be a text channel.")
        return blockers, warnings
    perms = obj.permissions_for(me)
    if not perms.view_channel:
        blockers.append(f"Bot cannot view {_mention(obj)}.")
    if not perms.send_messages:
        blockers.append(f"Bot cannot send messages in {_mention(obj)}.")
    if not perms.read_message_history:
        blockers.append(f"Bot needs Read Message History in {_mention(obj)}.")
    if spec.require_attach_files and not perms.attach_files:
        blockers.append(f"Bot needs Attach Files in {_mention(obj)} for transcripts.")
    return blockers, warnings


def _payload_for(interaction: discord.Interaction, spec: SetupTarget, obj: Any) -> dict[str, Any]:
    value = _role_value(obj) if spec.kind == "role" else _channel_value(obj)
    payload: dict[str, Any] = {
        spec.column: value,
        "configured_by_id": str(interaction.user.id),
        "configured_by_name": str(interaction.user),
        "configured_at": _utc_iso(),
    }
    for column, source in spec.also_write:
        if source == "same":
            payload[column] = value
    return payload


def _result_embed(guild: discord.Guild, spec: SetupTarget, query: str, matches: list[Any]) -> discord.Embed:
    embed = discord.Embed(
        title="🔎 Setup Search Results",
        description=(
            f"Target: **{spec.label}**\n"
            f"Search: `{_short(query, 80)}`\n\n"
            "Select the correct result below. Only the selected setup value will be saved."
        ),
        color=discord.Color.blurple(),
    )
    preview = []
    for obj in matches[:10]:
        preview.append(f"• {_mention(obj)} (`{int(obj.id)}`)")
    if len(matches) > 10:
        preview.append(f"• …and {len(matches) - 10} more result(s)")
    embed.add_field(name=f"Matches found: {len(matches)}", value="\n".join(preview) or "None", inline=False)
    embed.set_footer(text=f"Guild {guild.id} • showing top {min(25, len(matches))} selectable result(s)")
    return embed


class SetupSearchSelect(discord.ui.Select):
    def __init__(self, *, spec: SetupTarget, matches: list[Any]) -> None:
        options = [
            discord.SelectOption(
                label=_obj_label(obj, spec),
                description=_obj_description(obj, spec),
                value=str(int(obj.id)),
            )
            for obj in matches[:25]
        ]
        super().__init__(placeholder=f"Choose {spec.label}", min_values=1, max_values=1, options=options)
        self.spec = spec

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, SetupSearchResultView):
            return await interaction.response.send_message("❌ Search view state expired. Run `/dank setup` again.", ephemeral=True)
        await view.apply(interaction, int(self.values[0]))


class SetupSearchResultView(discord.ui.View):
    def __init__(self, *, owner_id: int, spec: SetupTarget, matches: list[Any], timeout: float = 300.0) -> None:
        super().__init__(timeout=timeout)
        self.owner_id = int(owner_id)
        self.spec = spec
        self.add_item(SetupSearchSelect(spec=spec, matches=matches))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) != self.owner_id:
            await interaction.response.send_message("❌ This setup search belongs to the admin who opened it.", ephemeral=True)
            return False
        return await _require_setup_permission(interaction)

    async def apply(self, interaction: discord.Interaction, obj_id: int) -> None:
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        obj = _resolve_object(guild, self.spec, obj_id)
        if obj is None:
            return await interaction.response.edit_message(
                embed=discord.Embed(
                    title="🚫 Setup Search Result Expired",
                    description="That channel/role no longer exists or no longer matches the selected setup type.",
                    color=discord.Color.red(),
                ),
                view=None,
            )
        blockers, warnings = _validate_object(guild, self.spec, obj)
        if blockers:
            embed = discord.Embed(title="🚫 Setup Value Rejected", color=discord.Color.red())
            embed.add_field(name="Target", value=self.spec.label, inline=False)
            embed.add_field(name="Selected", value=f"{_mention(obj)} (`{int(obj.id)}`)", inline=False)
            embed.add_field(name="Blockers", value="\n".join(f"• {item}" for item in blockers), inline=False)
            if warnings:
                embed.add_field(name="Warnings", value="\n".join(f"• {item}" for item in warnings), inline=False)
            return await interaction.response.edit_message(embed=embed, view=None)

        await upsert_guild_config(guild.id, _payload_for(interaction, self.spec, obj))
        invalidate_guild_config(guild.id)
        cfg = await get_guild_config(guild.id, refresh=True)
        embed = _config_embed(guild, cfg, title=f"✅ Saved {self.spec.label}")
        embed.add_field(name="Selected", value=f"{_mention(obj)} (`{int(obj.id)}`)", inline=False)
        if warnings:
            embed.add_field(name="Warnings", value="\n".join(f"• {item}" for item in warnings), inline=False)
        embed.set_footer(text="Run /dank setup after setup changes to verify the full configuration.")
        await interaction.response.edit_message(embed=embed, view=None)


@dank_group.command(name="setup-find", description="Search all channels/roles by name or ID and save a setup value.")
@app_commands.describe(
    target="The setup field to configure.",
    query="Part of a channel/role name, or the full Discord ID.",
)
@app_commands.choices(target=_TARGET_CHOICES)
async def setup_find(interaction: discord.Interaction, target: app_commands.Choice[str], query: str) -> None:
    if not await _require_setup_permission(interaction):
        return
    await safe_defer(interaction, ephemeral=True)
    guild = interaction.guild
    if guild is None:
        return await interaction.followup.send("❌ This command must be used inside a server.", ephemeral=True)

    key = target.value if isinstance(target, app_commands.Choice) else str(target)
    spec = TARGETS.get(str(key))
    if spec is None:
        return await interaction.followup.send("❌ Unknown setup target.", ephemeral=True)

    query = str(query or "").strip()
    if len(query) < 2:
        return await interaction.followup.send("❌ Search query must be at least 2 characters.", ephemeral=True)

    matches = _search(guild, spec, query)
    if not matches:
        embed = discord.Embed(
            title="🔎 No Setup Matches Found",
            description=(
                f"Target: **{spec.label}**\n"
                f"Search: `{_short(query, 80)}`\n\n"
                "Try a simpler search term, like `verify`, `welcome`, `mod`, `ticket`, `unverified`, or paste the exact ID."
            ),
            color=discord.Color.orange(),
        )
        return await interaction.followup.send(embed=embed, ephemeral=True)

    view = SetupSearchResultView(owner_id=int(interaction.user.id), spec=spec, matches=matches)
    await interaction.followup.send(embed=_result_embed(guild, spec, query, matches), view=view, ephemeral=True)


def register_public_setup_find_commands(bot: Any, tree: Any) -> None:
    _ = bot, tree
    apply_public_setup_writer_patch()
    try:
        print("✅ public_setup_find: attached advanced /dank setup-find search fallback command")
    except Exception:
        pass


__all__ = ["register_public_setup_find_commands"]
