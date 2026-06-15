from __future__ import annotations

"""All-in-one setup overview for public guild owners.

Read-only by design: this command does not create channels, roles, messages, panels,
or mutate guild config. It only reports what is already configured and points owners
to the next setup command. That keeps it safe for multi-server public use.
"""

from typing import Any, Iterable, Mapping, Optional

import discord

from ..guild_context import GuildContext, get_guild_context
from ..interaction_guard import run_guarded_interaction, safe_send_interaction
from .public_setup_group import stoney_group

_ATTACHED = False

PRONOUN_ROLE_NAMES: tuple[str, ...] = (
    "Pronouns: he/him",
    "Pronouns: she/her",
    "Pronouns: they/them",
    "Pronouns: he/they",
    "Pronouns: she/they",
    "Pronouns: it/its",
    "Pronouns: any pronouns",
    "Pronouns: no pronouns",
    "Pronouns: ask me",
    "Pronouns: custom",
)

IDENTITY_ROLE_NAMES: tuple[str, ...] = (
    "Identity: man",
    "Identity: woman",
    "Identity: non-binary",
    "Identity: genderfluid",
    "Identity: agender",
    "Identity: trans",
    "Identity: questioning",
    "Identity: prefer not to say",
    "Identity: custom / ask staff",
)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _admin_or_manage_guild(interaction: discord.Interaction) -> bool:
    try:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return False
        perms = interaction.user.guild_permissions
        return bool(perms.administrator or perms.manage_guild)
    except Exception:
        return False


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
            if isinstance(nested, Mapping) and nested.get(key) is not None:
                return nested.get(key)
        except Exception:
            pass
        try:
            if hasattr(cfg, "get"):
                nested = cfg.get(bucket)
                if isinstance(nested, Mapping) and nested.get(key) is not None:
                    return nested.get(key)
        except Exception:
            pass
    return default


def _cfg_snowflake(cfg: Any, *names: str) -> int:
    for name in names:
        out = _safe_int(_cfg_value(cfg, name, None), 0)
        if out > 0:
            return out
    return 0


def _cfg_bool(cfg: Any, *names: str, default: bool = False) -> bool:
    for name in names:
        raw = _cfg_value(cfg, name, None)
        if raw is not None:
            if isinstance(raw, bool):
                return raw
            return str(raw).strip().lower() in {"1", "true", "yes", "y", "on", "enabled"}
    return bool(default)


def _channel(guild: discord.Guild, channel_id: int) -> Optional[discord.abc.GuildChannel]:
    cid = _safe_int(channel_id, 0)
    return guild.get_channel(cid) if cid > 0 else None


def _channel_ready(guild: discord.Guild, cfg: Any, *keys: str) -> tuple[bool, str]:
    cid = _cfg_snowflake(cfg, *keys)
    channel = _channel(guild, cid)
    if channel is None:
        return False, "Not set"
    return True, f"{channel.mention}"


def _role_ready(guild: discord.Guild, cfg: Any, *keys: str) -> tuple[bool, str]:
    rid = _cfg_snowflake(cfg, *keys)
    role = guild.get_role(rid) if rid > 0 else None
    if role is None:
        return False, "Not set"
    return True, role.mention


def _has_role_named(guild: discord.Guild, names: Iterable[str]) -> int:
    wanted = {str(name).strip().casefold() for name in names}
    count = 0
    for role in list(getattr(guild, "roles", []) or []):
        try:
            if str(role.name).strip().casefold() in wanted:
                count += 1
        except Exception:
            continue
    return count


def _status(ok: bool, *, partial: bool = False) -> str:
    if ok:
        return "✅ Ready"
    if partial:
        return "🟡 Partial"
    return "⚪ Not set"


def _bot_channel_perms(channel: Optional[discord.abc.GuildChannel], guild: discord.Guild) -> list[str]:
    if not isinstance(channel, discord.TextChannel):
        return ["Not a text channel"]
    member = guild.me
    if not isinstance(member, discord.Member):
        return ["Could not resolve bot member"]
    perms = channel.permissions_for(member)
    checks = {
        "View Channel": perms.view_channel,
        "Send Messages": perms.send_messages,
        "Embed Links": perms.embed_links,
        "Read Message History": perms.read_message_history,
    }
    return [name for name, ok in checks.items() if not ok]


