from __future__ import annotations

"""First-class public Ticket Panel command family.

Boring/professional command strategy:
- Keep one top-level global slash command: /ticket-panel
- Put the feature surface behind subcommands and subcommand groups
- Keep /ticket-intake post-panel as a compatibility alias
- Do not create throwaway top-level commands like /ticket_panel_rules_set

This keeps the public command count low while still exposing a TicketTool-style
admin surface.
"""

from typing import Any, Dict, List, Optional

import discord
from discord import app_commands

from ..globals import now_utc
from .common import _staff_check, reply_once, safe_defer
from .public_ticket_intake_group import ticket_intake_group


_ATTACHED_GROUP = False
_ATTACHED_INTAKE_ALIAS = False


TRANSCRIPT_MODE_CHOICES = [
    app_commands.Choice(name="On Close", value="on_close"),
    app_commands.Choice(name="Always", value="always"),
    app_commands.Choice(name="Manual", value="manual"),
    app_commands.Choice(name="Disabled", value="disabled"),
]


ticket_panel_group = app_commands.Group(
    name="ticket-panel",
    description="Manage and post ticket panels.",
)

rules_group = app_commands.Group(
    name="rules",
    description="View or edit ticket panel behavior rules.",
)

bootstrap_group = app_commands.Group(
    name="bootstrap",
    description="Run ticket panel bootstrap/self-heal tools.",
)


# ============================================================
# Small helpers
# ============================================================

def _log(message: str) -> None:
    try:
        print(f"✅ public_ticket_panel_commands: {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ public_ticket_panel_commands: {message}")
    except Exception:
        pass


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


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


def _slugify(value: Any, limit: int = 100) -> str:
    raw = _safe_str(value).lower().replace("&", " and ")
    out: List[str] = []
    prev_dash = False
    for ch in raw:
        if ch.isalnum():
            out.append(ch)
            prev_dash = False
        elif ch in {" ", "-", "_", "/", ":"}:
            if not prev_dash:
                out.append("-")
                prev_dash = True
    return "".join(out).strip("-")[:limit]


