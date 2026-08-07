from __future__ import annotations

"""Compatibility import for the presentation-only compact setup module."""

import sys

from stoney_verify.setup_ui import public_setup_compact as _implementation
from stoney_verify.setup_ui import public_setup_guided_test as _guided
from stoney_verify.setup_voice_health_contract import install_voice_health_contract

if not getattr(_implementation, "_GUIDED_TEST_REGISTER_WRAPPED", False):
    _original_register = _implementation.register_public_setup_compact_commands

    def _register_with_guided_test(bot, tree):
        _original_register(bot, tree)
        _guided.apply_guided_test_patch()
        install_voice_health_contract()
        print("✅ public_setup_guided_test: one-button guided test sequence active")

    _implementation.register_public_setup_compact_commands = _register_with_guided_test
    _implementation._GUIDED_TEST_REGISTER_WRAPPED = True

# Cover direct imports in tests and repair processes where compact setup has
# already been applied before its registrar is probed again.
install_voice_health_contract()

sys.modules[__name__] = _implementation
