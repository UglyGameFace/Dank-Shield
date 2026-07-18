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


def test_secondary_setup_screens_use_the_same_plain_language():
    legacy_choice = block(RECOMMEND, "class SetupChoiceView(", "class SetupReviewFixNextButton(")
    for stale in ("Use My Existing Server", "Create Missing Items", "Health Check"):
        assert stale not in legacy_choice
    for stale in ("Service switches opened", "Service Switches Did Not Open", "Advanced Options •", "Timers & Behavior", "Use Existing Roles / Channels"):
        assert stale not in RECOMMEND


def test_custom_feature_picker_avoids_setup_jargon():
    for stale in ("Ticket panel and ticket lifecycle only.", "No ID, no VC", "verify blockers", "Custom mix:", "Basic Verify only", "Basic + Voice Verify"):
        assert stale not in FRESH
    for expected in ("Support ticket panel and ticket tools.", "Simple Verify only", "Simple + Voice Verify", "Your features:", "Simple Verify"):
        assert expected in FRESH


def test_plain_language_fallback_actions_are_consistent():
    for expected in ("Choose Existing Roles & Channels", "Choose which features are ON or OFF", "Feature Settings Did Not Open", "Check Setup Again", "Fix Setup or Start Over"):
        assert expected in RECOMMEND


def test_launch_and_fallback_copy_use_plain_language():
    for stale in ("manual service editor", "each service on/off", "Post Basic Verify Panel", "with an alt", "those switches are ON", "allowlisted/private servers"):
        assert stale not in RECOMMEND
    for expected in ("Post Simple Verify Panel", "second test account", "Simple Verify gives the member role", "servers approved to use it"):
        assert expected in RECOMMEND


def test_custom_saved_copy_uses_feature_language():
    for stale in ("Custom setup service switches.", "allowlisted servers only", "pre-selected:"):
        assert stale not in FRESH
    for expected in ("Custom feature choices.", "servers approved to use this feature", "turned on matching features"):
        assert expected in FRESH


def test_help_and_progress_only_teach_current_setup_path():
    help_block = block(RECOMMEND, "def _build_setup_help_embed()", "async def _setup_progress(")
    for stale in ("Use My Existing Server", "Create Missing Items", "legacy single-server", "hardcoded", "Choose Setup Type"):
        assert stale not in help_block
    for expected in ("Start Setup", "Set Up This Step", "Test & Launch", "More Options"):
        assert expected in help_block

    progress = block(RECOMMEND, "async def _setup_progress(", "async def _product_main_setup_payload(")
    for stale in ("Open Manage Setup", "Use Things I Already Made", "with an alt", "Basic Verify"):
        assert stale not in progress
    assert "Continue Setup" in progress
    assert "second Discord account" in progress
