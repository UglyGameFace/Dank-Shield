from __future__ import annotations

"""Private Verizon Shine/myAccess alert-only command module.

Safety contract:
- read-only alerts only
- no Verizon credentials
- no auto-claiming
- no CAPTCHA bypass
- no protected account scraping
"""

import os
from typing import Any, Optional

import discord
from discord import app_commands

from .common import safe_defer, safe_followup
from ..globals import GUILD_ID, is_staff
from ..verizon_rewards import repository
from ..verizon_rewards.embeds import build_status_embed
from ..verizon_rewards.service import build_digest, scan_text, send_test_alert, summarize_scan_result

_REGISTERED = False

verizon_group = app_commands.Group(
    name="verizon",
    description="Private Verizon Shine/myAccess reward alerts",
)

reminders_group = app_commands.Group(
    name="reminders",
    description="Turn Verizon reward reminders on or off",
)
verizon_group.add_command(reminders_group)


def _env_bool(name: str, default: bool = True) -> bool:
    raw = str(os.getenv(name, "") or "").strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "y", "on"}


def _env_guild_ids() -> list[int]:
    raw = str(os.getenv("VERIZON_REWARDS_GUILD_IDS", "") or "").strip()
    if not raw and int(GUILD_ID or 0) > 0:
        raw = str(int(GUILD_ID))
    out: list[int] = []
    for part in raw.split(","):
        try:
            gid = int(part.strip())
        except Exception:
            continue
        if gid > 0 and gid not in out:
            out.append(gid)
    return out


def _user_is_manager(interaction: discord.Interaction) -> bool:
    try:
        user = interaction.user
        if not isinstance(user, discord.Member):
            return False
        perms = user.guild_permissions
        return bool(perms.administrator or perms.manage_guild)
    except Exception:
        return False


def _user_is_staff(interaction: discord.Interaction) -> bool:
    try:
        user = interaction.user
        if not isinstance(user, discord.Member):
            return False
        perms = user.guild_permissions
        if perms.administrator or perms.manage_guild or perms.manage_messages or perms.moderate_members:
            return True
        return bool(is_staff(user))
    except Exception:
        return False


async def _require_staff(interaction: discord.Interaction) -> bool:
    if _user_is_staff(interaction):
        return True
    await safe_followup(interaction, "🚫 Verizon alerts are staff-only.", ephemeral=True)
    return False


async def _require_manager(interaction: discord.Interaction) -> bool:
    if _user_is_manager(interaction):
        return True
    await safe_followup(interaction, "🚫 Only server managers can change Verizon alert setup.", ephemeral=True)
    return False


def _guild_id(interaction: discord.Interaction) -> int:
    try:
        return int(interaction.guild_id or 0)
    except Exception:
        return 0


@verizon_group.command(name="setup", description="Configure private Verizon Shine/myAccess reward alerts")
@app_commands.describe(
    alert_channel="Channel where Verizon reward alerts should post",
    enabled="Turn alerts on after saving setup",
)
async def verizon_setup(
    interaction: discord.Interaction,
    alert_channel: discord.TextChannel,
    enabled: bool = True,
) -> None:
    await safe_defer(interaction, ephemeral=True)
    if not await _require_manager(interaction):
        return

    gid = _guild_id(interaction)
    if gid <= 0:
        await safe_followup(interaction, "Could not resolve this server.", ephemeral=True)
        return

    cfg = await repository.get_config(gid)
    cfg.alert_channel_id = int(alert_channel.id)
    cfg.enabled = bool(enabled)
    cfg.updated_by = str(getattr(interaction.user, "id", "") or "")
    saved = await repository.save_config(cfg)

    await safe_followup(
        interaction,
        (
            f"✅ Verizon alerts saved for {alert_channel.mention}.\n"
            f"Enabled: **{'yes' if saved.enabled else 'no'}**\n"
            "Safety mode: **read-only alerts only**. No Verizon login or claiming is used."
        ),
        ephemeral=True,
    )


