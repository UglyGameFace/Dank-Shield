from __future__ import annotations

"""Attach next-step buttons after setup dropdown saves.

When a server owner saves a role/channel/behavior option from Use Existing
Roles/Channels, do not leave them with a dead-end ephemeral confirmation. Give
buttons to refresh health, keep picking, or return to setup.
"""

from typing import Any, Optional

import discord

_PATCHED = False
_ORIGINAL_SEND_MESSAGE: Any = None
_SAVE_TITLE_PREFIXES = (
    "✅ Saved Setup Role",
    "✅ Saved Setup Channel",
    "✅ Saved Access Role Style",
    "✅ Saved Behavior Settings",
)


def _is_setup_save_embed(embed: Any) -> bool:
    try:
        title = str(getattr(embed, "title", "") or "")
        return any(title.startswith(prefix) for prefix in _SAVE_TITLE_PREFIXES)
    except Exception:
        return False


async def _open_health(interaction: discord.Interaction) -> None:
    try:
        from stoney_verify.commands_ext import public_setup_solid as solid

        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await solid._safe_defer_update(interaction)
        embed = await solid._build_health_embed(guild)
        # The canonical guided Setup Review owns the next-action view.
        await solid._edit_or_followup(interaction, embed=embed, view=solid.SetupNavView())
    except Exception as exc:
        try:
            await interaction.response.send_message(
                f"❌ Could not refresh health: `{type(exc).__name__}: {str(exc)[:250]}`",
                ephemeral=True,
            )
        except Exception:
            pass


async def _open_existing(interaction: discord.Interaction) -> None:
    try:
        from stoney_verify.commands_ext import public_setup_solid as solid

        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await solid._safe_defer_update(interaction)
        embed = discord.Embed(
            title="🧩 Use Existing Roles/Channels",
            description=(
                "Keep mapping the items your server already uses.\n"
                "Names do not matter — Dank Shield saves Discord IDs."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Recommended order",
            value=(
                "1. 🎫 Ticket Basics\n"
                "2. 🎭 Access Roles\n"
                "3. 🎙️ Verification Channels\n"
                "4. 🧾 Logs + Status\n"
                "5. ⚙️ Optional Rules"
            ),
            inline=False,
        )
        await solid._edit_or_followup(interaction, embed=embed, view=solid.ChooseExistingView())
    except Exception as exc:
        try:
            await interaction.response.send_message(
                f"❌ Could not reopen setup pickers: `{type(exc).__name__}: {str(exc)[:250]}`",
                ephemeral=True,
            )
        except Exception:
            pass


async def _back_to_setup(interaction: discord.Interaction) -> None:
    try:
        from stoney_verify.commands_ext import public_setup_solid as solid

        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        await solid._safe_defer_update(interaction)
        embed, view = await solid._build_main_setup_payload(guild)
        await solid._edit_or_followup(interaction, embed=embed, view=view)
    except Exception as exc:
        try:
            await interaction.response.send_message(
                f"❌ Could not go back to setup: `{type(exc).__name__}: {str(exc)[:250]}`",
                ephemeral=True,
            )
        except Exception:
            pass


class SetupSaveNextStepView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Refresh Health", emoji="🔄", style=discord.ButtonStyle.success, custom_id="stoney_setup_save:health", row=0)
    async def health(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_health(interaction)

    @discord.ui.button(label="More Pickers", emoji="🧩", style=discord.ButtonStyle.secondary, custom_id="stoney_setup_save:existing", row=0)
    async def existing(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_existing(interaction)

    @discord.ui.button(label="Back to Setup", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="stoney_setup_save:back", row=0)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _back_to_setup(interaction)


async def _wrapped_send_message(self: discord.InteractionResponse, *args: Any, **kwargs: Any) -> Any:
    try:
        embed = kwargs.get("embed")
        view = kwargs.get("view")
        if view is None and isinstance(embed, discord.Embed) and _is_setup_save_embed(embed):
            try:
                embed.add_field(
                    name="Next Step",
                    value="Press **🔄 Refresh Health** to see if this fixed setup, or keep mapping more items.",
                    inline=False,
                )
            except Exception:
                pass
            kwargs["view"] = SetupSaveNextStepView()
    except Exception:
        pass
    return await _ORIGINAL_SEND_MESSAGE(self, *args, **kwargs)


def apply() -> bool:
    global _PATCHED, _ORIGINAL_SEND_MESSAGE
    if _PATCHED:
        return True
    try:
        original = getattr(discord.InteractionResponse, "send_message", None)
        if not callable(original) or getattr(original, "_setup_save_next_step_wrapped", False):
            return False
        _ORIGINAL_SEND_MESSAGE = original
        setattr(_wrapped_send_message, "_setup_save_next_step_wrapped", True)
        setattr(discord.InteractionResponse, "send_message", _wrapped_send_message)
        _PATCHED = True
        print("🧭 setup_save_next_step_guard active; setup saves now include next-step buttons")
        return True
    except Exception as exc:
        print(f"⚠️ setup_save_next_step_guard failed: {exc!r}")
        return False


apply()

__all__ = ["apply", "SetupSaveNextStepView"]
