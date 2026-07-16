from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

RETIRED = (
    "modlog_center_tracking_guard.py",
    "setup_safety_repair_service_guard.py",
    "setup_service_navigation_guard.py",
    "setup_smart_home_menu_guard.py",
)


def test_patch_shell_guards_are_deleted() -> None:
    guard_dir = (
        ROOT
        / "stoney_verify"
        / "startup_guards"
    )

    for filename in RETIRED:
        assert not (
            guard_dir
            / filename
        ).exists()


def test_runtime_references_are_retired() -> None:
    registry = (
        ROOT
        / "stoney_verify"
        / "startup_guards"
        / "__init__.py"
    ).read_text(encoding="utf-8")

    main = (
        ROOT
        / "main.py"
    ).read_text(encoding="utf-8")

    protection = (
        ROOT
        / "stoney_verify"
        / "startup_guards"
        / "protection_center_invite_simple_flow_guard.py"
    ).read_text(encoding="utf-8")

    for filename in RETIRED:
        module_name = filename.removesuffix(".py")

        assert module_name not in registry
        assert module_name not in main
        assert module_name not in protection

    assert (
        "vc_join_leave_modlog_labels_guard"
        in registry
    )
