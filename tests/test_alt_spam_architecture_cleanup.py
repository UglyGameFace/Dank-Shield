from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_retired_risk_monkey_patches_are_gone() -> None:
    retired = (
        "stoney_verify/startup_guards/raidguard_hard_stop.py",
        "stoney_verify/startup_guards/raidguard_bot_heuristics.py",
        "stoney_verify/startup_guards/raidguard_risk_engine_v2.py",
        "runtime_raidguard_hard_stop.py",
        "runtime_raidguard_bot_heuristics_patch.py",
        "runtime_raidguard_risk_engine_v2_patch.py",
    )
    assert all(not (ROOT / path).exists() for path in retired)

    loader = (
        ROOT / "stoney_verify/startup_guards/__init__.py"
    ).read_text(encoding="utf-8")
    for name in (
        "raidguard_hard_stop",
        "raidguard_bot_heuristics",
        "raidguard_risk_engine_v2",
    ):
        assert name not in loader


def test_join_ownership_has_no_legacy_duplicate_emitters() -> None:
    events = (ROOT / "stoney_verify/events.py").read_text(encoding="utf-8")
    router = (
        ROOT / "stoney_verify/startup_guards/member_lifecycle_router_guard.py"
    ).read_text(encoding="utf-8")

    assert "Alt/Cluster Flag" not in events
    assert "ALT_JOIN_BUCKETS" not in events
    assert "_send_staff_join_audit" not in router
    assert "_send_staff_leave_audit" not in router
    assert "_detect_invite(member)" not in router
