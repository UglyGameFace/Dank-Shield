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
        # Keep the historical fail-open behavior, but stop making acknowledgement
        # failures invisible while we validate the live setup flow.
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
    # leaving two live setup views that could show different state. Recover on
    # the exact clicked message instead.
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


def _install_response_integrity() -> None:
    solid = _implementation.setup.solid
    solid._safe_defer_update = _safe_setup_defer
    solid._edit_or_followup = _safe_setup_edit


def _assert_runtime_ownership() -> None:
    setup = _implementation.setup
    checks = (
        (setup.ProductSetupHomeView is _guided.GuidedSetupHomeView, "ProductSetupHomeView"),
        (setup.SetupReviewView is _guided.GuidedReviewView, "SetupReviewView"),
        (setup.LaunchTestView is _guided.GuidedTestView, "LaunchTestView"),
        (setup._open_test_launch is _guided.open_guided_tests, "_open_test_launch"),
        (
            _implementation.setup.solid._build_category_manager_payload
            is _implementation._category_payload,
            "ticket category presentation",
        ),
        (
            _implementation.setup.solid._edit_or_followup is _safe_setup_edit,
            "setup response routing",
        ),
    )
    missing = [label for okay, label in checks if not okay]
    if missing:
        raise RuntimeError("public setup runtime ownership drift: " + ", ".join(missing))


def apply_public_setup_runtime() -> None:
    """Install the complete setup presentation in one deterministic order.

    Re-running is intentional and safe. Compact binds the canonical setup
    presentation first, guided testing is then reasserted last, navigation and
    health contracts follow, and setup responses are pinned to one message.
    """

    _implementation._PATCHED = False
    _original_apply_compact_setup_patch()

    # Guided testing must always be the last presentation owner. Its historical
    # one-shot flag is not an ownership guarantee after a late compact rebind.
    _guided._PATCHED = False
    _guided.apply_guided_test_patch()

    install_custom_service_navigation_compat()
    install_voice_health_contract()
    _install_response_integrity()
    _assert_runtime_ownership()


# Keep the historical name because public_setup_gate and compatibility tests
# already call it, but make that path install the whole runtime instead of only
# the compact layer.
_implementation.apply_public_setup_runtime = apply_public_setup_runtime
_implementation.apply_compact_setup_patch = apply_public_setup_runtime


def _register_public_setup_runtime(bot, tree) -> None:
    _ = bot, tree
    apply_public_setup_runtime()
    print("✅ public_setup_runtime: deterministic compact + guided setup active")


_implementation.register_public_setup_compact_commands = _register_public_setup_runtime
_implementation._GUIDED_TEST_REGISTER_WRAPPED = True
_implementation._REASSERTING_APPLY_WRAPPED = True

# Direct imports, the late setup gate, and compatibility tests all converge on
# the same final owner.
apply_public_setup_runtime()

sys.modules[__name__] = _implementation
