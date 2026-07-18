from pathlib import Path

RECOMMEND = Path("stoney_verify/commands_ext/public_setup_recommend.py").read_text(encoding="utf-8")
FRESH = Path("stoney_verify/commands_ext/public_setup_fresh_choice.py").read_text(encoding="utf-8")


def block(source: str, start: str, end: str) -> str:
    left = source.index(start)
    right = source.index(end, left)
    return source[left:right]


def test_home_has_one_primary_path_and_more_options():
    home = block(RECOMMEND, "class ProductSetupHomeView(", "class ContinueSetupView(")
    assert "Start Setup" in home
    assert "Continue Setup" in home
    assert "Test & Launch" in home
    assert "More Options" in home
    assert 'label="Setup Check"' not in home
    assert 'label="Manage Setup"' not in home


def test_guided_setup_has_only_current_step_and_home():
    guided = block(RECOMMEND, "class ContinueSetupView(", "class ManageSetupView(")
    assert "Set Up This Step" in guided
    assert "Back Home" in guided
    assert "Setup Check" not in guided
    assert "Change Setup Type" not in guided
    assert "Advanced Options" not in guided


def test_more_options_uses_literal_labels():
    more = block(RECOMMEND, "class ManageSetupView(", "class AdvancedCoreSetupView(")
    for text in ("Change Setup Type", "Other Settings", "Check Setup for Problems", "Fix Setup or Start Over", "Help", "Back Home"):
        assert text in more
    assert "Member Experience" not in more
    assert "Core Setup" not in more
    assert "Monitoring & Repair" not in more
    assert "Danger Zone" not in more


def test_advanced_hub_uses_plain_task_names():
    hub = block(RECOMMEND, "class AdvancedSettingsHubView(", "class AdvancedCoreSetupView(")
    assert "Features, Roles & Channels" in hub
    assert "Tickets" in hub
    assert "Logs & Safety" in hub
    assert "Server Design" in hub
    assert "Member Experience" not in hub


def test_setup_review_has_only_next_action_and_home():
    review = block(RECOMMEND, "class SetupReviewView(", "class SetupHealthHelpView(")
    assert "SetupReviewHomeButton" in review
    assert "SetupReviewAdvancedButton" not in review
    assert "SetupReviewChangeTypeButton" not in review
    assert "SetupReviewHelpButton" not in review


def test_custom_setup_stays_on_feature_choice_only():
    custom = block(FRESH, "class CustomServiceModeView(", "async def _open_custom_service_picker(")
    assert "Continue Setup" in custom
    assert 'label="Back"' in custom
    assert "Setup Check" not in custom
    assert "Advanced Options" not in custom
    assert "Setup Home" not in custom


def test_setup_type_uses_one_select_not_button_wall():
    choice = block(FRESH, "class SetupTypeChoiceSelect(", "def register_public_setup_fresh_choice_commands(")
    assert "discord.ui.Select" in choice
    assert "What do you want Dank Shield to do?" in choice
    for custom_id in ("dank_setup_choice:basic", "dank_setup_choice:basic_verify", "dank_setup_choice:helpdesk", "dank_setup_choice:voice", "dank_setup_choice:custom"):
        assert custom_id not in choice


def test_no_vague_setup_group_names_remain_in_user_navigation():
    for text in ("Member Experience", "Core Setup", "Monitoring & Repair", "Setup Check / Diagnostics"):
        assert text not in RECOMMEND


def test_plain_language_action_labels_exist():
    for text in ("Turn Features On / Off", "Timers & Rules", "Choose Roles & Channels", "Choose What Gets Logged", "Spam & Raid Protection", "Fix Channel Permissions"):
        assert text in RECOMMEND


def test_setup_type_names_are_plain_language():
    for text in ("Tickets + Server Basics", "Simple Verify", "Help Desk / Tickets", "Choose My Own Features"):
        assert text in FRESH
