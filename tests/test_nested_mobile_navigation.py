"""Focused nested Discord menu reachability for both mobile and desktop.

The same Discord components are rendered on both clients. The tests check
actual instantiated view children, not merely an AST inventory.
"""

from stoney_verify.commands_ext.public_community_hub import (
    SessionControlView,
    _SESSION_CONTROL_SECTIONS,
)
from stoney_verify.commands_ext.public_community_pings import (
    CommunityOptionEditorView,
    _OPTION_EDITOR_SECTIONS,
)


def _buttons(view):
    return [item for item in view.children if hasattr(item, "label")]


def test_session_controls_are_reachable_without_dense_button_wall():
    screens = {
        section: SessionControlView(987654321, session_id="test-session", section=section)
        for section in _SESSION_CONTROL_SECTIONS
    }
    expected = set().union(*_SESSION_CONTROL_SECTIONS.values())
    visible = set()

    for section, view in screens.items():
        labels = {item.label for item in _buttons(view)}
        assert len(view.children) <= 9, section
        assert len(labels) == len(_buttons(view)), section
        assert "Back to Group" in labels
        assert _SESSION_CONTROL_SECTIONS[section] <= labels
        assert len(labels & {"Session", "People", "Outside"}) == 2
        assert all(0 <= item.row <= 4 for item in _buttons(view))
        visible.update(labels)
    assert expected <= visible
    assert "End Session" in screens["session"].children[6].label or (
        "End Session" in {item.label for item in _buttons(screens["session"])}
    )


def test_community_option_editor_retains_each_action_with_small_sections():
    all_actions = set().union(*_OPTION_EDITOR_SECTIONS.values())
    visible = set()
    for section in _OPTION_EDITOR_SECTIONS:
        view = CommunityOptionEditorView(987654321, "fake-option", section=section)
        labels = {item.label for item in _buttons(view)}
        assert len(view.children) <= 9, section
        assert len(labels) == len(_buttons(view)), section
        assert _OPTION_EDITOR_SECTIONS[section] == tuple(
            label for label in _OPTION_EDITOR_SECTIONS[section]
        )
        assert set(_OPTION_EDITOR_SECTIONS[section]) <= labels
        assert {"Back", "Close"} <= labels
        assert any(getattr(item, "custom_id", None) == "dank:pings:option:section:v1"
                   for item in view.children)
        assert all(0 <= item.row <= 4 for item in _buttons(view))
        visible.update(labels)
    assert all_actions <= visible
    assert {"Edit Details", "Prerequisite", "Toke Starter",
            "Movie Night Notify", "Move Up", "Delete"} <= all_actions


def test_unknown_nested_section_defaults_to_safe_overview():
    session = SessionControlView(987654321, session_id="test", section="unknown")
    option = CommunityOptionEditorView(987654321, "fake-option", section="unknown")
    assert session.section == "session"
    assert option.section == "details"
    assert len(session.children) <= 9
    assert len(option.children) <= 9
