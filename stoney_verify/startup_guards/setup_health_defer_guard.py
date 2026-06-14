from __future__ import annotations

"""Make Setup Health acknowledge Discord interactions before running audit."""

import discord

_DONE = False


def _load_polish_guards() -> None:
    for module_name in (
        "verification_member_role_fallback_guard",
        "setup_picker_permission_error_guard",
    ):
        try:
            module = __import__(f"stoney_verify.startup_guards.{module_name}", fromlist=["apply"])
            apply_fn = getattr(module, "apply", None)
            if callable(apply_fn):
                apply_fn()
        except Exception:
            pass


def _log(msg: str) -> None:
    try:
        print(f"🩺 setup_health_defer_guard {msg}")
    except Exception:
        pass


async def _ack(interaction: discord.Interaction) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(thinking=True, ephemeral=True)
    except Exception:
        try:
            if not interaction.response.is_done():
                await interaction.response.defer(thinking=False)
        except Exception:
            pass


async def _send(interaction: discord.Interaction, *, embed: discord.Embed, view: discord.ui.View | None) -> None:
    try:
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=view)
        else:
            await interaction.response.edit_message(embed=embed, view=view)
        return
    except Exception:
        pass
    try:
        await interaction.followup.send(embed=embed, view=view, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        pass


def apply() -> bool:
    global _DONE
    if _DONE:
        return True
    _load_polish_guards()
    try:
        from stoney_verify.commands_ext import public_setup_fresh_choice as fresh
        from stoney_verify.commands_ext import public_setup_solid as solid

        async def open_plain_health(interaction: discord.Interaction) -> None:
            if not await solid._require_setup_permission(interaction):
                return
            guild = interaction.guild
            if guild is None:
                return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
            await _ack(interaction)
            try:
                embed = await fresh.recommend._build_plain_setup_health_embed(guild)  # type: ignore[attr-defined]
                view = getattr(fresh.recommend, "SetupHealthHelpView", solid.BackToSetupView)()
            except Exception as exc:
                try:
                    embed = await solid._build_health_embed(guild)
                    view = solid.BackToSetupView()
                except Exception:
                    embed = discord.Embed(title="❌ Setup Health Failed", description=f"`{type(exc).__name__}: {str(exc)[:260]}`", color=discord.Color.red())
                    view = solid.BackToSetupView()
            await _send(interaction, embed=embed, view=view)

        fresh._open_plain_health = open_plain_health
        _DONE = True
        _log("active; health buttons defer before full safety audit")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ setup_health_defer_guard failed: {exc!r}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
