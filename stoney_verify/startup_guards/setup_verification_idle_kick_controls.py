from __future__ import annotations

"""Add per-server setup controls for verification idle kick.

This is intentionally per guild and off by default. No deployment env IDs or
server-specific hardcoding are used.
"""

from typing import Any

import discord

_PATCHED = False


def _log(message: str) -> None:
    try:
        print(f"🧩 setup_verification_idle_kick_controls {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ setup_verification_idle_kick_controls {message}")
    except Exception:
        pass


def _safe_int(value: Any, default: int = 60) -> int:
    try:
        text = str(value or "").strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


class VerificationIdleKickModal(discord.ui.Modal, title="Idle Verification Kick"):
    minutes = discord.ui.TextInput(
        label="Minutes before removal",
        placeholder="60",
        default="60",
        min_length=1,
        max_length=5,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            from stoney_verify.commands_ext.public_setup_solid import _require_setup_permission, _save_config
        except Exception:
            return await interaction.response.send_message("❌ Setup module unavailable.", ephemeral=True)
        if not await _require_setup_permission(interaction):
            return
        mins = max(5, min(10080, _safe_int(self.minutes.value, 60)))
        await _save_config(
            interaction,
            {
                "verification_idle_kick_enabled": True,
                "verification_idle_kick_minutes": str(mins),
            },
        )
        embed = discord.Embed(
            title="✅ Verification Idle Kick Enabled",
            description=(
                f"New pending/unverified members will be removed after **{mins} minutes** if they never start verification progress.\n\n"
                "Progress means they either open/start a verification ticket or gain a configured safe access role."
            ),
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


class VerificationIdleKickControlsView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=300)

    @discord.ui.button(label="Enable / Set Minutes", emoji="⏳", style=discord.ButtonStyle.success, row=0)
    async def enable(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        try:
            from stoney_verify.commands_ext.public_setup_solid import _require_setup_permission
        except Exception:
            return await interaction.response.send_message("❌ Setup module unavailable.", ephemeral=True)
        if not await _require_setup_permission(interaction):
            return
        await interaction.response.send_modal(VerificationIdleKickModal())

    @discord.ui.button(label="Disable", emoji="🛑", style=discord.ButtonStyle.danger, row=0)
    async def disable(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        try:
            from stoney_verify.commands_ext.public_setup_solid import _require_setup_permission, _save_config
        except Exception:
            return await interaction.response.send_message("❌ Setup module unavailable.", ephemeral=True)
        if not await _require_setup_permission(interaction):
            return
        await _save_config(
            interaction,
            {
                "verification_idle_kick_enabled": False,
                "verification_idle_kick_minutes": "60",
            },
        )
        try:
            from stoney_verify.startup_guards.verification_idle_kick_feature import cancel_timer
            guild = interaction.guild
            if guild is not None:
                for member in list(getattr(guild, "members", []) or []):
                    await cancel_timer(guild.id, member.id)
        except Exception:
            pass
        embed = discord.Embed(
            title="✅ Verification Idle Kick Disabled",
            description="New pending/unverified members will not be auto-removed for failing to start verification.",
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


def _wrap_verification_channels_view(solid: Any) -> bool:
    cls = getattr(solid, "VerificationChannelsPickerView", None)
    if cls is None:
        return False
    original_init = getattr(cls, "__init__", None)
    if not callable(original_init) or getattr(original_init, "_idle_kick_controls_wrapped", False):
        return False

    def wrapped_init(self: discord.ui.View, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        try:
            self.add_item(_OpenIdleKickControlsButton(row=4))
        except Exception as e:
            _warn(f"failed adding idle kick button: {e!r}")

    setattr(wrapped_init, "_idle_kick_controls_wrapped", True)
    setattr(cls, "__init__", wrapped_init)
    return True


class _OpenIdleKickControlsButton(discord.ui.Button):
    def __init__(self, row: int = 4) -> None:
        super().__init__(
            label="No-Start Auto-Remove",
            emoji="⏳",
            style=discord.ButtonStyle.secondary,
            row=row,
            custom_id="dank_setup:verification_idle_kick_controls",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            from stoney_verify.commands_ext.public_setup_solid import _require_setup_permission
        except Exception:
            return await interaction.response.send_message("❌ Setup module unavailable.", ephemeral=True)
        if not await _require_setup_permission(interaction):
            return
        embed = discord.Embed(
            title="⏳ No-Start Verification Auto-Remove",
            description=(
                "Optional per-server feature. When enabled, new pending/unverified members are removed after your chosen delay "
                "if they never start verification progress.\n\n"
                "This is off by default for public servers. It never uses deployment/server env IDs."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Counts As Progress", value="Opening/starting a verification ticket or gaining a configured safe access role.", inline=False)
        embed.add_field(name="Recommended", value="Start with **60 minutes** for public/community servers.", inline=False)
        await interaction.response.send_message(embed=embed, view=VerificationIdleKickControlsView(), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        from stoney_verify.commands_ext import public_setup_solid as solid

        wrapped = _wrap_verification_channels_view(solid)
        _PATCHED = True
        _log(f"active wrapped_verification_channels={wrapped}")
        return True
    except Exception as e:
        _warn(f"failed: {e!r}")
        return False


apply()

__all__ = ["apply"]
