from __future__ import annotations

"""Deterministic runtime installer for the public ``/dank setup`` presentation.

The canonical setup behavior lives in ``public_setup_recommend`` and
``public_setup_solid``. This compatibility module owns one thing only: applying
presentation layers in one stable order and keeping setup interaction responses
on the panel that the owner actually clicked.
"""

import sys
from typing import Any, Optional

import discord

# Ticket categories own the canonical category data/service layer. Install that
# before compact presentation imports so compact captures the managed payload as
# its underlying implementation instead of whichever historical owner happened
# to import first.
from stoney_verify.startup_guards import ticket_category_setup_guard as _ticket_category_guard

_ticket_category_guard.apply()

from stoney_verify.setup_ui import public_setup_compact as _implementation
from stoney_verify.setup_ui import public_setup_guided_test as _guided
from stoney_verify.setup_020_navigation_compat import (
    install_custom_service_navigation_compat,
)
from stoney_verify.setup_voice_health_contract import install_voice_health_contract


async def _canonical_plan_route(self, interaction):
    _ = self
    await _implementation.setup._open_choose_setup_type(interaction)


# Never freeze a stale plan callback into compact views. Entitlement/recovery
# layers may replace the canonical route during startup.
_implementation.CompactSetupHomeView._plan = _canonical_plan_route
_implementation.CompactManagerView._plan = _canonical_plan_route


_ORIGINAL_APPLY_ATTR = "_DANK_SETUP_RUNTIME_ORIGINAL_COMPACT_APPLY"
_original_apply_compact_setup_patch = getattr(
    _implementation,
    _ORIGINAL_APPLY_ATTR,
    None,
)
if not callable(_original_apply_compact_setup_patch):
    _original_apply_compact_setup_patch = _implementation.apply_compact_setup_patch
    setattr(
        _implementation,
        _ORIGINAL_APPLY_ATTR,
        _original_apply_compact_setup_patch,
    )


def _setup_runtime_log(stage: str, interaction: Any, **details: Any) -> None:
    try:
        data = interaction.data if isinstance(interaction.data, dict) else {}
        fields: dict[str, Any] = {
            "stage": stage,
            "interaction": int(getattr(interaction, "id", 0) or 0),
            "guild": int(getattr(getattr(interaction, "guild", None), "id", 0) or 0),
            "message": int(getattr(getattr(interaction, "message", None), "id", 0) or 0),
            "user": int(getattr(getattr(interaction, "user", None), "id", 0) or 0),
            "custom_id": str(data.get("custom_id") or ""),
            "response_done": bool(interaction.response.is_done()),
        }
        fields.update(details)
        print("🔎 setup_runtime " + " ".join(f"{key}={value}" for key, value in fields.items()))
    except Exception:
        pass


async def _safe_setup_defer(interaction: discord.Interaction) -> None:
    try:
        if interaction.response.is_done():
            return
        await interaction.response.defer(thinking=False)
        _setup_runtime_log("acknowledged", interaction)
    except Exception as exc:
        # Preserve fail-open behavior but make acknowledgement failures visible.
        _setup_runtime_log(
            "ack_failed",
            interaction,
            error=f"{type(exc).__name__}:{str(exc)[:180]}",
        )


