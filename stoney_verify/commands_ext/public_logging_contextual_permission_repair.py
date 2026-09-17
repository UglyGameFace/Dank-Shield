from __future__ import annotations

"""Same-screen bot-access repair for public logging workflows.

This integration deliberately owns no Discord permission mutation. It maps the
already-selected logging routes into the shared contextual permission-repair
contract and leaves every overwrite change to ``permission_repair_core``.
"""

from typing import Any, Awaitable, Callable, Optional

import discord

from stoney_verify import contextual_permission_repair as contextual
from stoney_verify.exit_card_runtime import resolve_exit_card_channel
from stoney_verify.guild_config import get_guild_config
from stoney_verify.welcome_card_runtime import resolve_join_card_channel

from . import public_modlog_group as modlog
from .public_setup_group import _require_setup_permission, dank_group

_PATCHED = False
_ORIGINAL_MODLOG_HEALTH: Optional[Callable[..., Awaitable[Any]]] = None

MODLOG_KEYS: tuple[str, ...] = (
    "modlog_channel_id",
    "mod_log_channel_id",
    "logs_channel_id",
)

STAFF_AUDIT_KEYS: tuple[str, ...] = (
    "staff_join_audit_channel_id",
    "member_audit_log_channel_id",
    "staff_log_channel_id",
    "staff_logs_channel_id",
    "modlog_channel_id",
    "mod_log_channel_id",
    "audit_log_channel_id",
)

_EXPECTED_MODLOG_EVENTS: tuple[str, ...] = (
    "on_message_delete",
    "on_raw_message_delete",
    "on_bulk_message_delete",
    "on_raw_bulk_message_delete",
    "on_message_edit",
    "on_raw_message_edit",
    "on_member_join",
    "on_member_remove",
    "on_member_ban",
    "on_member_update",
    "on_voice_state_update",
    "on_guild_channel_create",
    "on_guild_channel_delete",
    "on_guild_channel_update",
    "on_guild_role_create",
    "on_guild_role_delete",
    "on_guild_role_update",
    "on_thread_create",
    "on_thread_delete",
    "on_thread_update",
    "on_invite_create",
    "on_invite_delete",
    "on_guild_update",
    "on_member_unban",
    "on_guild_emojis_update",
    "on_guild_stickers_update",
    "on_webhooks_update",
)


def _cfg_value(cfg: Any, key: str, default: Any = None) -> Any:
    try:
        value = getattr(cfg, key, None)
        if value is not None:
            return value
    except Exception:
        pass
    try:
        if hasattr(cfg, "get"):
            value = cfg.get(key)
            if value is not None:
                return value
    except Exception:
        pass
    for bucket in ("settings", "config", "metadata", "meta"):
        try:
            nested = getattr(cfg, bucket, None)
            if isinstance(nested, dict) and nested.get(key) is not None:
                return nested.get(key)
        except Exception:
            pass
        try:
            if hasattr(cfg, "get"):
                nested = cfg.get(bucket)
                if isinstance(nested, dict) and nested.get(key) is not None:
                    return nested.get(key)
        except Exception:
            pass
    return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip().strip("<#@!&>")
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _cfg_id(cfg: Any, *keys: str) -> int:
    for key in keys:
        parsed = _safe_int(_cfg_value(cfg, key, None), 0)
        if parsed > 0:
            return parsed
    return 0


def _channel_target(
    channel: Any,
    *,
    feature: str,
    label: str,
) -> contextual.ContextualRepairTarget | None:
    channel_id = _safe_int(getattr(channel, "id", 0), 0)
    if channel_id <= 0:
        return None
    return contextual.ContextualRepairTarget(
        channel_id=channel_id,
        feature=feature,
        label=label,
    )


def _modlog_targets(cfg: Any) -> tuple[contextual.ContextualRepairTarget, ...]:
    """Return only a saved Modlog target. Never name-guess a repair target."""

    channel_id = _cfg_id(cfg, *MODLOG_KEYS)
    if channel_id <= 0:
        return ()
    return (
        contextual.ContextualRepairTarget(
            channel_id=channel_id,
            feature="logs",
            label="Moderation log channel",
        ),
    )


