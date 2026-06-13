from __future__ import annotations

"""Attach direct fix buttons to setup health results."""

from typing import Any, Optional

import discord

_PATCHED = False
_ORIGINAL_EDIT_OR_FOLLOWUP: Any = None


def _is_health_embed(embed: Any) -> bool:
    try:
        title = str(getattr(embed, "title", "") or "").lower()
        desc = str(getattr(embed, "description", "") or "").lower()
        return "setup health" in title or "health check" in title or "recommended next click" in desc
    except Exception:
        return False


async def _open_existing(interaction: discord.Interaction) -> None:
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
            "Pick the exact roles/channels/categories your server already uses.\n"
            "Names do not matter — Dank Shield saves Discord IDs."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Best order",
        value=(
            "1. **Ticket Basics** — open/closed categories, staff role, transcripts\n"
            "2. **Access Roles** — waiting/approved/member roles\n"
            "3. **Verification Channels** — only if your server uses them\n"
            "4. **Logs + Status** — modlog/status channels\n"
            "5. **Optional Rules** — prefix/timers/style"
        ),
        inline=False,
    )
    await solid._edit_or_followup(interaction, embed=embed, view=solid.ChooseExistingView())


async def _open_main_setup(interaction: discord.Interaction) -> None:
    from stoney_verify.commands_ext import public_setup_solid as solid

    if not await solid._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
    await solid._safe_defer_update(interaction)
    embed, view = await solid._build_main_setup_payload(guild)
    await solid._edit_or_followup(interaction, embed=embed, view=view)


async def _rerun_health(interaction: discord.Interaction) -> None:
    from stoney_verify.commands_ext import public_setup_solid as solid

    if not await solid._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
    await solid._safe_defer_update(interaction)
    embed = await solid._build_health_embed(guild)
    await solid._edit_or_followup(interaction, embed=embed, view=HealthActionView())


async def _start_setup(interaction: discord.Interaction) -> None:
    from stoney_verify.commands_ext import public_setup_solid as solid

    if not await solid._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
    except Exception:
        pass
    try:
        from stoney_verify.commands_ext import public_setup_defaults

        await public_setup_defaults._setup_defaults_callback(interaction)
        try:
            created, skipped, error = await solid._seed_recommended_categories(guild)
            if error:
                await interaction.followup.send(
                    f"⚠️ Setup ran, but ticket menu options could not be checked: `{error}`",
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            elif created:
                await interaction.followup.send(
                    f"✅ Ticket menu options created: {', '.join(f'`{x}`' for x in created)}",
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            elif skipped:
                await interaction.followup.send(
                    "✅ Ticket menu options already exist. Nothing was overwritten.",
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
        except Exception:
            pass
        embed = await solid._build_health_embed(guild)
        await interaction.followup.send(
            embed=embed,
            view=HealthActionView(),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except Exception as exc:
        embed = discord.Embed(
            title="❌ Setup Could Not Finish",
            description=(
                f"`{type(exc).__name__}: {str(exc)[:300]}`\n\n"
                "Next: press **Use Existing Roles/Channels** if you want to map items manually, or fix permissions and try Start Setup again."
            ),
            color=discord.Color.red(),
        )
        await interaction.followup.send(embed=embed, view=HealthActionView(), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


class HealthActionView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(label="Start Setup / Fix Missing", emoji="🚀", style=discord.ButtonStyle.success, custom_id="stoney_setup_health:auto", row=0)
    async def start_setup(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _start_setup(interaction)

    @discord.ui.button(label="Use Existing Roles/Channels", emoji="🧩", style=discord.ButtonStyle.primary, custom_id="stoney_setup_health:existing", row=0)
    async def existing(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_existing(interaction)

    @discord.ui.button(label="Refresh Health", emoji="🔄", style=discord.ButtonStyle.secondary, custom_id="stoney_setup_health:refresh", row=1)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _rerun_health(interaction)

    @discord.ui.button(label="Back to Setup", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="stoney_setup_health:back", row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _open_main_setup(interaction)


async def _wrapped_edit_or_followup(interaction: discord.Interaction, *, embed: discord.Embed, view: Optional[discord.ui.View] = None) -> None:
    from stoney_verify.commands_ext import public_setup_solid as solid

    next_view = HealthActionView() if _is_health_embed(embed) else view
    await _ORIGINAL_EDIT_OR_FOLLOWUP(interaction, embed=embed, view=next_view)


def apply() -> bool:
    global _PATCHED, _ORIGINAL_EDIT_OR_FOLLOWUP
    if _PATCHED:
        return True
    try:
        from stoney_verify.commands_ext import public_setup_solid as solid

        original = getattr(solid, "_edit_or_followup", None)
        if not callable(original) or getattr(original, "_health_action_buttons_wrapped", False):
            return False
        _ORIGINAL_EDIT_OR_FOLLOWUP = original
        setattr(_wrapped_edit_or_followup, "_health_action_buttons_wrapped", True)
        solid._edit_or_followup = _wrapped_edit_or_followup
        _PATCHED = True
        print("🧭 setup_health_action_buttons_guard active; health results now include direct fix buttons")
        return True
    except Exception as exc:
        print(f"⚠️ setup_health_action_buttons_guard failed: {exc!r}")
        return False


apply()

__all__ = ["apply", "HealthActionView"]
