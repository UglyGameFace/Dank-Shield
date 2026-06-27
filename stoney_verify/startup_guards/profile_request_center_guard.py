from __future__ import annotations

from typing import Any

import discord

_PATCHED = False


def _build_clean_profile_panel_view(public_self_roles_group: Any) -> type[discord.ui.View]:
    profile_prefix = getattr(public_self_roles_group, "PROFILE_PREFIX", "dank:profile:v1:")

    class CleanProfilePanelView(discord.ui.View):
        def __init__(self) -> None:
            super().__init__(timeout=None)

            # Main public panel stays clean: one editor entrypoint, one viewer,
            # and staff-reviewed request actions. Direct section edit buttons
            # live inside Edit My Profile now.
            self.add_item(discord.ui.Button(label="Edit My Profile", emoji="✏️", style=discord.ButtonStyle.primary, custom_id=f"{profile_prefix}edit", row=0))
            self.add_item(discord.ui.Button(label="View My Profile", emoji="👤", style=discord.ButtonStyle.secondary, custom_id=f"{profile_prefix}view", row=0))
            self.add_item(discord.ui.Button(label="View Member Profile", emoji="👥", style=discord.ButtonStyle.secondary, custom_id=f"{profile_prefix}pick_member", row=1))
            self.add_item(discord.ui.Button(label="Learn Terms", emoji="📘", style=discord.ButtonStyle.secondary, custom_id=f"{profile_prefix}learn", row=1))
            self.add_item(discord.ui.Button(label="Server Cosmetics", emoji="🎭", style=discord.ButtonStyle.secondary, custom_id=f"{profile_prefix}cosmetics", row=2))
            self.add_item(discord.ui.Button(label="Suggest Missing Interest", emoji="➕", style=discord.ButtonStyle.secondary, custom_id=f"{profile_prefix}missing_interest", row=3))
            self.add_item(discord.ui.Button(label="Missing Identity?", emoji="✍️", style=discord.ButtonStyle.secondary, custom_id=f"{profile_prefix}missing", row=3))
            self.add_item(discord.ui.Button(label="Clear Profile Roles", emoji="🧹", style=discord.ButtonStyle.danger, custom_id=f"{profile_prefix}clear", row=4))

    CleanProfilePanelView.__name__ = "ProfilePanelView"
    return CleanProfilePanelView


async def _identity_submit(self: Any, interaction: discord.Interaction) -> None:
    from stoney_verify.services.profile_staff_requests import dispatch_profile_staff_request
    from stoney_verify.commands_ext import public_self_roles_group as profile

    guild = interaction.guild
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    if guild is None or member is None:
        return await profile._reply(interaction, "This only works inside the server.", ok=False)

    clean = str(getattr(getattr(self, "label", None), "value", "") or "").replace("@everyone", "everyone").replace("@here", "here").strip()
    clean = " ".join(clean.split())[:80]
    if not clean:
        return await profile._reply(interaction, "Missing identity label was empty.", ok=False)

    delivery = await dispatch_profile_staff_request(
        guild=guild,
        member=member,
        request_type="identity",
        requested_value=clean,
        source_channel=interaction.channel,
    )
    if delivery.ok:
        return await profile._reply(interaction, f"Missing Identity request sent to staff queue {delivery.channel_mention}: `{clean}`", ok=True)
    return await profile._reply(interaction, f"Could not send Missing Identity request: {delivery.reason}", ok=False)


async def _interest_submit(self: Any, interaction: discord.Interaction) -> None:
    from stoney_verify.services.profile_staff_requests import dispatch_profile_staff_request
    from stoney_verify.commands_ext import public_self_roles_group as profile

    guild = interaction.guild
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    if guild is None or member is None:
        return await profile._reply(interaction, "This only works inside the server.", ok=False)

    clean, error = profile._clean_missing_interest(str(getattr(getattr(self, "interest", None), "value", "") or ""))
    if error:
        return await profile._reply(interaction, error, ok=False)

    delivery = await dispatch_profile_staff_request(
        guild=guild,
        member=member,
        request_type="interest",
        requested_value=clean,
        source_channel=interaction.channel,
    )
    if delivery.ok:
        return await profile._reply(interaction, f"Missing interest request sent to staff queue {delivery.channel_mention}: `{clean}`", ok=True)
    return await profile._reply(interaction, f"Could not send missing interest request: {delivery.reason}", ok=False)


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        from stoney_verify.commands_ext import public_self_roles_group as profile

        profile.ProfilePanelView = _build_clean_profile_panel_view(profile)
        profile.MissingIdentityModal.on_submit = _identity_submit
        profile.MissingInterestModal.on_submit = _interest_submit
        try:
            profile._PROFILE_PANEL_VIEW_REGISTERED = False
        except Exception:
            pass

        _PATCHED = True
        print("✅ profile_request_center_guard active; public Profile Panel is clean and profile staff requests use centralized delivery")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ profile_request_center_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
