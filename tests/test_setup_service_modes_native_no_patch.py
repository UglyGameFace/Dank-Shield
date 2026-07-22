from __future__ import annotations

import importlib
import sys

from stoney_verify.commands_ext import public_setup_solid as solid
from stoney_verify import startup_guards


RETIRED_SETUP_PATCH_MODULES = {
    "stoney_verify.startup_guards.setup_service_modes",
    "stoney_verify.startup_guards.setup_verification_toggle_independence_guard",
    "stoney_verify.startup_guards.setup_save_next_step_guard",
    "stoney_verify.startup_guards.setup_check_existing_server_inference_guard",
    "stoney_verify.startup_guards.setup_check_ready_next_step_guard",
    "stoney_verify.startup_guards.setup_guided_flow_self_check",
}


def test_legacy_service_module_is_not_loaded_as_startup_guard() -> None:
    active = set(startup_guards._STARTUP_GUARDS)
    assert RETIRED_SETUP_PATCH_MODULES.isdisjoint(active)


def test_importing_compat_service_does_not_replace_canonical_builders() -> None:
    before_home = solid._build_main_setup_payload
    before_health = solid._build_health_embed

    sys.modules.pop(
        "stoney_verify.startup_guards.setup_service_modes",
        None,
    )
    module = importlib.import_module(
        "stoney_verify.startup_guards.setup_service_modes"
    )

    assert solid._build_main_setup_payload is before_home
    assert solid._build_health_embed is before_health

    assert module.install_setup_service_modes() is True
    assert solid._build_main_setup_payload is before_home
    assert solid._build_health_embed is before_health


def test_compat_service_exports_remain_available_without_ui_ownership() -> None:
    module = importlib.import_module(
        "stoney_verify.startup_guards.setup_service_modes"
    )

    for name in (
        "install_setup_service_modes",
        "load_service_state",
        "build_service_picker_embed",
        "build_spamguard_setup_embed",
        "ServiceModeView",
        "SpamGuardSetupView",
    ):
        assert hasattr(module, name)


def test_retired_setup_patch_modules_are_not_importable() -> None:
    for module_name in sorted(RETIRED_SETUP_PATCH_MODULES - {
        "stoney_verify.startup_guards.setup_service_modes"
    }):
        try:
            importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue
        raise AssertionError(
            f"retired setup patch module still exists: {module_name}"
        )
