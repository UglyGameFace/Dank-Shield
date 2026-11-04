from __future__ import annotations

import asyncio
from types import SimpleNamespace

import discord

from stoney_verify import action_center_service
from stoney_verify.commands_ext import public_self_roles_group as profile
from stoney_verify.commands_ext.public_member_setup import (
    _looks_like_member_setup_panel,
    _message_component_ids,
)
from stoney_verify.ui import cards


def _custom_ids(view: discord.ui.BaseView) -> list[str]:
    return [
        str(item.custom_id)
        for item in view.walk_children()
        if isinstance(item, discord.ui.Button) and item.custom_id
    ]


def _suffixes(view: discord.ui.BaseView) -> set[str]:
    return {custom_id[len(profile.PROFILE_PREFIX):] for custom_id in _custom_ids(view)}


def _button(view: discord.ui.BaseView, custom_id: str) -> discord.ui.Button:
    for item in view.walk_children():
        if isinstance(item, discord.ui.Button) and item.custom_id == custom_id:
            return item
    raise AssertionError(f"missing button {custom_id}")


def _member(*role_names: str) -> SimpleNamespace:
    return SimpleNamespace(roles=[SimpleNamespace(name=name) for name in role_names])


# Actions the legacy panels already route through _handle_profile_interaction.
_LEGACY_SUFFIXES = _suffixes(profile.ProfilePanelView()) | _suffixes(profile.ProfileEditView())


def test_public_panel_is_persistent_and_only_uses_routed_actions() -> None:
    view = profile.ProfilePanelLayout()

    assert view.is_persistent()
    assert all(custom_id.startswith(profile.PROFILE_PREFIX) for custom_id in _custom_ids(view))
    assert _suffixes(view) - _LEGACY_SUFFIXES == {"suggest"}
    assert view.has_components_v2()


def test_public_panel_leads_with_one_main_action() -> None:
    view = profile.ProfilePanelLayout()
    buttons = [item for item in view.walk_children() if isinstance(item, discord.ui.Button)]

    assert len(buttons) == 4
    main = _button(view, f"{profile.PROFILE_PREFIX}edit")
    assert main.style is discord.ButtonStyle.success
    assert [b for b in buttons if b.style is discord.ButtonStyle.success] == [main]


def test_public_panel_notice_keeps_same_actions() -> None:
    plain = profile.ProfilePanelLayout()
    with_notice = profile.ProfilePanelLayout(notice="Setup update 3 · Minor")

    assert _custom_ids(plain) == _custom_ids(with_notice)
    texts = [item.content for item in with_notice.walk_children() if isinstance(item, discord.ui.TextDisplay)]
    assert any("Setup update 3" in content for content in texts)


def test_editor_shows_current_values_and_add_for_empty_sections() -> None:
    member = _member("Pronouns: they/them", "Interest: Gaming", "Interest: Music")
    view = profile.ProfileEditorLayout(member)
    texts = "\n".join(item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay))

    assert "they/them" in texts
    assert "Gaming, Music" in texts
    assert _button(view, f"{profile.PROFILE_PREFIX}open:pronouns").label == "Change"
    assert _button(view, f"{profile.PROFILE_PREFIX}open:identity").label == "Add"
    assert _button(view, f"{profile.PROFILE_PREFIX}open:identity").style is discord.ButtonStyle.success


def test_editor_actions_are_unique_and_already_routed() -> None:
    member = _member()
    for review in ("", "The server added new options. Take a look?"):
        view = profile.ProfileEditorLayout(member, setup_review=review)
        ids = _custom_ids(view)

        assert len(ids) == len(set(ids)), "Discord rejects duplicate custom IDs in one message"
        assert _suffixes(view) <= _LEGACY_SUFFIXES
        assert f"{profile.PROFILE_PREFIX}member_setup" in ids
        assert cards.component_count(view) <= cards.MAX_COMPONENTS


def test_suggest_screen_reuses_existing_request_modals() -> None:
    assert _suffixes(profile.ProfileSuggestLayout()) == {"missing", "missing_interest"}


def test_setup_review_notice_reflects_member_setup_state(monkeypatch) -> None:
    async def required(_member):
        return SimpleNamespace(severity="required")

    async def recommended(_member):
        return SimpleNamespace(severity="recommended")

    async def nothing(_member):
        return None

    async def broken(_member):
        raise RuntimeError("database down")

    member = _member()
    monkeypatch.setattr(action_center_service, "_read_member_setup", required)
    assert "needs you to review" in asyncio.run(profile._profile_setup_review_notice(member))

    monkeypatch.setattr(action_center_service, "_read_member_setup", recommended)
    assert "new options" in asyncio.run(profile._profile_setup_review_notice(member))

    monkeypatch.setattr(action_center_service, "_read_member_setup", nothing)
    assert asyncio.run(profile._profile_setup_review_notice(member)) == ""

    monkeypatch.setattr(action_center_service, "_read_member_setup", broken)
    assert asyncio.run(profile._profile_setup_review_notice(member)) == ""


def test_member_setup_finds_profile_buttons_nested_in_layout_panels() -> None:
    edit = SimpleNamespace(custom_id=f"{profile.PROFILE_PREFIX}edit")
    suggest = SimpleNamespace(custom_id=f"{profile.PROFILE_PREFIX}suggest")
    container = SimpleNamespace(
        children=[
            SimpleNamespace(content="## Make your profile"),
            SimpleNamespace(children=[edit]),
            SimpleNamespace(children=[SimpleNamespace(content="Don't see yours?")], accessory=suggest),
        ]
    )
    message = SimpleNamespace(author=SimpleNamespace(id=7), components=[container], embeds=[])
    guild = SimpleNamespace(me=SimpleNamespace(id=7))

    assert _message_component_ids(message) == {edit.custom_id, suggest.custom_id}
    assert _looks_like_member_setup_panel(message, guild) is True


def test_card_helpers() -> None:
    assert cards.summarize([]) == "Not set"
    assert cards.summarize(["a", "b"]) == "a, b"
    assert cards.summarize(["a", "b", "c", "d", "e"]) == "a, b, c +2"
    assert cards.clip("x" * 10, 5) == "xxxx…"
    assert len(cards.button("y" * 200, "id").label) <= cards.MAX_BUTTON_LABEL
