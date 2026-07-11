from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SERVICE = (ROOT / "stoney_verify/member_review_feedback.py").read_text(encoding="utf-8")
UI = (ROOT / "stoney_verify/member_review_ui.py").read_text(encoding="utf-8")
COMMAND = (
    ROOT / "stoney_verify/commands_ext/public_member_review_feedback.py"
).read_text(encoding="utf-8")
ROUTER = (
    ROOT / "stoney_verify/startup_guards/member_lifecycle_router_guard.py"
).read_text(encoding="utf-8")
REGISTRY = (
    ROOT / "stoney_verify/commands_ext/__init__.py"
).read_text(encoding="utf-8")


def test_feedback_is_guild_scoped_and_audited() -> None:
    assert 'sb.table("member_events")' in SERVICE
    assert '"guild_id": guild_text' in SERVICE
    assert '"user_id": user_text' in SERVICE
    assert '"actor_id": actor_text' in SERVICE
    assert '"evidence_snapshot"' in SERVICE
    assert '"automatic_enforcement": False' in SERVICE


def test_all_staff_verdicts_exist() -> None:
    for token in (
        "looks_safe",
        "watch_member",
        "false_positive",
        "approved_bot",
        "suspicious_bot",
        "bad_invite_source",
        "clear_invite_source",
        "likely_alt",
        "confirmed_alt",
        "reset",
    ):
        assert token in SERVICE
        assert token in UI


def test_alt_feedback_uses_existing_identity_truth_service() -> None:
    assert "confirm_duplicate_users" in SERVICE
    assert "mark_users_likely_same_person" in SERVICE
    assert "related_user_id" in SERVICE
    assert "A member cannot be linked to themselves" in SERVICE


def test_source_feedback_is_reusable_on_future_joins() -> None:
    assert "SOURCE_REVIEW_EVENT" in SERVICE
    assert "get_latest_source_review_feedback" in SERVICE
    assert "source_key_from_join_context" in SERVICE
    assert "Source Staff Verdict" in UI
    assert "Previous Source Verdict" in ROUTER


def test_join_staff_audit_has_review_controls() -> None:
    start = ROUTER.index("async def _send_staff_join_audit(")
    end = ROUTER.index("async def _send_staff_leave_audit(", start)
    block = ROUTER[start:end]

    assert "build_member_review_view" in block
    assert "view=review_view" in block
    assert "Previous Staff Verdict" in block
    assert "_build_member_context_fields" in block


def test_public_join_leave_card_does_not_get_staff_buttons() -> None:
    start = ROUTER.index("async def _send_join_leave_join(")
    end = ROUTER.index("async def _send_public_join(", start)
    block = ROUTER[start:end]
    assert "build_member_review_view" not in block


def test_review_buttons_do_not_punish_automatically() -> None:
    combined = SERVICE + UI + COMMAND
    for forbidden in (
        ".ban(",
        ".kick(",
        ".timeout(",
        ".add_roles(",
        ".remove_roles(",
    ):
        assert forbidden not in combined


def test_durable_command_fallback_is_registered() -> None:
    assert 'name="review"' in COMMAND
    assert 'name="review-history"' in COMMAND
    assert "public_member_review_feedback" in REGISTRY
    assert '"public_member_review_feedback"' in REGISTRY


if __name__ == "__main__":
    for test in (
        test_feedback_is_guild_scoped_and_audited,
        test_all_staff_verdicts_exist,
        test_alt_feedback_uses_existing_identity_truth_service,
        test_source_feedback_is_reusable_on_future_joins,
        test_join_staff_audit_has_review_controls,
        test_public_join_leave_card_does_not_get_staff_buttons,
        test_review_buttons_do_not_punish_automatically,
        test_durable_command_fallback_is_registered,
    ):
        test()
        print(f"PASS {test.__name__}")