@verizon_group.command(name="status", description="Show Verizon alert setup and safety state")
async def verizon_status(interaction: discord.Interaction) -> None:
    await safe_defer(interaction, ephemeral=True)
    if not await _require_staff(interaction):
        return

    gid = _guild_id(interaction)
    cfg = await repository.get_config(gid)
    rewards = await repository.list_rewards(gid, limit=1)
    await safe_followup(interaction, embed=build_status_embed(cfg, stored_rewards=len(rewards)), ephemeral=True)


@verizon_group.command(name="scan", description="Paste Shine reward text/countdown and alert only if new or changed")
@app_commands.describe(
    text="Paste reward title, status, countdown, available time, or screenshot summary",
    screenshot="Optional screenshot attachment for staff reference; paste text too because OCR is not enabled",
)
async def verizon_scan(
    interaction: discord.Interaction,
    text: Optional[str] = None,
    screenshot: Optional[discord.Attachment] = None,
) -> None:
    await safe_defer(interaction, ephemeral=True)
    if not await _require_staff(interaction):
        return

    gid = _guild_id(interaction)
    raw_text = str(text or "").strip()
    if screenshot is not None:
        raw_text = (raw_text + f"\n\nScreenshot reference: {screenshot.filename} {screenshot.url}").strip()

    if not raw_text:
        await safe_followup(
            interaction,
            "Paste the Shine reward text or screenshot summary. I do not OCR screenshots in Phase 1.",
            ephemeral=True,
        )
        return

    result = await scan_text(
        bot=interaction.client,
        guild_id=gid,
        text=raw_text,
        source="manual-scan",
    )
    await safe_followup(interaction, summarize_scan_result(result), ephemeral=True)


@verizon_group.command(name="test-alert", description="Post a safe fake Verizon Shine alert to confirm setup")
async def verizon_test_alert(interaction: discord.Interaction) -> None:
    await safe_defer(interaction, ephemeral=True)
    if not await _require_staff(interaction):
        return

    result = await send_test_alert(interaction.client, _guild_id(interaction))
    await safe_followup(interaction, summarize_scan_result(result), ephemeral=True)


@verizon_group.command(name="add-keyword", description="Add a priority keyword for Verizon reward alerts")
@app_commands.describe(keyword="Keyword to treat as higher priority")
async def verizon_add_keyword(interaction: discord.Interaction, keyword: str) -> None:
    await safe_defer(interaction, ephemeral=True)
    if not await _require_staff(interaction):
        return

    cfg, changed = await repository.add_keyword(_guild_id(interaction), keyword, updated_by=str(getattr(interaction.user, "id", "") or ""))
    if changed:
        await safe_followup(interaction, f"✅ Added priority keyword: `{keyword.strip().lower()}`", ephemeral=True)
    else:
        await safe_followup(interaction, "That keyword is already listed or was empty.", ephemeral=True)


@verizon_group.command(name="remove-keyword", description="Remove a Verizon reward priority keyword")
@app_commands.describe(keyword="Keyword to remove")
async def verizon_remove_keyword(interaction: discord.Interaction, keyword: str) -> None:
    await safe_defer(interaction, ephemeral=True)
    if not await _require_staff(interaction):
        return

    cfg, changed = await repository.remove_keyword(_guild_id(interaction), keyword, updated_by=str(getattr(interaction.user, "id", "") or ""))
    if changed:
        await safe_followup(interaction, f"✅ Removed priority keyword: `{keyword.strip().lower()}`", ephemeral=True)
    else:
        await safe_followup(interaction, "That keyword was not in the list.", ephemeral=True)


@verizon_group.command(name="list-keywords", description="List Verizon reward priority keywords")
async def verizon_list_keywords(interaction: discord.Interaction) -> None:
    await safe_defer(interaction, ephemeral=True)
    if not await _require_staff(interaction):
        return

    cfg = await repository.get_config(_guild_id(interaction))
    keywords = ", ".join(f"`{k}`" for k in cfg.priority_keywords)
    await safe_followup(interaction, keywords or "No priority keywords configured.", ephemeral=True)


