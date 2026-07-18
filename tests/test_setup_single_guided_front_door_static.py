from pathlib import Path


RECOMMEND = Path(
    "stoney_verify/commands_ext/public_setup_recommend.py"
).read_text(encoding="utf-8")

FRESH = Path(
    "stoney_verify/commands_ext/public_setup_fresh_choice.py"
).read_text(encoding="utf-8")


def section(
    source: str,
    start: str,
    end: str,
) -> str:
    left = source.index(start)
    right = source.index(end, left)
    return source[left:right]



def test_normal_front_door_has_no_competing_setup_methods():
    """Canonical setup screens must not expose competing systems."""

    import ast

    def class_source(
        source: str,
        class_name: str,
    ) -> str:
        tree = ast.parse(source)

        matches = [
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            and node.name == class_name
        ]

        assert len(matches) == 1

        return (
            ast.get_source_segment(
                source,
                matches[0],
            )
            or ""
        )

    normal_sections = (
        class_source(
            RECOMMEND,
            "ProductSetupHomeView",
        ),
        class_source(
            FRESH,
            "SetupTypeChoiceView",
        ),
    )

    forbidden = (
        "Use My Existing Server",
        "Use Existing Server",
        "Use Things I Already Made",
        "Create Missing Items",
        "Make Missing Things For Me",
        "Service Switches",
    )

    for current in normal_sections:
        for label in forbidden:
            assert label not in current




def test_custom_switches_are_explicitly_custom_only():
    custom = section(
        FRESH,
        "class CustomServiceModeView(",
        "async def _open_custom_service_picker(",
    )

    assert "Custom Setup only" in custom
    assert "CustomServiceToggleButton" in custom
    assert "Continue Setup" in custom


def test_advanced_tools_still_exist():
    """Every optional tool remains reachable under literal secondary labels."""
    import ast

    tree = ast.parse(RECOMMEND)
    classes = {
        node.name: ast.get_source_segment(RECOMMEND, node) or ""
        for node in tree.body
        if isinstance(node, ast.ClassDef)
    }

    more = classes["ManageSetupView"]
    for label in (
        "Change Setup Type",
        "Other Settings",
        "Check Setup for Problems",
        "Fix Setup or Start Over",
        "Help",
        "Back Home",
    ):
        assert label in more

    hub = classes["AdvancedSettingsHubView"]
    for label in (
        "Features, Roles & Channels",
        "Tickets",
        "Logs & Safety",
        "Server Design",
    ):
        assert label in hub

    advanced = "\n".join(
        classes[name]
        for name in (
            "AdvancedCoreSetupView",
            "AdvancedMemberExperienceView",
            "AdvancedMonitoringRepairView",
            "AdvancedAppearanceView",
            "AdvancedDangerZoneView",
        )
    )
    for label in (
        "Turn Features On / Off",
        "Ticket Choices",
        "Spam & Raid Protection",
        "Choose What Gets Logged",
        "Timers & Rules",
        "Server Design",
        "Choose Roles & Channels",
        "Fix Channel Permissions",
        "Fix or Start Over",
        "Back Home",
    ):
        assert label in advanced

    for route in (
        "_open_services",
        "_open_ticket_menu",
        "_open_protection_options",
        "_open_modlog_tracking",
        "_open_timers_behavior",
        "_open_existing_server",
        "_open_permission_repair",
        "_open_recovery_center",
        "_home_edit",
    ):
        assert route in advanced

    assert "class PlainManageSetupView" not in FRESH
    assert "Member Experience" not in RECOMMEND
