from pathlib import Path


RECOMMEND = Path(
    "stoney_verify/commands_ext/public_setup_recommend.py"
).read_text(encoding="utf-8")

FRESH = Path(
    "stoney_verify/commands_ext/public_setup_fresh_choice.py"
).read_text(encoding="utf-8")


def block(
    source: str,
    start: str,
    end: str,
) -> str:
    left = source.index(start)
    right = source.index(end, left)
    return source[left:right]


def test_home_has_one_setup_entry():
    home = block(
        RECOMMEND,
        "class ProductSetupHomeView(",
        "class ContinueSetupView(",
    )

    assert "Start Setup" in home
    assert "More Options" in home
    assert "_open_guided_setup(interaction)" in home
    assert "Use Things I Already Made" not in home
    assert "Make Missing Things For Me" not in home
    assert "Service Switches" not in home


def test_guided_screen_has_one_fix_action():
    guided = block(
        RECOMMEND,
        "class ContinueSetupView(",
        "class ManageSetupView(",
    )

    assert "Set Up This Step" in guided
    assert "Setup Check" not in guided
    assert "Change Setup Type" not in guided
    assert "Advanced Options" not in guided
    assert "Back Home" in guided

    assert "Use Things I Already Made" not in guided
    assert "Make Missing Things For Me" not in guided
    assert "Service Switches" not in guided


def test_guided_target_is_structured():
    target = block(
        RECOMMEND,
        "async def _guided_setup_target(",
        "async def _open_guided_target(",
    )

    for key in (
        '"setup_type"',
        '"services"',
        '"permissions"',
        '"roles"',
        '"folders"',
        '"channels"',
        '"ticket_choices"',
        '"logs"',
        '"ready"',
    ):
        assert key in target



def test_saved_preset_enters_guided_setup():
    choice = block(
        FRESH,
        "class SetupTypeChoiceView(",
        "def register_public_setup_fresh_choice_commands(",
    )

    assert "recommend._open_guided_setup(" in choice
    assert "SetupTypeChoiceView" in choice
    assert "id_verify_allowed_for_guild" in choice


def test_custom_setup_has_switches_then_one_continue_button():
    custom = block(
        FRESH,
        "class CustomServiceModeView(",
        "async def _open_custom_service_picker(",
    )

    assert "Continue Setup" in custom
    assert "CustomServiceToggleButton" in custom
    assert "Advanced Options" not in custom
    assert 'label="Back"' in custom

    assert "Use My Existing Server" not in custom
    assert "Review / Create Missing Items" not in custom



def test_duplicate_setup_home_family_is_retired():
    retired_classes = (
        "PlainSetupHomeView",
        "PlainContinueSetupView",
        "PlainLaunchView",
        "AfterChoiceView",
        "CreateMissingItemsView",
    )

    retired_functions = (
        "_choice_lines",
        "_bool_icon",
        "_setup_progress_for_home",
        "_service_summary_for_home",
        "_choice_preview_embed",
        "_edit_setup_message",
        "_open_existing_server_setup",
        "_open_create_missing_items",
        "_open_ticket_menu_options",
        "_build_setup_help_embed",
        "_plain_choice_main_payload",
    )

    for class_name in retired_classes:
        assert f"class {class_name}(" not in FRESH

    for function_name in retired_functions:
        assert f"def {function_name}(" not in FRESH





def test_setup_type_choice_has_one_guided_exit():
    choice = block(
        FRESH,
        "class SetupTypeChoiceView(",
        "def register_public_setup_fresh_choice_commands(",
    )

    assert "recommend._open_guided_setup(" in choice
    assert "_open_custom_service_picker(" in choice

    assert "Use My Existing Server" not in choice
    assert "Create Missing Items" not in choice
    assert "Service Switches" not in choice