async def _safe_setup_edit(
    interaction: discord.Interaction,
    *,
    embed: discord.Embed,
    view: Optional[discord.ui.View] = None,
) -> None:
    primary_error: Optional[Exception] = None
    try:
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=view)
            route = "edit_original_response"
        else:
            await interaction.response.edit_message(embed=embed, view=view)
            route = "response.edit_message"
        _setup_runtime_log("edit_ok", interaction, route=route)
        return
    except Exception as exc:
        primary_error = exc
        _setup_runtime_log(
            "edit_failed",
            interaction,
            error=f"{type(exc).__name__}:{str(exc)[:180]}",
        )

    # A component action belongs to the message that was clicked. The old helper
    # silently sent a second interactive ephemeral panel when the edit failed,
    # leaving two live setup views that could show different state.
    message = getattr(interaction, "message", None)
    edit_message = getattr(message, "edit", None)
    if message is not None and callable(edit_message):
        try:
            await edit_message(embed=embed, view=view)
            _setup_runtime_log("direct_message_recovery_ok", interaction)
            return
        except Exception as exc:
            _setup_runtime_log(
                "direct_message_recovery_failed",
                interaction,
                error=f"{type(exc).__name__}:{str(exc)[:180]}",
            )

        notice = (
            "⚠️ This setup screen could not update safely. Reopen `/dank setup` "
            "to continue. No second setup panel was opened."
        )
        try:
            if interaction.response.is_done():
                await interaction.followup.send(notice, ephemeral=True)
            else:
                await interaction.response.send_message(notice, ephemeral=True)
            _setup_runtime_log("panel_fork_blocked", interaction)
            return
        except Exception as exc:
            _setup_runtime_log(
                "panel_fork_notice_failed",
                interaction,
                error=f"{type(exc).__name__}:{str(exc)[:180]}",
            )
            if primary_error is not None:
                raise primary_error
            raise

    # Slash-command entrypoints do not have a clicked component message. A new
    # private response/follow-up is correct for those callers.
    try:
        if interaction.response.is_done():
            await interaction.followup.send(
                embed=embed,
                view=view,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            route = "followup.send"
        else:
            await interaction.response.send_message(
                embed=embed,
                view=view,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            route = "response.send_message"
        _setup_runtime_log("non_component_recovery_ok", interaction, route=route)
    except Exception as exc:
        _setup_runtime_log(
            "non_component_recovery_failed",
            interaction,
            error=f"{type(exc).__name__}:{str(exc)[:180]}",
        )
        if primary_error is not None:
            raise primary_error
        raise


async def _open_welcome_setup_in_place(interaction: discord.Interaction) -> None:
    from stoney_verify import welcome_setup_ui as welcome

    original = getattr(welcome, "_dank_setup_original_open_welcome_setup", None)
    # Standalone/slash callers have no setup message to replace, so preserve the
    # canonical standalone behavior there.
    if getattr(interaction, "message", None) is None and callable(original):
        await original(interaction)
        return
    if not await welcome._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return
    await _safe_setup_defer(interaction)
    config = await welcome.get_guild_config(guild.id, refresh=True)
    await _safe_setup_edit(
        interaction,
        embed=await welcome._welcome_embed(guild, config),
        view=welcome.WelcomeSetupView(owner_id=interaction.user.id, config=config),
    )


async def _open_profile_setup_in_place(interaction: discord.Interaction) -> None:
    from stoney_verify import profile_card_setup_ui as profile

    original = getattr(profile, "_dank_setup_original_open_profile_card_setup", None)
    if getattr(interaction, "message", None) is None and callable(original):
        await original(interaction)
        return
    if not await profile._require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return
    await _safe_setup_defer(interaction)
    config = await profile.get_guild_config(guild.id, refresh=True)
    await _safe_setup_edit(
        interaction,
        embed=profile._setup_embed(guild, config),
        view=profile.ProfileCardSetupView(owner_id=interaction.user.id, config=config),
    )


async def _profile_edit_or_send(
    interaction: discord.Interaction,
    *,
    embed: discord.Embed,
    view: discord.ui.View,
) -> None:
    await _safe_setup_edit(interaction, embed=embed, view=view)


async def _ack_then_open_manager(interaction: discord.Interaction) -> None:
    setup = _implementation.setup
    if not await setup.solid._require_setup_permission(interaction):
        return
    if interaction.guild is None:
        return await interaction.response.send_message(
            "❌ This must be used inside a server.",
            ephemeral=True,
        )
    await _safe_setup_defer(interaction)
    original = getattr(_implementation, "_DANK_SETUP_ORIGINAL_OPEN_MANAGER")
    await original(interaction)


async def _ack_then_review_next(self: Any, interaction: discord.Interaction) -> None:
    setup = _implementation.setup
    if not await setup.solid._require_setup_permission(interaction):
        return
    if interaction.guild is None:
        return await interaction.response.send_message(
            "❌ This must be used inside a server.",
            ephemeral=True,
        )
    await _safe_setup_defer(interaction)
    original = getattr(
        _implementation.CompactReviewView,
        "_DANK_SETUP_ORIGINAL_REVIEW_NEXT",
    )
    await original(self, interaction)


async def _ack_then_open_guided_target(
    interaction: discord.Interaction,
    target: str,
    requirement_key: str = "",
) -> None:
    setup = _implementation.setup
    if not await setup.solid._require_setup_permission(interaction):
        return
    if interaction.guild is None:
        return await interaction.response.send_message(
            "❌ This must be used inside a server.",
            ephemeral=True,
        )
    await _safe_setup_defer(interaction)
    original = getattr(setup, "_DANK_SETUP_ORIGINAL_OPEN_GUIDED_TARGET")
    await original(interaction, target, requirement_key)


async def _ack_then_open_timers_behavior(interaction: discord.Interaction) -> None:
    setup = _implementation.setup
    if not await setup.solid._require_setup_permission(interaction):
        return
    if interaction.guild is None:
        return await interaction.response.send_message(
            "❌ This must be used inside a server.",
            ephemeral=True,
        )
    await _safe_setup_defer(interaction)
    original = getattr(setup, "_DANK_SETUP_ORIGINAL_OPEN_TIMERS_BEHAVIOR")
    await original(interaction)


async def _ack_then_open_protection_options(interaction: discord.Interaction) -> None:
    setup = _implementation.setup
    if not await setup.solid._require_setup_permission(interaction):
        return
    if interaction.guild is None:
        return await interaction.response.send_message(
            "❌ This must be used inside a server.",
            ephemeral=True,
        )
    await _safe_setup_defer(interaction)
    original = getattr(setup, "_DANK_SETUP_ORIGINAL_OPEN_PROTECTION_OPTIONS")
    await original(interaction)


def _install_ack_integrity() -> None:
    setup = _implementation.setup

    if not hasattr(_implementation, "_DANK_SETUP_ORIGINAL_OPEN_MANAGER"):
        _implementation._DANK_SETUP_ORIGINAL_OPEN_MANAGER = _implementation._open_manager
    _implementation._open_manager = _ack_then_open_manager

    if not hasattr(
        _implementation.CompactReviewView,
        "_DANK_SETUP_ORIGINAL_REVIEW_NEXT",
    ):
        _implementation.CompactReviewView._DANK_SETUP_ORIGINAL_REVIEW_NEXT = (
            _implementation.CompactReviewView._next
        )
    _implementation.CompactReviewView._next = _ack_then_review_next

    if not hasattr(setup, "_DANK_SETUP_ORIGINAL_OPEN_GUIDED_TARGET"):
        setup._DANK_SETUP_ORIGINAL_OPEN_GUIDED_TARGET = setup._open_guided_target
    setup._open_guided_target = _ack_then_open_guided_target

    if not hasattr(setup, "_DANK_SETUP_ORIGINAL_OPEN_TIMERS_BEHAVIOR"):
        setup._DANK_SETUP_ORIGINAL_OPEN_TIMERS_BEHAVIOR = setup._open_timers_behavior
    setup._open_timers_behavior = _ack_then_open_timers_behavior

    if not hasattr(setup, "_DANK_SETUP_ORIGINAL_OPEN_PROTECTION_OPTIONS"):
        setup._DANK_SETUP_ORIGINAL_OPEN_PROTECTION_OPTIONS = setup._open_protection_options
    setup._open_protection_options = _ack_then_open_protection_options


def _install_feature_area_integrity() -> None:
    # Welcome setup historically deferred a setup-menu component and then sent a
    # second ephemeral interactive panel. Keep component navigation in-place,
    # while preserving its standalone behavior for non-component entrypoints.
    from stoney_verify import welcome_setup_ui as welcome

    if not hasattr(welcome, "_dank_setup_original_open_welcome_setup"):
        welcome._dank_setup_original_open_welcome_setup = welcome.open_welcome_setup
    welcome.open_welcome_setup = _open_welcome_setup_in_place

    # Profile setup already tries to edit in-place, but its helper could fall
    # back to another interactive follow-up. Route both the entrypoint and its
    # refresh/toggle helper through the same setup response owner.
    from stoney_verify import profile_card_setup_ui as profile
    from stoney_verify import profile_card_setup_ui_core as profile_core

    if not hasattr(profile, "_dank_setup_original_open_profile_card_setup"):
        profile._dank_setup_original_open_profile_card_setup = profile.open_profile_card_setup
    profile.open_profile_card_setup = _open_profile_setup_in_place
    profile._edit_or_send = _profile_edit_or_send
    profile_core._edit_or_send = _profile_edit_or_send


def _install_response_integrity_impl() -> None:
    solid = _implementation.setup.solid
    solid._safe_defer_update = _safe_setup_defer
    solid._edit_or_followup = _safe_setup_edit


_install_response_integrity = _install_response_integrity_impl


def _assert_runtime_ownership_impl() -> None:
    setup = _implementation.setup
    solid = setup.solid
    checks = (
        (setup.ProductSetupHomeView is _guided.GuidedSetupHomeView, "ProductSetupHomeView"),
        (setup.SetupReviewView is _guided.GuidedReviewView, "SetupReviewView"),
        (setup.LaunchTestView is _guided.GuidedTestView, "LaunchTestView"),
        (setup._open_test_launch is _guided.open_guided_tests, "_open_test_launch"),
        (
            _implementation._ORIGINAL_CATEGORY_PAYLOAD
            is _ticket_category_guard._build_category_manager_payload,
            "ticket category service payload",
        ),
        (
            solid._category_load is _ticket_category_guard._setup_category_load,
            "ticket category loader",
        ),
        (
            solid._seed_recommended_categories
            is _ticket_category_guard._seed_catalog_without_enabling_everything,
            "ticket category seed service",
        ),
        (
            solid._build_category_manager_payload is _implementation._category_payload,
            "ticket category presentation",
        ),
        (solid._edit_or_followup is _safe_setup_edit, "setup response routing"),
        (_implementation._open_manager is _ack_then_open_manager, "manager acknowledgement"),
        (
            _implementation.CompactReviewView._next is _ack_then_review_next,
            "review acknowledgement",
        ),
        (setup._open_guided_target is _ack_then_open_guided_target, "guided acknowledgement"),
        (setup._open_timers_behavior is _ack_then_open_timers_behavior, "timers acknowledgement"),
        (
            setup._open_protection_options is _ack_then_open_protection_options,
            "protection acknowledgement",
        ),
    )
    missing = [label for okay, label in checks if not okay]
    if missing:
        raise RuntimeError("public setup runtime ownership drift: " + ", ".join(missing))


_assert_runtime_ownership = _assert_runtime_ownership_impl


def apply_public_setup_runtime() -> None:
    """Install the complete setup presentation in one deterministic order.

    Re-running is intentional and safe. The canonical ticket-category service
    installs first, active component routes gain early acknowledgement, compact
    binds presentation over those services, guided testing is reasserted last,
    navigation and health contracts follow, and setup responses are pinned to
    one message.
    """

    _ticket_category_guard.apply()
    # Be deterministic even when a compatibility test imported the presentation
    # module before this installer. Compact must wrap the canonical managed
    # category payload, never a pre-guard historical payload.
    _implementation._ORIGINAL_CATEGORY_PAYLOAD = (
        _ticket_category_guard._build_category_manager_payload
    )

    _install_ack_integrity()

    _implementation._PATCHED = False
    _original_apply_compact_setup_patch()

    # Guided testing must always be the last presentation owner. Its historical
    # one-shot flag is not an ownership guarantee after a late compact rebind.
    _guided._PATCHED = False
    _guided.apply_guided_test_patch()

    install_custom_service_navigation_compat()
    install_voice_health_contract()
    _install_response_integrity()
    _install_feature_area_integrity()
    _assert_runtime_ownership()


# Keep the historical name because public_setup_gate and compatibility tests
# already call it, but make that path install the whole runtime instead of only
# the compact layer.
_implementation.apply_public_setup_runtime = apply_public_setup_runtime
_implementation.apply_compact_setup_patch = apply_public_setup_runtime


def _register_public_setup_runtime(bot, tree) -> None:
    _ = bot, tree
    apply_public_setup_runtime()
    print("✅ public_setup_runtime: deterministic category + compact + guided setup active")


_implementation.register_public_setup_compact_commands = _register_public_setup_runtime
_implementation._GUIDED_TEST_REGISTER_WRAPPED = True
_implementation._REASSERTING_APPLY_WRAPPED = True

# Direct imports, the late setup gate, and compatibility tests all converge on
# the same final owner.
apply_public_setup_runtime()

sys.modules[__name__] = _implementation