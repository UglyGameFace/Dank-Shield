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
    assert "Continue Guided Setup" in custom


def test_advanced_tools_still_exist():
    """Every advanced tool must live under one canonical owner."""

    import ast

    recommend_tree = ast.parse(RECOMMEND)

    matches = [
        node
        for node in recommend_tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "ManageSetupView"
    ]

    assert len(matches) == 1

    advanced = (
        ast.get_source_segment(
            RECOMMEND,
            matches[0],
        )
        or ""
    )

    required_labels = (
        "Features On / Off",
        "Ticket Choices",
        "Protection",
        "Timers & Behavior",
        "Server Design",
        "Detailed Role / Channel Mapping",
        "Recovery / Start Over",
        "Help / FAQ",
        "Back Home",
    )

    for label in required_labels:
        assert label in advanced

    required_routes = (
        "_open_services",
        "_open_ticket_menu",
        "_open_protection_options",
        "_open_timers_behavior",
        "_open_existing_server",
        "_open_recovery_center",
        "_home_edit",
    )

    for route in required_routes:
        assert route in advanced

    assert "class PlainManageSetupView" not in FRESH
    assert "recommend._open_manage_setup" in FRESH
