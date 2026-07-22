from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SERVICE = (
    ROOT / "stoney_verify/member_review_feedback.py"
).read_text(encoding="utf-8")
UI = (
    ROOT / "stoney_verify/member_review_ui.py"
).read_text(encoding="utf-8")
COMMAND = (
    ROOT / "stoney_verify/commands_ext/public_member_review_feedback.py"
).read_text(encoding="utf-8")
MEMBERS = (
    ROOT / "stoney_verify/commands_ext/public_members_group.py"
).read_text(encoding="utf-8")
EVENTS = (
    ROOT / "stoney_verify/events.py"
).read_text(encoding="utf-8")
REGISTRY = (
    ROOT / "stoney_verify/commands_ext/__init__.py"
).read_text(encoding="utf-8")


def test_feedback_is_guild_scoped_and_non_enforcing() -> None:
    assert 'sb.table("member_events")' in SERVICE
    assert '"guild_id": guild_text' in SERVICE
    assert '"user_id": user_text' in SERVICE
    assert '"actor_id": actor_text' in SERVICE
    assert '"automatic_enforcement": False' in SERVICE


def test_public_profile_loads_member_review_module() -> None:
    assert "register_public_member_review_feedback_commands" in REGISTRY

    start = REGISTRY.index("_PUBLIC_CORE_MODULES:")
    end = REGISTRY.index("_PUBLIC_ADMIN_EXTRA_MODULES:", start)
    core = REGISTRY[start:end]

    assert '"public_member_review_feedback"' in core


def test_review_command_opens_panel_before_verdict() -> None:
    start = COMMAND.index('if "review" not in existing:')
    end = COMMAND.index('if "history" not in existing:', start)
    block = COMMAND[start:end]

    assert "build_member_review_view" in block
    assert "_build_member_context_fields" in block
    assert "previous_feedback" in block
    assert "source_key" in block
    assert "verdict: app_commands.Choice" not in block
    assert "reason: str" not in block


def test_mobile_review_controls_are_compact() -> None:
    assert "class MoreReviewActionsSelect" in UI
    assert 'placeholder="More staff verdicts…"' in UI
    assert "Reset Review Verdict" in UI
    assert "identity links stay active" in UI
    assert "self.add_item(MoreReviewActionsSelect(self))" in UI


def test_command_permission_accepts_configured_staff_roles() -> None:
    assert "staff_role_id" in COMMAND
    assert "vc_staff_role_id" in COMMAND
    assert "get_guild_config" in COMMAND


def test_clean_command_names_replace_old_aliases() -> None:
    assert 'name="review"' in COMMAND
    assert 'name="history"' in COMMAND
    assert 'name="review-history"' not in COMMAND

    assert 'name="scan"' in MEMBERS
    assert 'name="scan-custom"' in MEMBERS
    assert 'name="scan-last"' in MEMBERS

    assert 'name="inactive"' not in MEMBERS
    assert 'name="advanced-scan"' not in MEMBERS
    assert 'name="last-scan"' not in MEMBERS


def test_reset_wording_is_honest() -> None:
    assert '"reset": "Reset Review Verdict"' in SERVICE
    assert '"identity_links_unchanged": verdict_text == "reset"' in SERVICE


def test_staff_audit_still_has_review_controls() -> None:
    start = EVENTS.index(
        "@bot.event\nasync def on_member_join(member: discord.Member):"
    )
    end = EVENTS.index(
        "@bot.event\nasync def on_member_remove(member: discord.Member):",
        start,
    )
    block = EVENTS[start:end]

    assert "build_member_review_view" in block
    assert "view=review_view" in block
    assert "Previous Staff Verdict" in block
    assert "event_key=f\"member_join:{member.id}\"" in block


def test_review_system_never_punishes_automatically() -> None:
    combined = SERVICE + UI + COMMAND

    for forbidden in (
        ".ban(",
        ".kick(",
        ".timeout(",
        ".add_roles(",
        ".remove_roles(",
    ):
        assert forbidden not in combined


if __name__ == "__main__":
    for test in (
        test_feedback_is_guild_scoped_and_non_enforcing,
        test_public_profile_loads_member_review_module,
        test_review_command_opens_panel_before_verdict,
        test_mobile_review_controls_are_compact,
        test_command_permission_accepts_configured_staff_roles,
        test_clean_command_names_replace_old_aliases,
        test_reset_wording_is_honest,
        test_staff_audit_still_has_review_controls,
        test_review_system_never_punishes_automatically,
    ):
        test()
        print(f"PASS {test.__name__}")