def _bot_has_view_audit_log(guild: discord.Guild) -> bool:
    try:
        me = guild.me
        perms = getattr(me, "guild_permissions", None)
        return bool(
            getattr(perms, "view_audit_log", False)
            or getattr(perms, "administrator", False)
        )
    except Exception:
        return False


def _bot_can_read_invites(guild: discord.Guild) -> bool:
    try:
        me = guild.me
        perms = getattr(me, "guild_permissions", None)
        return bool(
            getattr(perms, "manage_guild", False)
            or getattr(perms, "administrator", False)
        )
    except Exception:
        return False


def _modlog_manual_issues(guild: discord.Guild, cfg: Any) -> list[str]:
    issues: list[str] = []
    saved_id = _cfg_id(cfg, *MODLOG_KEYS)
    if saved_id <= 0:
        issues.append(
            "No Modlog channel is saved. Choose the real staff log channel before Fix Access can target it."
        )
    elif not isinstance(guild.get_channel(saved_id), discord.TextChannel):
        issues.append(
            f"The saved Modlog channel `{saved_id}` is missing or no longer a text channel. Choose the correct channel first."
        )
    if not _bot_has_view_audit_log(guild):
        issues.append(
            "Server-level **View Audit Log** is missing. Reauthorize Dank Shield or grant that bot-role permission; a channel overwrite cannot add it."
        )
    return issues


def _resolve_staff_audit_channel(guild: discord.Guild, cfg: Any) -> Optional[discord.TextChannel]:
    for key in STAFF_AUDIT_KEYS:
        channel_id = _safe_int(_cfg_value(cfg, key, None), 0)
        if channel_id <= 0:
            continue
        channel = guild.get_channel(channel_id)
        if isinstance(channel, discord.TextChannel):
            return channel
    return None


def _staff_audit_is_configured(cfg: Any) -> bool:
    return any(_safe_int(_cfg_value(cfg, key, None), 0) > 0 for key in STAFF_AUDIT_KEYS)


def _member_log_targets(
    join_channel: Any,
    exit_channel: Any,
    staff_channel: Any,
) -> tuple[contextual.ContextualRepairTarget, ...]:
    rows = (
        _channel_target(
            join_channel,
            feature="welcome",
            label="Live join card channel",
        ),
        _channel_target(
            exit_channel,
            feature="logs",
            label="Live exit card channel",
        ),
        _channel_target(
            staff_channel,
            feature="logs",
            label="Staff member-audit channel",
        ),
    )
    return contextual.normalize_targets(item for item in rows if item is not None)


def _member_log_manual_issues(
    guild: discord.Guild,
    cfg: Any,
    *,
    join_channel: Optional[discord.TextChannel],
    join_reason: str,
    exit_channel: Optional[discord.TextChannel],
    exit_reason: str,
    staff_channel: Optional[discord.TextChannel],
) -> list[str]:
    issues: list[str] = []
    if not isinstance(join_channel, discord.TextChannel):
        issues.append(
            "Live join-card route is unavailable: "
            + str(join_reason or "no channel configured")
            + ". Choose the intended Welcome Card channel first."
        )
    if not isinstance(exit_channel, discord.TextChannel):
        issues.append(
            "Live exit-card route is unavailable: "
            + str(exit_reason or "no channel configured")
            + ". Choose the intended Exit Card / join-leave log channel first."
        )
    if _staff_audit_is_configured(cfg) and not isinstance(staff_channel, discord.TextChannel):
        issues.append(
            "A staff member-audit route is saved but its channel is missing. Choose the real staff audit/Modlog channel first."
        )
    if not _bot_can_read_invites(guild):
        issues.append(
            "Server-level **Manage Server** is missing, so invite-source attribution may stay unknown. Channel Fix Access cannot grant that permission."
        )
    return issues


def _member_routes(
    guild: discord.Guild,
    cfg: Any,
) -> tuple[
    Optional[discord.TextChannel],
    str,
    Optional[discord.TextChannel],
    str,
    Optional[discord.TextChannel],
]:
    join_channel, join_reason = resolve_join_card_channel(guild, cfg)
    exit_channel, exit_reason = resolve_exit_card_channel(guild, cfg)
    staff_channel = _resolve_staff_audit_channel(guild, cfg)
    return join_channel, join_reason, exit_channel, exit_reason, staff_channel


