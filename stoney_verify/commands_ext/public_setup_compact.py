from __future__ import annotations

"""Deterministic runtime installer for the public ``/dank setup`` presentation.

The canonical setup behavior lives in ``public_setup_recommend`` and
``public_setup_solid``.  This compatibility module owns one thing only: applying
presentation layers in one stable order.  Older code applied compact setup late
and could silently erase the guided-test bindings, leaving Discord with a mixed
view/callback graph.
"""

import sys

from stoney_verify.setup_ui import public_setup_compact as _implementation
from stoney_verify.setup_ui import public_setup_guided_test as _guided
from stoney_verify.setup_020_navigation_compat import (
    install_custom_service_navigation_compat,
)
from stoney_verify.setup_voice_health_contract import install_voice_health_contract


async def _canonical_plan_route(self, interaction):
    _ = self
    await _implementation.setup._open_choose_setup_type(interaction)


# Never freeze a stale plan callback into compact views.  Entitlement/recovery
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


def _assert_runtime_ownership() -> None:
    setup = _implementation.setup
    checks = (
        (
            setup.ProductSetupHomeView is _guided.GuidedSetupHomeView,
            "ProductSetupHomeView",
        ),
        (
            setup.SetupReviewView is _guided.GuidedReviewView,
            "SetupReviewView",
        ),
        (
            setup.LaunchTestView is _guided.GuidedTestView,
            "LaunchTestView",
        ),
        (
            setup._open_test_launch is _guided.open_guided_tests,
            "_open_test_launch",
        ),
        (
            _implementation.setup.solid._build_category_manager_payload
            is _implementation._category_payload,
            "ticket category presentation",
        ),
    )
    missing = [label for okay, label in checks if not okay]
    if missing:
        raise RuntimeError(
            "public setup runtime ownership drift: " + ", ".join(missing)
        )


def apply_public_setup_runtime() -> None:
    """Install the complete setup presentation in one deterministic order.

    Re-running is intentional and safe.  Compact binds the canonical setup
    presentation first, guided testing is then reasserted last, and navigation /
    health contracts are applied after both.  No guild configuration or Discord
    resources are created here.
    """

    _implementation._PATCHED = False
    _original_apply_compact_setup_patch()

    # Guided testing must be the last presentation owner.  Its installer is
    # intentionally re-entrant so a late compact rebind can never erase it.
    _guided.apply_guided_test_patch()

    install_custom_service_navigation_compat()
    install_voice_health_contract()
    _assert_runtime_ownership()


# Keep the historical name for callers such as public_setup_gate, but make it
# impossible for that path to install compact-without-guided again.
_implementation.apply_public_setup_runtime = apply_public_setup_runtime
_implementation.apply_compact_setup_patch = apply_public_setup_runtime


def _register_public_setup_runtime(bot, tree) -> None:
    _ = bot, tree
    apply_public_setup_runtime()
    print("✅ public_setup_runtime: deterministic compact + guided setup active")


_implementation.register_public_setup_compact_commands = _register_public_setup_runtime
_implementation._GUIDED_TEST_REGISTER_WRAPPED = True
_implementation._REASSERTING_APPLY_WRAPPED = True

# Direct imports, setup-gate registration, and compatibility tests all converge
# on the same final runtime owner.
apply_public_setup_runtime()

sys.modules[__name__] = _implementation
