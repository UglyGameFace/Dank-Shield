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
        async def post_or_replace_verify_ui_guarded(
            channel: discord.TextChannel,
            *args: Any,
            **kwargs: Any,
        ) -> str:
            guild = getattr(channel, "guild", None)
            if _not_allowed(guild):
                try:
                    print(
                        "id_verify_allowlist_guard blocked legacy verify panel "
                        f"guild={getattr(guild, 'id', 0)}"
                    )
                except Exception:
                    pass
                return "disabled_basic_button_mode"
            return await original_post(channel, *args, **kwargs)

        setattr(post_or_replace_verify_ui_guarded, "_id_allowlist_wrapped", True)
        verify_ui.post_or_replace_verify_ui = post_or_replace_verify_ui_guarded  # type: ignore[assignment]

    original_handle = getattr(verify_ui, "maybe_handle_verify_ui_interaction", None)
    if callable(original_handle) and not getattr(original_handle, "_id_allowlist_wrapped", False):
        async def maybe_handle_verify_ui_interaction_guarded(
            interaction: discord.Interaction,
            *,
            site_url: str,
        ) -> bool:
            data = getattr(interaction, "data", None) or {}
            custom_id = str(data.get("custom_id") or "")
            if custom_id.startswith("sv:verify:") and _not_allowed(interaction.guild):
                message = (
                    "✅ This server uses Basic Button Verification. Please use the green "
                    "**Verify** button in the verification channel."
                )
                try:
                    if not interaction.response.is_done():
                        await interaction.response.send_message(
                            message,
                            ephemeral=True,
                            allowed_mentions=discord.AllowedMentions.none(),
                        )
                    else:
                        await interaction.followup.send(
                            message,
                            ephemeral=True,
                            allowed_mentions=discord.AllowedMentions.none(),
                        )
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


def _patch_verification_ticket_flow() -> bool:
    """Route ticket panel posting through the canonical allowlisted runtime.

    This replaces the old unconditional permission rewrite in the existing
    verification-ticket path. Tickets that already inherit valid owner/bot
    access are posted immediately; repair is attempted only when effective
    access is actually missing.
    """
    try:
        from stoney_verify import verify_ui
        from stoney_verify.startup_guards import unverified_ticket_panel_flow as flow
        from stoney_verify.verification_new.id_ticket_runtime import (
            post_allowlisted_id_ticket_panel,
        )
    except Exception:
        return False

    current = getattr(flow, "_post_verify_ui", None)
    if not callable(current):
        return False
    if getattr(current, "_canonical_id_ticket_runtime", False):
        return True

    async def post_verify_ui_canonical(
        channel: discord.TextChannel,
        member: discord.Member,
    ) -> bool:
        return await post_allowlisted_id_ticket_panel(
            channel,
            member,
            config_loader=flow._get_guild_config_safe,
            access_repair=flow._ensure_ticket_channel_access,
            panel_poster=verify_ui.post_or_replace_verify_ui,
            site_url=flow._site_url(),
            ttl_minutes=flow._token_ttl_minutes(),
            allow_regen=flow._allow_user_regen(),
        )

    setattr(post_verify_ui_canonical, "_canonical_id_ticket_runtime", True)
    flow._post_verify_ui = post_verify_ui_canonical  # type: ignore[assignment]
    return True


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True

    ui_ready = _patch_verify_ui()
    ticket_flow_ready = _patch_verification_ticket_flow()
    _PATCHED = bool(ui_ready and ticket_flow_ready)
    if _PATCHED:
        try:
            print(
                "id_verify_allowlist_guard active "
                "ui_allowlist=True canonical_ticket_runtime=True"
            )
        except Exception:
            pass
    return _PATCHED


apply()

__all__ = ["apply"]
