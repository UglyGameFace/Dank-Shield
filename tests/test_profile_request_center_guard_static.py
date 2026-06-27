from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUARD = (ROOT / "stoney_verify/startup_guards/profile_request_center_guard.py").read_text(encoding="utf-8")
SELF_ROLE_GUARD = (ROOT / "stoney_verify/startup_guards/self_roles_command_guard.py").read_text(encoding="utf-8")
SERVICE = (ROOT / "stoney_verify/services/profile_staff_requests.py").read_text(encoding="utf-8")


def test_public_profile_panel_main_page_no_direct_section_edit_buttons():
    assert "profile.ProfilePanelView = _build_clean_profile_panel_view(profile)" in GUARD
    clean_builder = GUARD[GUARD.index("def _build_clean_profile_panel_view"):GUARD.index("async def _identity_submit")]

    assert "Edit My Profile" in clean_builder
    assert "View My Profile" in clean_builder
    assert "View Member Profile" in clean_builder
    assert "Learn Terms" in clean_builder
    assert "Server Cosmetics" in clean_builder
    assert "Suggest Missing Interest" in clean_builder
    assert "Missing Identity?" in clean_builder
    assert "Clear Profile Roles" in clean_builder
    assert "Edit Pronouns" not in clean_builder
    assert "Edit Identity" not in clean_builder
    assert "Edit Interests" not in clean_builder


def test_missing_identity_and_interest_use_central_dispatcher():
    assert "dispatch_profile_staff_request" in GUARD
    assert "profile.MissingIdentityModal.on_submit = _identity_submit" in GUARD
    assert "profile.MissingInterestModal.on_submit = _interest_submit" in GUARD
    assert "request_type=\"identity\"" in GUARD
    assert "request_type=\"interest\"" in GUARD
    assert "sent to staff queue" in GUARD


def test_dispatcher_requires_confirmed_delivery_before_success_reply():
    assert "ProfileStaffRequestDelivery" in SERVICE
    assert "profile_staff_request_channels" in SERVICE
    assert "await channel.send" in SERVICE
    assert "return ProfileStaffRequestDelivery(\n                True" in SERVICE
    assert "No staff request/modlog/ticket channel is configured or sendable" in SERVICE
    assert "delivery_failed" in SERVICE


def test_staff_request_channel_resolution_is_centralized_and_ticket_like():
    assert "PROFILE_STAFF_REQUEST_CHANNEL_KEYS" in SERVICE
    assert "staff_requests_channel_id" in SERVICE
    assert "profile_requests_channel_id" in SERVICE
    assert "modlog_channel_id" in SERVICE
    assert "tickets" in SERVICE
    assert "ticket-logs" in SERVICE
    assert "AllowedMentions.none()" in SERVICE


def test_self_role_guard_installs_profile_request_center_before_registering_view():
    assert "profile_request_center_guard" in SELF_ROLE_GUARD
    assert "profile_request_center_guard.apply()" in SELF_ROLE_GUARD
    assert SELF_ROLE_GUARD.index("profile_request_center_guard.apply()") < SELF_ROLE_GUARD.index("register(bot")
