from __future__ import annotations

from typing import Any

import discord

from .common import safe_defer
from .public_setup_group import _require_setup_permission, stoney_group


_ATTACHED = False

_REPLACEMENTS = {
    "/stoney setup-picker": "/stoney setup",
    "/stoney setup-assistant": "/stoney setup",
    "/stoney setup-defaults": "/stoney setup",
    "/stoney setup-find": "/stoney setup",
    "/stoney setup-logs": "/stoney setup",
    "/stoney setup-review": "/stoney setup",
    "/stoney setup-status": "/stoney setup",
    "/stoney setup-tickets": "/stoney setup",
    "/stoney setup-verify": "/stoney setup",
    "/stoney setup-verify-ids": "/stoney setup",
    "/stoney setup-access": "/stoney setup",
    "/stoney permission-check": "/stoney setup",
    "/stoney launch-check": "/stoney setup",
    "/stoney production-audit": "/stoney setup",
    "/stoney tickettool-check": "/stoney setup",
    "/stoney db-check": "/stoney setup",
    "`/stoney setup-picker`": "`/stoney setup`",
    "`/stoney setup-assistant`": "`/stoney setup`",
    "`/stoney setup-defaults`": "`/stoney setup`",
    "`/stoney setup-find`": "`/stoney setup`",
    "`/stoney setup-logs`": "`/stoney setup`",
    "`/stoney setup-review`": "`/stoney setup`",
    "`/stoney setup-status`": "`/stoney setup`",
    "`/stoney setup-tickets`": "`/stoney setup`",
    "`/stoney setup-verify`": "`/stoney setup`",
    "`/stoney setup-verify-ids`": "`/stoney setup`",
    "`/stoney setup-access`": "`/stoney setup`",
    "`/stoney permission-check`": "`/stoney setup`",
    "`/stoney launch-check`": "`/stoney setup`",
    "`/stoney production-audit`": "`/stoney setup`",
    "`/stoney tickettool-check`": "`/stoney setup`",
    "`/stoney db-check`": "`/stoney setup`",
}


def _clean_text(value: Any) -> str:
    try:
        text = str(value or "")
    except Exception:
        return ""
    for old, new in _REPLACEMENTS.items():
        text = text.replace(old, new)
    text = text.replace("setup assistant", "quick setup")
    text = text.replace("Setup Assistant", "Quick Setup")
    return text


def _clean_embed(embed: discord.Embed) -> discord.Embed:
    try:
        if embed.title:
            embed.title = _clean_text(embed.title)
        if embed.description:
            embed.description = _clean_text(embed.description)[:4096]
        fields = list(getattr(embed, "fields", []) or [])
        if fields:
            embed.clear_fields()
            for field in fields:
                embed.add_field(
                    name=_clean_text(getattr(field, "name", ""))[:256] or "Status",
                    value=_clean_text(getattr(field, "value", ""))[:1024] or "—",
                    inline=bool(getattr(field, "inline", False)),
                )
        footer_text = getattr(getattr(embed, "footer", None), "text", "")
        if footer_text:
            embed.set_footer(text=_clean_text(footer_text))
    except Exception:
        pass
    return embed


def _install_cleaners(module: Any) -> None:
    try:
        if getattr(module, "_STONEY_SETUP_CLEANERS_INSTALLED", False):
            return
        original_health = getattr(module, "_health_embed", None)
        if callable(original_health):
            def cleaned_health(guild: discord.Guild, cfg: Any):
                return _clean_embed(original_health(guild, cfg))
            module._health_embed = cleaned_health
        original_payload = getattr(module, "_build_assistant_payload", None)
        if callable(original_payload):
            async def cleaned_payload(guild: discord.Guild):
                embed, view = await original_payload(guild)
                return _clean_embed(embed), view
            module._build_assistant_payload = cleaned_payload
        module._STONEY_SETUP_CLEANERS_INSTALLED = True
    except Exception as e:
        try:
            print(f"⚠️ public_setup_start cleaner install failed: {repr(e)}")
        except Exception:
            pass


async def _setup_callback(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return

    await safe_defer(interaction, ephemeral=True)

    guild = interaction.guild
    if guild is None:
        return await interaction.followup.send("❌ This command must be used inside a server.", ephemeral=True)

    try:
        from . import public_setup_assistant

        _install_cleaners(public_setup_assistant)
        embed, view = await public_setup_assistant._build_assistant_payload(guild)
        embed = _clean_embed(embed)
        embed.title = "🚀 Stoney Quick Setup"
        embed.description = (
            "This is the main setup screen. Pick the easiest path below:\n\n"
            "✨ **Auto-Fix Missing Defaults** creates only missing default roles/channels.\n"
            "✏️ **Customize Missing Names** lets you rename missing items first.\n"
            "🧩 **Choose Existing Items** is for servers that already have their own layout.\n\n"
            f"{embed.description or ''}"
        )[:4096]
        embed = _clean_embed(embed)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Setup failed: `{repr(e)[:300]}`", ephemeral=True)


def _attach() -> None:
    global _ATTACHED
    if _ATTACHED:
        return
    try:
        existing = stoney_group.get_command("setup")
    except Exception:
        existing = None
    if existing is not None:
        _ATTACHED = True
        return
    stoney_group.add_command(
        discord.app_commands.Command(
            name="setup",
            description="Start the guided Stoney setup flow.",
            callback=_setup_callback,
        )
    )
    _ATTACHED = True


_attach()


def register_public_setup_start_commands(bot: Any, tree: Any) -> None:
    _ = bot, tree
    _attach()
    try:
        print("✅ public_setup_start: attached /stoney setup quick-start command")
    except Exception:
        pass


__all__ = ["register_public_setup_start_commands"]
