from __future__ import annotations

"""Compatibility import for the presentation-only compact setup module."""

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


# Do not freeze a copy of the plan callback into compact views. The canonical
# setup route may be replaced by entitlement and recovery guards at startup.
_implementation.CompactSetupHomeView._plan = _canonical_plan_route
_implementation.CompactManagerView._plan = _canonical_plan_route

install_custom_service_navigation_compat()

# ``importlib.reload(public_setup_solid)`` is used by a few compatibility and
# registration tests. Reloading that module can restore its original function
# objects while this presentation module legitimately remains loaded. The old
# installer trusted a stale ``_PATCHED=True`` flag and could leave an impossible
# half-patched runtime. Re-run the deterministic assignments whenever requested,
# and make the patch-state truth check repair ownership before reporting True.
if not getattr(_implementation, "_REASSERTING_APPLY_WRAPPED", False):
    _original_apply_compact_setup_patch = _implementation.apply_compact_setup_patch

    class _ReassertingPatchState:
        def __bool__(self) -> bool:
            try:
                if (
                    _implementation.setup.solid._build_category_manager_payload
                    is not _implementation._category_payload
                ):
                    _reasserting_apply_compact_setup_patch()
            except Exception:
                pass
            return True

    _patch_state = _ReassertingPatchState()

    def _reasserting_apply_compact_setup_patch() -> None:
        # Force the original deterministic installer through its one-time guard.
        # It only rebinds callbacks/classes; it does not create Discord resources
        # or mutate guild configuration, so repeating it is safe.
        _implementation._PATCHED = False
        _original_apply_compact_setup_patch()
        _implementation._PATCHED = _patch_state

    _implementation.apply_compact_setup_patch = _reasserting_apply_compact_setup_patch
    _implementation._REASSERTING_APPLY_WRAPPED = True

if not getattr(_implementation, "_GUIDED_TEST_REGISTER_WRAPPED", False):
    _original_register = _implementation.register_public_setup_compact_commands

    def _register_with_guided_test(bot, tree):
        _original_register(bot, tree)
        _guided.apply_guided_test_patch()
        install_custom_service_navigation_compat()
        install_voice_health_contract()
        print("✅ public_setup_guided_test: one-button guided test sequence active")

    _implementation.register_public_setup_compact_commands = _register_with_guided_test
    _implementation._GUIDED_TEST_REGISTER_WRAPPED = True

# Cover direct imports in tests and repair processes where compact setup has
# already been applied before its registrar is probed again.
_implementation.apply_compact_setup_patch()
install_voice_health_contract()

sys.modules[__name__] = _implementation
