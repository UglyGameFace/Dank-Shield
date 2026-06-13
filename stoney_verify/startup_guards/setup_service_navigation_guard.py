from __future__ import annotations

"""Add non-dead-end navigation to setup service pages.

Every /dank setup subpage should give the owner an obvious way to go home,
run setup health, or finish. The Services page previously showed only toggles,
which made mobile users feel trapped after saving choices.
"""

from typing import Any

import discord

_PATCHED = False
_ORIGINAL_SERVICE_INIT: Any = None
_ORIGINAL_SPAM_INIT: Any = None


def _log(message: str) -> None:
    try:
        print(f"🧭 setup_service_navigation_guard {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ setup_service_navigation_guard {message}")
    except Exception:
        pass


async def _open_setup_home(interaction: discord.Interaction) -> None:
    try:
        from stoney_verify.commands_ext import public_setup_solid as solid

        if not await solid._require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ This must be used inside a server.", ephemeral=True)
        embed, view = await solid._build_main_setup_payload(guild)
        if interaction.response.is_done():
            return await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        await interaction.response.edit_message(embed=embed, view=view)
    except Exception as exc:
        await _send_error(interaction, "Setup home failed", exc)


async def _run_setup_check(interaction: discord.Interaction) -> None:
    try:
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
        embed = await solid._build_health_embed(guild)
        await interaction.followup.send(embed=embed, view=solid.SetupNavView(), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    except Exception as exc:
        await _send_error(interaction, "Setup check failed", exc)


async def _done(interaction: discord.Interaction) -> None:
    try:
        embed = discord.Embed(
            title="✅ Setup Choices Saved",
            description="You are not stuck here. Go back to setup home or run a setup check to see the next exact fix.",
            color=discord.Color.green(),
        )
        if interaction.response.is_done():
            return await interaction.followup.send(embed=embed, view=SetupMiniNavView(), ephemeral=True)
        await interaction.response.edit_message(embed=embed, view=SetupMiniNavView())
    except Exception as exc:
        await _send_error(interaction, "Done failed", exc)


async def _send_error(interaction: discord.Interaction, label: str, exc: BaseException) -> None:
    msg = f"❌ {label}: `{type(exc).__name__}: {str(exc)[:240]}`"
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        else:
            await interaction.response.send_message(msg, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        pass


class SetupHomeButton(discord.ui.Button):
    def __init__(self, *, row: int = 4) -> None:
        super().__init__(label="Setup Home", emoji="🏠", style=discord.ButtonStyle.primary, custom_id="stoney_setup_nav:home", row=row)

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        await _open_setup_home(interaction)


class SetupCheckButton(discord.ui.Button):
    def __init__(self, *, row: int = 4) -> None:
        super().__init__(label="Run Setup Check", emoji="🩺", style=discord.ButtonStyle.secondary, custom_id="stoney_setup_nav:check", row=row)

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        await _run_setup_check(interaction)


class DoneButton(discord.ui.Button):
    def __init__(self, *, row: int = 4) -> None:
        super().__init__(label="Done", emoji="✅", style=discord.ButtonStyle.success, custom_id="stoney_setup_nav:done", row=row)

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        await _done(interaction)


class SetupMiniNavView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)
        self.add_item(SetupHomeButton(row=0))
        self.add_item(SetupCheckButton(row=0))


def _has_nav(view: Any) -> bool:
    try:
        return any(str(getattr(child, "custom_id", "") or "").startswith("stoney_setup_nav:") for child in list(getattr(view, "children", []) or []))
    except Exception:
        return False


def _append_nav(view: Any, *, include_done: bool = True) -> None:
    try:
        if _has_nav(view):
            return
        if len(list(getattr(view, "children", []) or [])) >= 22:
            return
        view.add_item(SetupHomeButton(row=4))
        view.add_item(SetupCheckButton(row=4))
        if include_done:
            view.add_item(DoneButton(row=4))
    except Exception as exc:
        _warn(f"could not append setup nav: {exc!r}")


def apply() -> bool:
    global _PATCHED, _ORIGINAL_SERVICE_INIT, _ORIGINAL_SPAM_INIT
    if _PATCHED:
        return True
    try:
        from stoney_verify.startup_guards import setup_service_modes as modes

        service_cls = getattr(modes, "ServiceModeView", None)
        spam_cls = getattr(modes, "SpamGuardSetupView", None)
        if service_cls is None or spam_cls is None:
            return False

        original_service_init = getattr(service_cls, "__init__", None)
        original_spam_init = getattr(spam_cls, "__init__", None)
        if not callable(original_service_init) or getattr(original_service_init, "_setup_nav_wrapped", False):
            return False

        def service_init(self: Any, *args: Any, **kwargs: Any) -> None:
            original_service_init(self, *args, **kwargs)
            _append_nav(self, include_done=True)

        def spam_init(self: Any, *args: Any, **kwargs: Any) -> None:
            original_spam_init(self, *args, **kwargs)
            _append_nav(self, include_done=True)

        setattr(service_init, "_setup_nav_wrapped", True)
        setattr(spam_init, "_setup_nav_wrapped", True)
        _ORIGINAL_SERVICE_INIT = original_service_init
        _ORIGINAL_SPAM_INIT = original_spam_init
        service_cls.__init__ = service_init
        spam_cls.__init__ = spam_init
        _PATCHED = True
        _log("active; Services and SpamGuard setup pages now include home/check/done navigation")
        return True
    except Exception as exc:
        _warn(f"failed: {exc!r}")
        return False


apply()

__all__ = ["apply", "SetupMiniNavView"]