@reminders_group.command(name="on", description="Turn Verizon reward opening reminders on")
async def verizon_reminders_on(interaction: discord.Interaction) -> None:
    await safe_defer(interaction, ephemeral=True)
    if not await _require_staff(interaction):
        return

    cfg = await repository.patch_config(
        _guild_id(interaction),
        reminders_enabled=True,
        updated_by=str(getattr(interaction.user, "id", "") or ""),
    )
    await safe_followup(interaction, "✅ Verizon reward reminders are now **on**.", ephemeral=True)


@reminders_group.command(name="off", description="Turn Verizon reward opening reminders off")
async def verizon_reminders_off(interaction: discord.Interaction) -> None:
    await safe_defer(interaction, ephemeral=True)
    if not await _require_staff(interaction):
        return

    cfg = await repository.patch_config(
        _guild_id(interaction),
        reminders_enabled=False,
        updated_by=str(getattr(interaction.user, "id", "") or ""),
    )
    await safe_followup(interaction, "✅ Verizon reward reminders are now **off**.", ephemeral=True)


@verizon_group.command(name="digest", description="Show recent saved Verizon reward alerts")
@app_commands.describe(post="Post the digest publicly to the alert channel instead of only showing you")
async def verizon_digest(interaction: discord.Interaction, post: bool = False) -> None:
    await safe_defer(interaction, ephemeral=True)
    if not await _require_staff(interaction):
        return

    embed, count = await build_digest(interaction.client, _guild_id(interaction), post=bool(post))
    await safe_followup(interaction, embed=embed, ephemeral=True)


def _attach_startup_hooks(bot: discord.Client) -> None:
    try:
        from ..verizon_rewards.schema import attach_schema_listener
        from ..verizon_rewards.relay_api import attach_relay_listener

        attach_schema_listener(bot)
        attach_relay_listener(bot)
    except Exception as e:
        try:
            print(f"⚠️ private_verizon_rewards failed to attach startup hooks: {repr(e)}")
        except Exception:
            pass

    if getattr(bot, "_verizon_rewards_recovery_listener_attached", False):
        return

    @bot.listen("on_ready")
    async def _verizon_rewards_recover_on_ready() -> None:
        try:
            from ..verizon_rewards.service import recover_pending_reminders

            count = await recover_pending_reminders(bot)
            if count:
                print(f"⏰ verizon_rewards recovered pending reminders count={count}")
        except Exception as e:
            print(f"⚠️ verizon_rewards reminder recovery failed: {repr(e)}")

    try:
        setattr(bot, "_verizon_rewards_recovery_listener_attached", True)
    except Exception:
        pass


def register_private_verizon_rewards_commands(bot: Any, tree: Any) -> None:
    global _REGISTERED
    if _REGISTERED:
        return
    if not _env_bool("VERIZON_REWARDS_COMMANDS_ENABLED", True):
        print("ℹ️ private_verizon_rewards commands disabled by VERIZON_REWARDS_COMMANDS_ENABLED=false")
        return

    _attach_startup_hooks(bot)

    guild_ids = _env_guild_ids()
    added = 0

    if not guild_ids:
        print(
            "ℹ️ private_verizon_rewards skipped command registration: "
            "set VERIZON_REWARDS_GUILD_IDS to keep /verizon private-server scoped."
        )
        _REGISTERED = True
        return

    for gid in guild_ids:
        try:
            tree.add_command(verizon_group, guild=discord.Object(id=int(gid)))
            added += 1
        except Exception as e:
            print(f"⚠️ private_verizon_rewards failed to add guild command guild={gid}: {repr(e)}")

    _REGISTERED = True
    print(f"✅ private_verizon_rewards registered /verizon commands targets={guild_ids} added={added}")