def _line(label: str, ready: bool, detail: str) -> str:
    return f"{_status(ready)} **{label}** — {detail}"


def _add_module(embed: discord.Embed, *, name: str, lines: list[str], action: str, ready: bool, partial: bool = False) -> None:
    value = "\n".join(lines + [f"**Next:** `{action}`"])
    emoji = "✅" if ready else "🟡" if partial else "⚪"
    embed.add_field(name=f"{emoji} {name}", value=value[:1024], inline=False)


def _guild_context_lines(context: GuildContext) -> list[str]:
    return [
        _line("Config source", bool(context.source), f"`{context.source}`"),
        _line("Public config isolation", context.public_config_isolation, "Enabled" if context.public_config_isolation else "Disabled"),
        _line("Unsafe to run mutations", not context.unsafe_to_act, "No" if not context.unsafe_to_act else "Yes — run setup first"),
        _line("Tickets", context.ticket_ready, "Ready" if context.ticket_ready else ", ".join(context.missing_ticket_keys) or "Not ready"),
        _line("Verification", context.verify_ready, "Ready" if context.verify_ready else ", ".join(context.missing_verify_keys) or "Not ready"),
        _line("Logging", context.logging_ready, "Ready" if context.logging_ready else ", ".join(context.missing_log_keys) or "Not ready"),
    ]