def _member_user_authorized(interaction: discord.Interaction) -> bool:
    if interaction.guild is None:
        return False
    perms = getattr(interaction.user, "guild_permissions", None)
    return bool(
        getattr(perms, "administrator", False)
        or getattr(perms, "manage_guild", False)
        or getattr(perms, "manage_channels", False)
    )


async def _require_member_logs_permission(interaction: discord.Interaction) -> bool:
    if _member_user_authorized(interaction):
        return True
    try:
        await interaction.response.send_message(
            "❌ You need **Manage Server** or **Manage Channels** to repair member-log access.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except Exception:
        try:
            await interaction.followup.send(
                "❌ You need **Manage Server** or **Manage Channels** to repair member-log access.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception:
            pass
    return False


async def _defer_component(interaction: discord.Interaction) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.defer()
    except Exception:
        pass


def _modlog_coverage(client: Any) -> tuple[int, list[str]]:
    listeners = getattr(client, "extra_events", {}) or {}
    if not isinstance(listeners, dict):
        return 0, []
    present = 0
    missing: list[str] = []
    for event in _EXPECTED_MODLOG_EVENTS:
        count = len(list(listeners.get(event) or []))
        if count:
            present += 1
        else:
            missing.append(f"`{event}`")
    return present, missing


def _modlog_health_embed(
    guild: discord.Guild,
    cfg: Any,
    *,
    client: Any,
    last_action: str = "",
) -> discord.Embed:
    saved_id = _cfg_id(cfg, *MODLOG_KEYS)
    saved_channel = guild.get_channel(saved_id) if saved_id > 0 else None
    channel = saved_channel if isinstance(saved_channel, discord.TextChannel) else None
    discovered = modlog._modlog_channel(guild, cfg)

    missing = modlog._missing_perms(channel, guild.me) if channel is not None else []
    healthy = bool(channel is not None and not missing and _bot_has_view_audit_log(guild))
    embed = discord.Embed(
        title="🧾 Modlog Health",
        color=discord.Color.green() if healthy else discord.Color.red(),
        timestamp=discord.utils.utcnow(),
    )
    if channel is not None:
        embed.add_field(name="Channel", value=f"✅ {channel.mention}", inline=False)
        embed.add_field(
            name="Permissions",
            value="✅ Ready" if not missing else "❌ Missing: " + ", ".join(missing),
            inline=False,
        )
    elif isinstance(discovered, discord.TextChannel):
        embed.add_field(
            name="Channel",
            value=(
                f"⚠️ No saved Modlog target. I discovered {discovered.mention} by name, "
                "but Fix Access will not guess. Save that channel first if it is correct."
            ),
            inline=False,
        )
    else:
        embed.add_field(
            name="Channel",
            value=(
                "❌ Not saved. Use the normal Modlog/Logs setup picker to choose the real staff log channel."
            ),
            inline=False,
        )

    present, missing_events = _modlog_coverage(client)
    embed.add_field(
        name="Coverage",
        value=(
            f"✅ {present}/{len(_EXPECTED_MODLOG_EVENTS)} event families active"
            if present
            else "⚠️ Could not confirm active event listeners."
        ),
        inline=False,
    )
    if missing_events:
        embed.add_field(
            name="Missing / Core handled elsewhere",
            value="\n".join(missing_events[:12])[:1024],
            inline=False,
        )
    if not _bot_has_view_audit_log(guild):
        embed.add_field(
            name="Manual prerequisite",
            value="⚠️ Dank Shield still needs server-level **View Audit Log**.",
            inline=False,
        )
    if last_action:
        embed.add_field(name="Last repair", value=str(last_action)[:1024], inline=False)
    embed.set_footer(text="Fix Access targets only the saved Modlog channel; it never guesses a replacement.")
    return embed


def _member_logs_embed(
    guild: discord.Guild,
    cfg: Any,
    *,
    last_action: str = "",
) -> discord.Embed:
    join_channel, join_reason, exit_channel, exit_reason, staff_channel = _member_routes(guild, cfg)
    embed = discord.Embed(
        title="👋 Member Lifecycle Routing",
        description=(
            "Welcome Card Studio owns the live join card. Exit Card Studio owns the live leave card. "
            "Staff audit remains a separate route."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Live join card",
        value=(join_channel.mention if isinstance(join_channel, discord.TextChannel) else f"`Unavailable: {join_reason}`"),
        inline=False,
    )
    embed.add_field(
        name="Live exit card",
        value=(exit_channel.mention if isinstance(exit_channel, discord.TextChannel) else f"`Unavailable: {exit_reason}`"),
        inline=False,
    )
    embed.add_field(
        name="Staff audit / invite source",
        value=(staff_channel.mention if isinstance(staff_channel, discord.TextChannel) else "`Not set — detailed audit will not be posted publicly`"),
        inline=False,
    )
    embed.add_field(
        name="Legacy public lifecycle cards",
        value=(
            "Retired. Neither `dank_shield:join_leave_event:v3` nor "
            "`dank_shield:leave_event:v4` is emitted by the public router."
        ),
        inline=False,
    )
    embed.add_field(
        name="Invite tracking",
        value=(
            "Can read invites ✅"
            if _bot_can_read_invites(guild)
            else "Missing Manage Server permission ⚠️ invite source may stay unknown"
        ),
        inline=False,
    )
    if last_action:
        embed.add_field(name="Last repair", value=str(last_action)[:1024], inline=False)
    return embed


class ModlogHealthRepairView(discord.ui.View):
    def __init__(self, *, owner_id: int, guild: discord.Guild, cfg: Any) -> None:
        super().__init__(timeout=900)
        self.owner_id = int(owner_id)
        self.guild_id = int(guild.id)
        audit = contextual.audit_context(
            guild,
            _modlog_targets(cfg),
            manual_issues=_modlog_manual_issues(guild, cfg),
        )
        label, emoji, style, disabled = contextual.repair_button_state(audit)
        self.repair.label = label
        self.repair.emoji = emoji
        self.repair.style = style
        self.repair.disabled = disabled

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) == self.owner_id:
            return True
        try:
            await interaction.response.send_message(
                "❌ Open your own Modlog Health screen to use this repair control.",
                ephemeral=True,
            )
        except Exception:
            pass
        return False

    @discord.ui.button(
        label="Check / Fix Access",
        emoji="🛠️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank:modlog:contextual_repair:v1",
    )
    async def repair(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None or int(guild.id) != self.guild_id:
            return
        await _defer_component(interaction)
        cfg = await get_guild_config(int(guild.id), refresh=True)
        result = await contextual.repair_context(
            guild,
            _modlog_targets(cfg),
            actor_id=int(interaction.user.id),
            manual_issues=_modlog_manual_issues(guild, cfg),
        )
        cfg = await get_guild_config(int(guild.id), refresh=True)
        await interaction.edit_original_response(
            embed=_modlog_health_embed(
                guild,
                cfg,
                client=interaction.client,
                last_action=result.summary(),
            ),
            view=ModlogHealthRepairView(
                owner_id=self.owner_id,
                guild=guild,
                cfg=cfg,
            ),
        )


class MemberLogsRepairView(discord.ui.View):
    def __init__(self, *, owner_id: int, guild: discord.Guild, cfg: Any) -> None:
        super().__init__(timeout=900)
        self.owner_id = int(owner_id)
        self.guild_id = int(guild.id)
        join_channel, join_reason, exit_channel, exit_reason, staff_channel = _member_routes(guild, cfg)
        audit = contextual.audit_context(
            guild,
            _member_log_targets(join_channel, exit_channel, staff_channel),
            manual_issues=_member_log_manual_issues(
                guild,
                cfg,
                join_channel=join_channel,
                join_reason=join_reason,
                exit_channel=exit_channel,
                exit_reason=exit_reason,
                staff_channel=staff_channel,
            ),
        )
        label, emoji, style, disabled = contextual.repair_button_state(audit)
        self.repair.label = label
        self.repair.emoji = emoji
        self.repair.style = style
        self.repair.disabled = disabled

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) == self.owner_id:
            return True
        try:
            await interaction.response.send_message(
                "❌ Open your own Member Logs screen to use this repair control.",
                ephemeral=True,
            )
        except Exception:
            pass
        return False

    @discord.ui.button(
        label="Check / Fix Access",
        emoji="🛠️",
        style=discord.ButtonStyle.secondary,
        custom_id="dank:member-logs:contextual_repair:v1",
    )
    async def repair(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_member_logs_permission(interaction):
            return
        guild = interaction.guild
        if guild is None or int(guild.id) != self.guild_id:
            return
        await _defer_component(interaction)
        cfg = await get_guild_config(int(guild.id), refresh=True)
        join_channel, join_reason, exit_channel, exit_reason, staff_channel = _member_routes(guild, cfg)
        manual = _member_log_manual_issues(
            guild,
            cfg,
            join_channel=join_channel,
            join_reason=join_reason,
            exit_channel=exit_channel,
            exit_reason=exit_reason,
            staff_channel=staff_channel,
        )
        result = await contextual.repair_context(
            guild,
            _member_log_targets(join_channel, exit_channel, staff_channel),
            actor_id=int(interaction.user.id),
            manual_issues=manual,
        )
        cfg = await get_guild_config(int(guild.id), refresh=True)
        await interaction.edit_original_response(
            embed=_member_logs_embed(guild, cfg, last_action=result.summary()),
            view=MemberLogsRepairView(
                owner_id=self.owner_id,
                guild=guild,
                cfg=cfg,
            ),
        )


async def open_contextual_modlog_health(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        try:
            return await interaction.response.send_message(
                "❌ This command must be used inside a server.",
                ephemeral=True,
            )
        except Exception:
            return None
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
    except Exception:
        pass
    cfg = await get_guild_config(int(guild.id), refresh=True)
    await interaction.followup.send(
        embed=_modlog_health_embed(guild, cfg, client=interaction.client),
        view=ModlogHealthRepairView(
            owner_id=int(interaction.user.id),
            guild=guild,
            cfg=cfg,
        ),
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


async def attach_member_logs_contextual_repair(interaction: discord.Interaction) -> None:
    """Attach the repair view after the authoritative Member Logs callback responds."""

    if not _PATCHED or not _member_user_authorized(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return
    try:
        cfg = await get_guild_config(int(guild.id), refresh=True)
        await interaction.edit_original_response(
            view=MemberLogsRepairView(
                owner_id=int(interaction.user.id),
                guild=guild,
                cfg=cfg,
            )
        )
    except Exception:
        # The authoritative callback already returned its status. Decoration is
        # best-effort and must never turn a successful config write into a
        # second user-visible failure.
        pass


def apply_logging_contextual_permission_repair() -> bool:
    global _PATCHED, _ORIGINAL_MODLOG_HEALTH
    if _PATCHED:
        return True
    try:
        member_logs_command = dank_group.get_command("member-logs")
        if member_logs_command is None:
            return False
        current_member_callback = getattr(member_logs_command, "callback", None)
        if not callable(current_member_callback):
            return False

        original_modlog = _ORIGINAL_MODLOG_HEALTH or modlog.open_modlog_health
        previous_modlog = modlog.open_modlog_health
        previous_original_modlog = _ORIGINAL_MODLOG_HEALTH
        try:
            _ORIGINAL_MODLOG_HEALTH = original_modlog
            modlog.open_modlog_health = open_contextual_modlog_health
        except Exception:
            modlog.open_modlog_health = previous_modlog
            _ORIGINAL_MODLOG_HEALTH = previous_original_modlog
            raise

        _PATCHED = True
        return True
    except Exception as exc:
        try:
            print(f"⚠️ public_logging_contextual_permission_repair failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


__all__ = [
    "MODLOG_KEYS",
    "STAFF_AUDIT_KEYS",
    "ModlogHealthRepairView",
    "MemberLogsRepairView",
    "open_contextual_modlog_health",
    "attach_member_logs_contextual_repair",
    "apply_logging_contextual_permission_repair",
]
