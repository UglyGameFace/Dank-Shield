from __future__ import annotations

from typing import Any

import discord

_PATCHED = False


def _not_allowed(guild: discord.Guild | None, cfg: Any = None) -> bool:
    try:
        from stoney_verify.setup_engine.verification_modes import id_verify_allowed_for_guild
        return not bool(guild and id_verify_allowed_for_guild(guild, cfg))
    except Exception:
        return True


def _patch_verify_ui() -> bool:
    try:
        from stoney_verify import verify_ui
    except Exception:
        return False

    original_post = getattr(verify_ui, "post_or_replace_verify_ui", None)
    if callable(original_post) and not getattr(original_post, "_id_allowlist_wrapped", False):
        async def post_or_replace_verify_ui_guarded(channel: discord.TextChannel, *args: Any, **kwargs: Any) -> str:
            guild = getattr(channel, "guild", None)
            if _not_allowed(guild):
                try:
                    print(f"id_verify_allowlist_guard blocked legacy verify panel guild={getattr(guild, 'id', 0)}")
                except Exception:
                    pass
                return "disabled_basic_button_mode"
            return await original_post(channel, *args, **kwargs)

        setattr(post_or_replace_verify_ui_guarded, "_id_allowlist_wrapped", True)
        verify_ui.post_or_replace_verify_ui = post_or_replace_verify_ui_guarded  # type: ignore[assignment]

    original_handle = getattr(verify_ui, "maybe_handle_verify_ui_interaction", None)
    if callable(original_handle) and not getattr(original_handle, "_id_allowlist_wrapped", False):
        async def maybe_handle_verify_ui_interaction_guarded(interaction: discord.Interaction, *, site_url: str) -> bool:
            data = getattr(interaction, "data", None) or {}
            custom_id = str(data.get("custom_id") or "")
            if custom_id.startswith("sv:verify:") and _not_allowed(interaction.guild):
                message = "✅ This server uses Basic Button Verification. Please use the green **Verify** button in the verification channel."
                try:
                    if not interaction.response.is_done():
                        await interaction.response.send_message(message, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
                    else:
                        await interaction.followup.send(message, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
                except Exception:
                    pass
                return True
            return await original_handle(interaction, site_url=site_url)

        setattr(maybe_handle_verify_ui_interaction_guarded, "_id_allowlist_wrapped", True)
        verify_ui.maybe_handle_verify_ui_interaction = maybe_handle_verify_ui_interaction_guarded  # type: ignore[assignment]

    try:
        from stoney_verify.verification_new import service as verification_service
        verification_service.post_or_replace_verify_ui = verify_ui.post_or_replace_verify_ui  # type: ignore[attr-defined]
    except Exception:
        pass
    return True


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    _PATCHED = _patch_verify_ui()
    if _PATCHED:
        try:
            print("id_verify_allowlist_guard active")
        except Exception:
            pass
    return _PATCHED


apply()

__all__ = ["apply"]