@stoney_group.command(name="overview", description="Show one clean setup checklist for this server.")
async def setup_overview(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await safe_send_interaction(
            interaction,
            content="❌ This command must be used inside a server.",
            ephemeral=True,
        )
        return

    if not _admin_or_manage_guild(interaction):
        await safe_send_interaction(
            interaction,
            content="❌ Server setup requires **Administrator** or **Manage Server** permission.",
            ephemeral=True,
        )
        return

    guild = interaction.guild

    async def _run() -> None:
        context = await get_guild_context(int(guild.id), refresh=True)
        cfg = context.config
        embed = discord.Embed(
            title="🧭 Dank Shield Setup Overview",
            description=(
                f"Server: **{guild.name}** (`{guild.id}`)\n"
                "This screen is read-only. It does not change settings, create roles, post panels, or overwrite existing config."
            ),
            color=discord.Color.red() if context.unsafe_to_act else discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )

        _add_module(
            embed,
            name="Config Safety",
            ready=not context.unsafe_to_act,
            partial=not context.unsafe_to_act,
            lines=_guild_context_lines(context),
            action="/dank diagnostics",
        )

        ticket_category_ok, ticket_category = _channel_ready(guild, cfg, "ticket_category_id", "active_ticket_category_id", "open_ticket_category_id")
        staff_ok, staff_role = _role_ready(guild, cfg, "staff_role_id", "ticket_staff_role_id", "vc_staff_role_id")
        transcripts_ok, transcripts = _channel_ready(guild, cfg, "transcripts_channel_id", "ticket_transcripts_channel_id", "transcript_channel_id")
        tickets_ready = ticket_category_ok and staff_ok
        _add_module(
            embed,
            name="Tickets",
            ready=tickets_ready,
            partial=ticket_category_ok or staff_ok or transcripts_ok,
            lines=[
                _line("Open category", ticket_category_ok, ticket_category),
                _line("Staff role", staff_ok, staff_role),
                _line("Transcripts", transcripts_ok, transcripts),
            ],
            action="/ticket-panel health",
        )

        verify_channel_ok, verify_channel = _channel_ready(guild, cfg, "verify_channel_id", "verification_channel_id")
        unverified_ok, unverified = _role_ready(guild, cfg, "unverified_role_id")
        verified_ok, verified = _role_ready(guild, cfg, "verified_role_id")
        verify_ready = verify_channel_ok and unverified_ok and verified_ok
        _add_module(
            embed,
            name="Verification",
            ready=verify_ready,
            partial=verify_channel_ok or unverified_ok or verified_ok,
            lines=[
                _line("Verify channel", verify_channel_ok, verify_channel),
                _line("Unverified role", unverified_ok, unverified),
                _line("Verified role", verified_ok, verified),
            ],
            action="/dank setup",
        )

        welcome_channel_ok, welcome_channel = _channel_ready(guild, cfg, "welcome_channel_id", "start_channel_id")
        welcome_msg_ok = _safe_int(_cfg_value(cfg, "welcome_message_id", 0), 0) > 0
        join_auto_ok = _cfg_bool(cfg, "welcome_join_enabled", "join_welcome_enabled", default=False)
        _add_module(
            embed,
            name="Welcome",
            ready=welcome_channel_ok and (welcome_msg_ok or join_auto_ok),
            partial=welcome_channel_ok or welcome_msg_ok or join_auto_ok,
            lines=[
                _line("Welcome channel", welcome_channel_ok, welcome_channel),
                _line("Pinned/start message", welcome_msg_ok, "Saved" if welcome_msg_ok else "Not posted"),
                _line("Join automation", join_auto_ok, "Enabled" if join_auto_ok else "Disabled"),
            ],
            action="/dank welcome health",
        )

        modlog_ok, modlog = _channel_ready(guild, cfg, "modlog_channel_id", "mod_log_channel_id", "logs_channel_id")
        modlog_channel = _channel(guild, _cfg_snowflake(cfg, "modlog_channel_id", "mod_log_channel_id", "logs_channel_id"))
        missing_modlog_perms = _bot_channel_perms(modlog_channel, guild) if modlog_channel is not None else []
        _add_module(
            embed,
            name="Modlog",
            ready=modlog_ok and not missing_modlog_perms,
            partial=modlog_ok,
            lines=[
                _line("Log channel", modlog_ok, modlog),
                _line("Bot permissions", not missing_modlog_perms and modlog_ok, "Ready" if not missing_modlog_perms and modlog_ok else ", ".join(missing_modlog_perms) or "Not checked"),
            ],
            action="/dank modlog health",
        )

        pronoun_count = _has_role_named(guild, PRONOUN_ROLE_NAMES)
        identity_count = _has_role_named(guild, IDENTITY_ROLE_NAMES)
        self_roles_ready = pronoun_count >= 1 or identity_count >= 1
        _add_module(
            embed,
            name="Self Roles",
            ready=self_roles_ready,
            partial=self_roles_ready,
            lines=[
                _line("Pronoun roles", pronoun_count > 0, f"{pronoun_count}/{len(PRONOUN_ROLE_NAMES)} found"),
                _line("Identity roles", identity_count > 0, f"{identity_count}/{len(IDENTITY_ROLE_NAMES)} found"),
                "✅ Cosmetic-only safety note is included on generated panels.",
            ],
            action="/dank roles pronouns",
        )

        automod_enabled = _cfg_bool(cfg, "automod_enabled", default=False)
        automod_preset = str(_cfg_value(cfg, "automod_preset", "custom") or "custom")
        bad_words = [x for x in str(_cfg_value(cfg, "automod_bad_words", "") or "").replace("\n", ",").split(",") if x.strip()]
        _add_module(
            embed,
            name="Automod",
            ready=automod_enabled,
            partial=bool(bad_words) or automod_preset != "custom",
            lines=[
                _line("Enabled", automod_enabled, "Yes" if automod_enabled else "No"),
                _line("Preset", automod_preset != "custom", automod_preset),
                _line("Bad-word filters", bool(bad_words), f"{len(bad_words)} saved"),
            ],
            action="/dank automod health",
        )

        _add_module(
            embed,
            name="Embed Builder",
            ready=True,
            lines=[
                "✅ Available as a safe private-preview flow.",
                "✅ Does not post publicly until staff presses Send Embed.",
            ],
            action="/dank embed health",
        )

        embed.set_footer(text="Dank Shield overview: read-only, per-guild, no hidden config changes")
        sent = await safe_send_interaction(
            interaction,
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        if not sent:
            raise RuntimeError("Setup overview response could not be sent to Discord.")

    await run_guarded_interaction(
        interaction,
        _run,
        defer=True,
        ephemeral=True,
        error_title="❌ Setup overview failed safely",
        error_guidance="Nothing was changed. Retry `/dank overview`, then check `/dank diagnostics` if it keeps failing.",
    )


def register_public_setup_overview_commands(bot: Any, tree: Any) -> None:
    _ = bot, tree
    global _ATTACHED
    if _ATTACHED:
        return
    # The command is declared directly on the Dank Shield command group at import time.
    # This function exists so registration stays explicit and consistent with the other public modules.
    _ATTACHED = True
    try:
        print("✅ public_setup_overview: attached /dank overview checklist")
    except Exception:
        pass


__all__ = ["register_public_setup_overview_commands"]
