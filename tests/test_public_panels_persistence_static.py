from pathlib import Path


VERIFY_UI = Path("stoney_verify/verify_ui.py").read_text(encoding="utf-8")
PROFILE = Path("stoney_verify/commands_ext/public_self_roles_group.py").read_text(encoding="utf-8")


def test_public_verify_panel_is_persistent():
    assert "class VerifyView" in VERIFY_UI
    assert "super().__init__(timeout=None)" in VERIFY_UI
    assert 'custom_id="sv:verify:get"' in VERIFY_UI
    assert 'custom_id="sv:verify:vc"' in VERIFY_UI
    assert 'custom_id="sv:verify:raw"' in VERIFY_UI


def test_public_profile_panel_is_persistent_and_registered_on_boot():
    assert "class ProfilePanelView" in PROFILE
    start = PROFILE.index("class ProfilePanelView")
    end = PROFILE.index("def register_profile_panel_runtime", start)
    block = PROFILE[start:end]

    assert "super().__init__(timeout=None)" in block
    assert 'custom_id=f"{PROFILE_PREFIX}view"' in block
    assert 'custom_id=f"{PROFILE_PREFIX}open:pronouns"' in block
    assert 'custom_id=f"{PROFILE_PREFIX}open:identity"' in block
    assert 'custom_id=f"{PROFILE_PREFIX}open:interests"' in block
    assert "add_view(ProfilePanelView())" in PROFILE


def test_public_advanced_self_role_panel_is_persistent():
    start = PROFILE.index("class AdvancedSelfRolePanelView")
    end = PROFILE.index("async def _post_advanced_panel", start)
    block = PROFILE[start:end]

    assert "super().__init__(timeout=None)" in block
    assert 'custom_id=f"{SELF_ROLE_PREFIX}{int(role.id)}"' in block


def test_private_builders_can_expire_but_public_panels_cannot():
    assert "class ProfileBuilderView" in PROFILE
    assert "super().__init__(timeout=300)" in PROFILE
    assert "class ProfileCategorySelectView" in PROFILE
    assert "super().__init__(timeout=300)" in PROFILE


def test_slow_profile_role_actions_ack_before_work():
    assert "async def _ack_profile_action" in PROFILE

    select_start = PROFILE.index('if suffix.startswith("select:")')
    select_end = PROFILE.index('if suffix == "clear"', select_start)
    assert "await _ack_profile_action(interaction)" in PROFILE[select_start:select_end]

    clear_start = PROFILE.index('if suffix == "clear"')
    clear_end = PROFILE.index('if suffix == "missing"', clear_start)
    assert "await _ack_profile_action(interaction)" in PROFILE[clear_start:clear_end]

    self_role_start = PROFILE.index("async def _handle_self_role")
    self_role_end = PROFILE.index("def _panel_guard_custom_id", self_role_start)
    assert "await _ack_profile_action(interaction)" in PROFILE[self_role_start:self_role_end]