def _truncate(value: Any, limit: int = 1000) -> str:
    text = _safe_str(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _comma_slugs(value: Any) -> List[str]:
    raw = _safe_str(value)
    if not raw:
        return []
    out: List[str] = []
    for part in raw.split(","):
        slug = _slugify(part, limit=120)
        if slug and slug not in out:
            out.append(slug)
    return out


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


def _friendly_error(error: BaseException) -> str:
    text = repr(error)
    lowered = text.lower()
    if "ticket_panels" in lowered or "ticket_panel" in lowered or "pgrst205" in lowered:
        return (
            "The DB-backed panel tables are not installed yet. "
            "Run the ticket panel SQL migration in Supabase first."
        )
    return f"{type(error).__name__}: {_truncate(error, 220)}"


async def _configured_ticket_panel_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    try:
        from ..guild_config import get_guild_config

        cfg = await get_guild_config(guild.id, refresh=True)
        for attr in ("ticket_panel_channel_id", "support_channel_id", "verify_channel_id"):
            cid = _safe_int(getattr(cfg, attr, 0), 0)
            if cid <= 0:
                continue
            channel = guild.get_channel(cid)
            if isinstance(channel, discord.TextChannel):
                return channel
    except Exception:
        pass
    return None


def _public_panel_embed(guild: discord.Guild) -> discord.Embed:
    embed = discord.Embed(
        title="🎫 Need help? Open a ticket",
        description=(
            "Press **Create Ticket** below to open a private support ticket.\n\n"
            "A staff member will help you as soon as possible. Please include a clear reason when asked."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="How it works",
        value=(
            "1. Press **Create Ticket**\n"
            "2. Choose or describe what you need\n"
            "3. A private ticket channel opens for you and staff"
        ),
        inline=False,
    )
    embed.set_footer(text=f"{guild.name} • Stoney Verify ticket panel")
    return embed


def _panel_line(row: Dict[str, Any]) -> str:
    key = _safe_str(row.get("panel_key"), "unknown")
    name = _safe_str(row.get("panel_name"), key)
    enabled = "enabled" if _safe_bool(row.get("is_enabled"), True) else "disabled"
    style = _safe_str(row.get("panel_style"), "buttons")
    ch = _safe_int(row.get("panel_channel_id"), 0)
    msg = _safe_int(row.get("panel_message_id"), 0)
    location = f" • <#{ch}>" if ch > 0 else ""
    if ch > 0 and msg > 0:
        location += f" / `{msg}`"
    return f"• `{key}` — **{name}** • `{style}` • `{enabled}`{location}"


def _rules_text(rules: Dict[str, Any]) -> str:
    lines = [
        f"per_owner_open_limit: `{_safe_int(rules.get('per_owner_open_limit'), 1)}`",
        f"cooldown_seconds: `{_safe_int(rules.get('cooldown_seconds'), 0)}`",
        f"auto_close_enabled: `{_safe_bool(rules.get('auto_close_enabled'), False)}`",
        f"auto_close_minutes: `{_safe_int(rules.get('auto_close_minutes'), 1440)}`",
        f"inactivity_reminders_enabled: `{_safe_bool(rules.get('inactivity_reminders_enabled'), True)}`",
        f"inactivity_reminder_minutes: `{_safe_int(rules.get('inactivity_reminder_minutes'), 240)}`",
        f"allow_unverified: `{_safe_bool(rules.get('allow_unverified'), True)}`",
        f"allow_verified: `{_safe_bool(rules.get('allow_verified'), True)}`",
        f"allow_resident: `{_safe_bool(rules.get('allow_resident'), True)}`",
        f"allow_staff: `{_safe_bool(rules.get('allow_staff'), True)}`",
        f"allow_unknown_members: `{_safe_bool(rules.get('allow_unknown_members'), True)}`",
        f"ghost_allowed: `{_safe_bool(rules.get('ghost_allowed'), False)}`",
        f"transcript_mode: `{_safe_str(rules.get('transcript_mode'), 'on_close')}`",
        f"close_confirmation_required: `{_safe_bool(rules.get('close_confirmation_required'), True)}`",
        f"staff_alert_channel_id: `{_safe_str(rules.get('staff_alert_channel_id'), 'not set')}`",
    ]
    return "\n".join(lines)


def _panel_embed_from_bundle(bundle: Dict[str, Any]) -> discord.Embed:
    panel = dict(bundle.get("panel") or {})
    rules = dict(bundle.get("rules") or {})
    categories = list(bundle.get("categories") or [])
    embed = discord.Embed(
        title=f"🎛️ Ticket Panel: {_safe_str(panel.get('panel_name'), 'Unnamed')}",
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.add_field(name="Panel Key", value=f"`{_safe_str(panel.get('panel_key'), 'unknown')}`", inline=True)
    embed.add_field(name="Style", value=f"`{_safe_str(panel.get('panel_style'), 'buttons')}`", inline=True)
    embed.add_field(name="Enabled", value=f"`{_safe_bool(panel.get('is_enabled'), True)}`", inline=True)
    embed.add_field(name="Prompt", value=_truncate(_safe_str(panel.get("prompt_description"), "not set"), 1000), inline=False)
    if categories:
        lines = [f"• `{_safe_str(row.get('category_slug'), 'unknown')}`" for row in categories[:25] if isinstance(row, dict)]
        embed.add_field(name="Bound Categories", value="\n".join(lines)[:1024] or "All categories are allowed.", inline=False)
    else:
        embed.add_field(name="Bound Categories", value="All categories are allowed.", inline=False)
    embed.add_field(name="Rules", value=_rules_text(rules)[:1024], inline=False)
    return embed


def _guild_result_line(row: Dict[str, Any]) -> str:
    ok = "✅" if _safe_bool(row.get("ok"), False) else "❌"
    guild_id = _safe_str(row.get("guild_id"), "unknown")
    guild_name = _safe_str(row.get("guild_name"), "Unknown Guild")
    parts: List[str] = [f"{ok} `{guild_id}` — **{guild_name}**"]
    if _safe_bool(row.get("saved_discovery"), False):
        parts.append("discovery saved")
    if _safe_bool(row.get("default_preset_ready"), False):
        parts.append("preset ready")
    if _safe_bool(row.get("default_panel_created"), False):
        parts.append("default panel created")
    repaired = _safe_int(row.get("rules_repaired"), 0)
    if repaired > 0:
        parts.append(f"rules repaired={repaired}")
    error = _safe_str(row.get("error"))
    if error:
        parts.append(f"error={_truncate(error, 160)}")
    return " • ".join(parts)


def _bootstrap_status_embed(status: Dict[str, Any]) -> discord.Embed:
    state = _safe_str(status.get("task_state"), "unknown")
    color = discord.Color.green() if state == "running" else discord.Color.orange()
    embed = discord.Embed(title="🧩 Panel Bootstrap Status", color=color, timestamp=now_utc())
    embed.add_field(name="Task State", value=f"`{state}`", inline=True)
    embed.add_field(name="Interval Seconds", value=f"`{_safe_int(status.get('interval_seconds'), 0)}`", inline=True)
    embed.add_field(name="Guild Concurrency", value=f"`{_safe_int(status.get('guild_concurrency'), 0)}`", inline=True)
    last_run = status.get("last_run")
    if isinstance(last_run, dict) and last_run:
        lines = [f"`{gid}` → `{ran_at}`" for gid, ran_at in list(last_run.items())[:15]]
        embed.add_field(name="Last Run", value=_truncate("\n".join(lines), 1024), inline=False)
    else:
        embed.add_field(name="Last Run", value="No guild bootstrap run recorded yet.", inline=False)
    last_error = status.get("last_error")
    if isinstance(last_error, dict) and last_error:
        lines = [f"`{gid}` → {_truncate(err, 140)}" for gid, err in list(last_error.items())[:10]]
        embed.add_field(name="Last Errors", value=_truncate("\n".join(lines), 1024), inline=False)
    return embed


def _single_bootstrap_embed(result: Dict[str, Any]) -> discord.Embed:
    ok = _safe_bool(result.get("ok"), False)
    embed = discord.Embed(
        title="🧩 Panel Bootstrap Result",
        color=discord.Color.green() if ok else discord.Color.red(),
        timestamp=now_utc(),
    )
    embed.add_field(name="Guild", value=f"{_safe_str(result.get('guild_name'), 'Unknown Guild')}\n`{_safe_str(result.get('guild_id'), 'unknown')}`", inline=False)
    embed.add_field(name="OK", value=f"`{ok}`", inline=True)
    embed.add_field(name="Discovery Saved", value=f"`{_safe_bool(result.get('saved_discovery'), False)}`", inline=True)
    embed.add_field(name="Default Preset Ready", value=f"`{_safe_bool(result.get('default_preset_ready'), False)}`", inline=True)
    embed.add_field(name="Default Panel Created", value=f"`{_safe_bool(result.get('default_panel_created'), False)}`", inline=True)
    embed.add_field(name="Rules Repaired", value=f"`{_safe_int(result.get('rules_repaired'), 0)}`", inline=True)
    if _safe_str(result.get("error")):
        embed.add_field(name="Error", value=_truncate(result.get("error"), 1024), inline=False)
    return embed


def _summary_bootstrap_embed(summary: Dict[str, Any]) -> discord.Embed:
    ok = _safe_bool(summary.get("ok"), False)
    embed = discord.Embed(title="🧩 Panel Bootstrap Summary", color=discord.Color.green() if ok else discord.Color.orange(), timestamp=now_utc())
    embed.add_field(name="Guilds Seen", value=f"`{_safe_int(summary.get('guilds_seen'), 0)}`", inline=True)
    embed.add_field(name="Guilds OK", value=f"`{_safe_int(summary.get('guilds_ok'), 0)}`", inline=True)
    embed.add_field(name="Guilds Failed", value=f"`{_safe_int(summary.get('guilds_failed'), 0)}`", inline=True)
    embed.add_field(name="Rules Repaired", value=f"`{_safe_int(summary.get('rules_repaired'), 0)}`", inline=True)
    embed.add_field(name="Default Panels Created", value=f"`{_safe_int(summary.get('default_panels_created'), 0)}`", inline=True)
    results = summary.get("results")
    if isinstance(results, list) and results:
        lines = [_guild_result_line(row) for row in results[:15] if isinstance(row, dict)]
        embed.add_field(name="Results", value=_truncate("\n".join(lines), 1024) or "No details.", inline=False)
    return embed


# ============================================================
# /ticket-panel commands
# ============================================================

@ticket_panel_group.command(name="post", description="Post the public Create Ticket button panel.")
@app_commands.describe(channel="Optional channel. Defaults to configured panel/support channel, then current channel.")
async def ticket_panel_post(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None) -> None:
    await post_ticket_panel_callback(interaction, channel=channel)


@ticket_panel_group.command(name="list", description="List DB-backed ticket panels for this server.")
async def ticket_panel_list(interaction: discord.Interaction) -> None:
    if not await _staff_only(interaction):
        return
    guild = await _guild_or_reply(interaction)
    if guild is None:
        return
    try:
        from ..tickets_new.panel_repository import list_ticket_panels

        rows = await list_ticket_panels(guild.id)
        embed = discord.Embed(title="🎛️ Ticket Panels", color=discord.Color.blurple(), timestamp=now_utc())
        embed.description = "\n".join(_panel_line(row) for row in rows[:25])[:4000] if rows else "No DB-backed panels have been created yet."
        await reply_once(interaction, {"embed": embed, "ephemeral": True})
    except Exception as e:
        await reply_once(interaction, {"content": f"❌ Panel list failed: {_friendly_error(e)}", "ephemeral": True})


@ticket_panel_group.command(name="show", description="Show a DB-backed ticket panel config.")
@app_commands.describe(panel_key="Panel key to inspect")
async def ticket_panel_show(interaction: discord.Interaction, panel_key: str) -> None:
    if not await _staff_only(interaction):
        return
    guild = await _guild_or_reply(interaction)
    if guild is None:
        return
    key = _slugify(panel_key)
    try:
        from ..tickets_new.panel_repository import get_ticket_panel_bundle

        bundle = await get_ticket_panel_bundle(guild.id, key)
        if not bundle:
            return await reply_once(interaction, {"content": f"❌ Panel `{key}` was not found.", "ephemeral": True})
        await reply_once(interaction, {"embed": _panel_embed_from_bundle(bundle), "ephemeral": True})
    except Exception as e:
        await reply_once(interaction, {"content": f"❌ Panel show failed: {_friendly_error(e)}", "ephemeral": True})


@ticket_panel_group.command(name="bind-categories", description="Bind allowed ticket category slugs to a panel.")
@app_commands.describe(panel_key="Panel key", categories="Comma-separated slugs. Example: support,appeal,report")
async def ticket_panel_bind_categories(interaction: discord.Interaction, panel_key: str, categories: str) -> None:
    if not await _staff_only(interaction):
        return
    guild = await _guild_or_reply(interaction)
    if guild is None:
        return
    key = _slugify(panel_key)
    slugs = _comma_slugs(categories)
    try:
        from ..tickets_new.panel_repository import replace_ticket_panel_categories

        rows = await replace_ticket_panel_categories(guild.id, key, slugs)
        category_text = ", ".join(f"`{_safe_str(row.get('category_slug'))}`" for row in rows[:20] if isinstance(row, dict) and row.get("category_slug"))
        if not category_text:
            category_text = "No categories; panel allows all categories."
        await reply_once(interaction, {"content": f"✅ Bound `{len(rows)}` categories to panel `{key}`.\n{category_text}", "ephemeral": True})
    except Exception as e:
        await reply_once(interaction, {"content": f"❌ Binding categories failed: {_friendly_error(e)}", "ephemeral": True})


@ticket_panel_group.command(name="runtime", description="Show the effective runtime config for a panel.")
@app_commands.describe(panel_key="Panel key")
async def ticket_panel_runtime(interaction: discord.Interaction, panel_key: str) -> None:
    if not await _staff_only(interaction):
        return
    guild = await _guild_or_reply(interaction)
    if guild is None:
        return
    key = _slugify(panel_key)
    try:
        from ..tickets_new.panel_repository import build_panel_runtime_config

        runtime = await build_panel_runtime_config(guild.id, key)
        if not runtime:
            return await reply_once(interaction, {"content": f"❌ Runtime config for `{key}` was not found.", "ephemeral": True})
        embed = discord.Embed(title=f"🧪 Panel Runtime: {key}", color=discord.Color.dark_teal(), timestamp=now_utc())
        embed.add_field(name="Panel", value=_truncate(str(runtime.get("panel") or {}), 1000), inline=False)
        embed.add_field(name="Rules", value=_truncate(str(runtime.get("rules") or {}), 1000), inline=False)
        embed.add_field(name="Categories", value=_truncate(str(runtime.get("categories") or []), 1000), inline=False)
        await reply_once(interaction, {"embed": embed, "ephemeral": True})
    except Exception as e:
        await reply_once(interaction, {"content": f"❌ Runtime lookup failed: {_friendly_error(e)}", "ephemeral": True})


# ============================================================
# /ticket-panel rules commands
# ============================================================

@rules_group.command(name="view", description="Show rules for a panel.")
@app_commands.describe(panel_key="Panel key")
async def ticket_panel_rules_view(interaction: discord.Interaction, panel_key: str) -> None:
    if not await _staff_only(interaction):
        return
    guild = await _guild_or_reply(interaction)
    if guild is None:
        return
    key = _slugify(panel_key)
    try:
        from ..tickets_new.panel_repository import get_ticket_panel_rules

        rules = await get_ticket_panel_rules(guild.id, key)
        embed = discord.Embed(title=f"⚙️ Panel Rules: {key}", description=_rules_text(rules), color=discord.Color.blurple(), timestamp=now_utc())
        await reply_once(interaction, {"embed": embed, "ephemeral": True})
    except Exception as e:
        await reply_once(interaction, {"content": f"❌ Rules lookup failed: {_friendly_error(e)}", "ephemeral": True})


@rules_group.command(name="set", description="Update common panel rules.")
@app_commands.describe(
    panel_key="Panel key",
    per_owner_open_limit="Open ticket limit per owner for this panel",
    auto_close_enabled="Enable auto-close",
    auto_close_minutes="Minutes before auto-close",
    inactivity_reminders_enabled="Enable inactivity reminders",
    inactivity_reminder_minutes="Minutes before inactivity reminder",
    transcript_mode="Transcript mode",
)
@app_commands.choices(transcript_mode=TRANSCRIPT_MODE_CHOICES)
async def ticket_panel_rules_set(
    interaction: discord.Interaction,
    panel_key: str,
    per_owner_open_limit: Optional[int] = None,
    auto_close_enabled: Optional[bool] = None,
    auto_close_minutes: Optional[int] = None,
    inactivity_reminders_enabled: Optional[bool] = None,
    inactivity_reminder_minutes: Optional[int] = None,
    transcript_mode: Optional[app_commands.Choice[str]] = None,
) -> None:
    if not await _staff_only(interaction):
        return
    guild = await _guild_or_reply(interaction)
    if guild is None:
        return
    key = _slugify(panel_key)
    try:
        from ..tickets_new.panel_repository import get_ticket_panel_rules, upsert_ticket_panel_rules

        current = await get_ticket_panel_rules(guild.id, key)
        patch: Dict[str, Any] = {"guild_id": str(guild.id), "panel_key": key, **current}
        if per_owner_open_limit is not None:
            patch["per_owner_open_limit"] = max(1, int(per_owner_open_limit))
        if auto_close_enabled is not None:
            patch["auto_close_enabled"] = bool(auto_close_enabled)
        if auto_close_minutes is not None:
            patch["auto_close_minutes"] = max(5, int(auto_close_minutes))
        if inactivity_reminders_enabled is not None:
            patch["inactivity_reminders_enabled"] = bool(inactivity_reminders_enabled)
        if inactivity_reminder_minutes is not None:
            patch["inactivity_reminder_minutes"] = max(1, int(inactivity_reminder_minutes))
        if transcript_mode is not None:
            patch["transcript_mode"] = transcript_mode.value
        updated = await upsert_ticket_panel_rules(patch)
        embed = discord.Embed(title=f"✅ Updated Panel Rules: {key}", description=_rules_text(updated), color=discord.Color.green(), timestamp=now_utc())
        await reply_once(interaction, {"embed": embed, "ephemeral": True})
    except Exception as e:
        await reply_once(interaction, {"content": f"❌ Rules update failed: {_friendly_error(e)}", "ephemeral": True})


# ============================================================
# /ticket-panel bootstrap commands
# ============================================================

@bootstrap_group.command(name="status", description="Show panel bootstrap worker status.")
async def ticket_panel_bootstrap_status(interaction: discord.Interaction) -> None:
    if not await _staff_only(interaction):
        return
    try:
        from ..tickets_new.panel_bootstrap import panel_bootstrap_status

        await reply_once(interaction, {"embed": _bootstrap_status_embed(panel_bootstrap_status()), "ephemeral": True})
    except Exception as e:
        await reply_once(interaction, {"content": f"❌ Bootstrap status failed: {_friendly_error(e)}", "ephemeral": True})


@bootstrap_group.command(name="run", description="Run panel bootstrap/self-heal for this server now.")
async def ticket_panel_bootstrap_run(interaction: discord.Interaction, save_discovery: bool = True, seed_default_panel: bool = True) -> None:
    if not await _staff_only(interaction):
        return
    guild = await _guild_or_reply(interaction)
    if guild is None:
        return
    await safe_defer(interaction, ephemeral=True)
    try:
        from ..tickets_new.panel_bootstrap import bootstrap_panel_system_for_guild

        result = await bootstrap_panel_system_for_guild(guild, save_discovery=save_discovery, seed_default_panel=seed_default_panel)
        await interaction.followup.send(embed=_single_bootstrap_embed(result), ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Bootstrap run failed: {_friendly_error(e)}", ephemeral=True)


@bootstrap_group.command(name="all", description="Run panel bootstrap/self-heal for every attached guild.")
async def ticket_panel_bootstrap_all(interaction: discord.Interaction, save_discovery: bool = True, seed_default_panel: bool = True) -> None:
    if not await _staff_only(interaction):
        return
    await safe_defer(interaction, ephemeral=True)
    try:
        from ..tickets_new.panel_bootstrap import bootstrap_panel_system_for_bot

        summary = await bootstrap_panel_system_for_bot(interaction.client, save_discovery=save_discovery, seed_default_panel=seed_default_panel)
        await interaction.followup.send(embed=_summary_bootstrap_embed(summary), ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Bootstrap all failed: {_friendly_error(e)}", ephemeral=True)


@bootstrap_group.command(name="start", description="Start the recurring panel bootstrap/self-heal worker.")
async def ticket_panel_bootstrap_start(interaction: discord.Interaction, save_discovery: bool = True, seed_default_panel: bool = True) -> None:
    if not await _staff_only(interaction):
        return
    try:
        from ..tickets_new.panel_bootstrap import panel_bootstrap_status, start_panel_bootstrap_worker

        task = start_panel_bootstrap_worker(interaction.client, save_discovery=save_discovery, seed_default_panel=seed_default_panel)
        content = "✅ Panel bootstrap worker started." if task is not None else "⚠️ Panel bootstrap worker did not start."
        await reply_once(interaction, {"content": content, "embed": _bootstrap_status_embed(panel_bootstrap_status()), "ephemeral": True})
    except Exception as e:
        await reply_once(interaction, {"content": f"❌ Bootstrap start failed: {_friendly_error(e)}", "ephemeral": True})


@bootstrap_group.command(name="once", description="Schedule one background panel bootstrap pass.")
async def ticket_panel_bootstrap_once(interaction: discord.Interaction, save_discovery: bool = True, seed_default_panel: bool = True) -> None:
    if not await _staff_only(interaction):
        return
    try:
        from ..tickets_new.panel_bootstrap import panel_bootstrap_status, start_panel_bootstrap_once

        task = start_panel_bootstrap_once(interaction.client, save_discovery=save_discovery, seed_default_panel=seed_default_panel)
        content = "✅ One-shot panel bootstrap scheduled." if task is not None else "⚠️ One-shot panel bootstrap was not scheduled."
        await reply_once(interaction, {"content": content, "embed": _bootstrap_status_embed(panel_bootstrap_status()), "ephemeral": True})
    except Exception as e:
        await reply_once(interaction, {"content": f"❌ Bootstrap once failed: {_friendly_error(e)}", "ephemeral": True})


@bootstrap_group.command(name="stop", description="Stop the recurring panel bootstrap/self-heal worker.")
async def ticket_panel_bootstrap_stop(interaction: discord.Interaction) -> None:
    if not await _staff_only(interaction):
        return
    await safe_defer(interaction, ephemeral=True)
    try:
        from ..tickets_new.panel_bootstrap import panel_bootstrap_status, stop_panel_bootstrap_worker

        await stop_panel_bootstrap_worker()
        await interaction.followup.send(content="✅ Panel bootstrap worker stopped.", embed=_bootstrap_status_embed(panel_bootstrap_status()), ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Bootstrap stop failed: {_friendly_error(e)}", ephemeral=True)


# Attach nested groups once at import time.
try:
    if ticket_panel_group.get_command("rules") is None:
        ticket_panel_group.add_command(rules_group)
except Exception:
    pass

try:
    if ticket_panel_group.get_command("bootstrap") is None:
        ticket_panel_group.add_command(bootstrap_group)
except Exception:
    pass


# ============================================================
# Backward-compatible alias: /ticket-intake post-panel
# ============================================================

async def post_ticket_panel_callback(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None) -> None:
    if not await _staff_only(interaction):
        return

    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
    except Exception:
        pass

    guild = interaction.guild
    if guild is None:
        return await reply_once(interaction, {"content": "❌ This command must be used inside a server.", "ephemeral": True})

    target = channel or await _configured_ticket_panel_channel(guild)
    if target is None and isinstance(interaction.channel, discord.TextChannel):
        target = interaction.channel
    if target is None:
        return await reply_once(interaction, {"content": "❌ I could not find a text channel to post the ticket panel. Pick a channel explicitly.", "ephemeral": True})

    me = guild.me
    if me is not None:
        perms = target.permissions_for(me)
        missing: list[str] = []
        if not perms.view_channel:
            missing.append("View Channel")
        if not perms.send_messages:
            missing.append("Send Messages")
        if not perms.embed_links:
            missing.append("Embed Links")
        if missing:
            return await reply_once(interaction, {"content": f"❌ I cannot post the ticket panel in {target.mention}. Missing: {', '.join(missing)}.", "ephemeral": True})

    try:
        from ..tickets_new.panel import TicketPanelView

        view = TicketPanelView()
    except Exception as e:
        return await reply_once(interaction, {"content": f"❌ Ticket panel button view is unavailable: `{type(e).__name__}: {_truncate(e, 180)}`", "ephemeral": True})

    try:
        msg = await target.send(embed=_public_panel_embed(guild), view=view, allowed_mentions=discord.AllowedMentions.none())
    except Exception as e:
        return await reply_once(interaction, {"content": f"❌ Failed posting ticket panel in {target.mention}: `{type(e).__name__}: {_truncate(e, 180)}`", "ephemeral": True})

    try:
        from .public_setup_config_writer import upsert_guild_config
        from ..guild_config import invalidate_guild_config

        await upsert_guild_config(
            guild.id,
            {
                "ticket_panel_channel_id": str(int(target.id)),
                "ticket_panel_message_id": str(int(msg.id)),
            },
        )
        invalidate_guild_config(guild.id)
    except Exception:
        pass

    await reply_once(interaction, {"content": f"✅ Posted the public **Create Ticket** panel in {target.mention}. Use `/ticket-panel post` next time.", "ephemeral": True})


post_ticket_panel_callback = app_commands.describe(  # type: ignore[assignment]
    channel="Optional channel. Defaults to configured support/ticket-panel channel, then current channel."
)(post_ticket_panel_callback)


def _add_intake_alias() -> bool:
    global _ATTACHED_INTAKE_ALIAS
    if _ATTACHED_INTAKE_ALIAS:
        return False
    try:
        if ticket_intake_group.get_command("post-panel") is not None:
            _ATTACHED_INTAKE_ALIAS = True
            return False
    except Exception:
        pass

    ticket_intake_group.add_command(
        app_commands.Command(
            name="post-panel",
            description="Post the public Create Ticket button panel for users.",
            callback=post_ticket_panel_callback,
        )
    )
    _ATTACHED_INTAKE_ALIAS = True
    return True


def _add_ticket_panel_group(tree: Any) -> bool:
    global _ATTACHED_GROUP
    if _ATTACHED_GROUP:
        return False

    try:
        existing = tree.get_command("ticket-panel", guild=None)
    except Exception:
        existing = None

    if existing is not None:
        # If an old single-command /ticket-panel is already attached in-memory,
        # remove it and replace it with the command family. This only affects
        # the local command tree before sync.
        try:
            if not isinstance(existing, app_commands.Group):
                tree.remove_command("ticket-panel", guild=None)
                existing = None
        except Exception:
            pass

    if existing is not None:
        _ATTACHED_GROUP = True
        return False

    tree.add_command(ticket_panel_group)
    _ATTACHED_GROUP = True
    return True


def register_public_ticket_panel_commands(bot: Any, tree: Any) -> None:
    _ = bot
    added_group = _add_ticket_panel_group(tree)
    added_alias = _add_intake_alias()
    added: list[str] = []
    if added_group:
        added.append("/ticket-panel group")
    if added_alias:
        added.append("/ticket-intake post-panel alias")
    _log("registered " + (", ".join(added) if added else "existing panel commands already present"))


__all__ = ["register_public_ticket_panel_commands", "post_ticket_panel_callback", "ticket_panel_group"]
