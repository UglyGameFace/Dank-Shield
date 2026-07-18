from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

GUARD = (
    ROOT
    / "stoney_verify"
    / "startup_guards"
    / "setup_first_run_ux_guard.py"
)

REGISTRY = (
    ROOT
    / "stoney_verify"
    / "startup_guards"
    / "__init__.py"
).read_text(encoding="utf-8")

SELF_CHECK = (
    ROOT
    / "stoney_verify"
    / "startup_guards"
    / "setup_guided_flow_self_check.py"
).read_text(encoding="utf-8")

MAIN = (ROOT / "main.py").read_text(encoding="utf-8")

RECOMMEND = (
    ROOT
    / "stoney_verify"
    / "commands_ext"
    / "public_setup_recommend.py"
).read_text(encoding="utf-8")


def test_legacy_first_run_guard_is_retired() -> None:
    assert not GUARD.exists()

    for source in (
        REGISTRY,
        SELF_CHECK,
        MAIN,
    ):
        assert "setup_first_run_ux_guard" not in source


def test_native_setup_owns_first_run_experience() -> None:
    for marker in (
        "class ProductSetupHomeView",
        "class ContinueSetupView",
        "Start Setup",
        "More Options",
        "Set Up This Step",
        "Test & Launch",
        "Other Settings",
        "_open_guided_setup",
        "_open_manage_setup",
    ):
        assert marker in RECOMMEND

    for stale in (
        "Start / Continue Setup",
        "Advanced Options",
        "Fix Next Item",
        "Test / Launch",
    ):
        assert stale not in RECOMMEND


def test_retired_wrapper_assignment_is_gone() -> None:
    guards = (
        ROOT
        / "stoney_verify"
        / "startup_guards"
    )

    for path in guards.glob("*.py"):
        source = path.read_text(
            encoding="utf-8",
            errors="ignore",
        )

        assert (
            "setup_start._build_main_setup_payload = "
            "patched_build_main_setup_payload"
        ) not in source
