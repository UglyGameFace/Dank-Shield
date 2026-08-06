from __future__ import annotations

"""Compatibility entry point for Voice Verify setup permission repair.

All baseline Voice Verify permission writes go through the canonical
``vc_verification_permissions`` service. DS-SETUP-020 is installed here after
legacy setup guards so the owner-facing picker and guided setup share the same
canonical service state and permission policy.
"""

from typing import Any

import discord

_PATCHED = False
CUSTOM_ID = "stoney_solid:fix_vc_permissions"


async def _run_vc_permission_fix(
    interaction: discord.Interaction,
) -> tuple[list[str], list[str]]:
    guild = interaction.guild
    if guild is None:
        return [], ["This button must be used inside a server."]
    from stoney_verify.services.vc_verification_permissions import (
        reconcile_vc_verification_channel,
    )

    result = await reconcile_vc_verification_channel(
        guild,
        reason=(
            "Dank Shield setup VC permission repair by "
            f"{interaction.user} ({getattr(interaction.user, 'id', 0)})"
        ),
    )
    changed = [f"Reconciled Voice Verify access for {label}" for label in result.changed]
    return changed, list(result.failed)


async def _handle_fix_button(interaction: discord.Interaction) -> None:
    try:
        from stoney_verify.commands_ext import public_setup_solid
        from stoney_verify.commands_ext.public_setup_group import _require_setup_permission
    except Exception as exc:
        return await interaction.response.send_message(
            f"❌ VC repair is unavailable: `{type(exc).__name__}`",
            ephemeral=True,
        )
    if not await _require_setup_permission(interaction):
        return
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
    except Exception:
        pass
    changed, failed = await _run_vc_permission_fix(interaction)
    try:
        embed = await public_setup_solid._build_health_embed(interaction.guild)
    except Exception:
        embed = discord.Embed(title="🩺 Setup Health Check", color=discord.Color.blurple())
    if changed:
        embed.add_field(name="🔒 VC Permissions Repaired", value="\n".join(f"✅ {item}" for item in changed)[:1024], inline=False)
    if failed:
        embed.add_field(name="Still Needs Permission Help", value="\n".join(f"⚠️ {item}" for item in failed)[:1024], inline=False)
    await interaction.edit_original_response(embed=embed, view=public_setup_solid.SetupNavView())


def patch_setup_nav_with_vc_fix_button() -> bool:
    global _PATCHED
    _PATCHED = True
    return True


patch_setup_nav_with_vc_fix_button()

try:
    from stoney_verify.startup_guards.setup_020_entitled_id_guard import install as _install_ds_setup_020

    _install_ds_setup_020()
except Exception as exc:
    try:
        print(f"⚠️ DS-SETUP-020 setup integration failed to install: {type(exc).__name__}: {exc}")
    except Exception:
        pass


__all__ = ["patch_setup_nav_with_vc_fix_button", "_run_vc_permission_fix"]
