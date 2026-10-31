from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import discord

from stoney_verify import navigation_registry
from stoney_verify.help_guides import HELP_GUIDES, HELP_GUIDE_ERRORS
from stoney_verify.commands_ext import public_command_surface_v2 as nav


ROOT = Path(__file__).resolve().parents[1]


def test_all_registered_features_have_actionable_help_not_just_navigation():
    assert not HELP_GUIDE_ERRORS
    assert set(HELP_GUIDES) == {feature.key for feature in navigation_registry.FEATURES}
    assert len(HELP_GUIDES) == 36
    for feature in navigation_registry.FEATURES:
        guide = HELP_GUIDES[feature.key]
        assert len(guide.steps) >= 2
        assert guide.outcome and guide.purpose
        embed = nav._help_guide_embed(feature.key)
        assert feature.label in embed.title
        assert "**How to use**" in embed.description
        assert "**What to expect**" in embed.description
        assert "**1.**" in embed.description
        assert len(embed.description) <= 4096
        assert len(embed.fields) == 0
        if feature.manager_only:
            assert "Staff" in embed.footer.text
        if guide.caution:
            assert "**Important**" in embed.description


def test_help_entry_is_readable_and_explains_its_guides():
    embed = nav._help_embed()
    assert len(embed.description) < 400
    assert not embed.fields
    assert "how" in embed.description.lower() or "explains" in embed.description.lower()
    assert "Find Help" in embed.description
    assert "Browse Guides" in embed.description
    assert "Quick commands" not in str(embed.to_dict())
    assert "Other shortcuts" not in str(embed.to_dict())
    assert chr(96) + "/dank" not in str(embed.to_dict())

    view = nav.CompactHelpView(123)
    assert view.timeout == nav.PRIVATE_MENU_TTL_SECONDS
    assert {getattr(item, "label", None) for item in view.children} >= {
        "Find Help", "Browse Guides", "Home", "Close",
    }
    assert "dank:help:topics:v2" in _ids(view)


def _ids(view: discord.ui.View) -> list[str]:
    return [str(getattr(item, "custom_id", "")) for item in view.children]


def test_topic_pages_offer_first_step_then_full_howto_guides():
    assert 4 <= len(nav._HELP_TOPICS) <= 8
    for key, label, summary, feature_keys in nav._HELP_TOPICS:
        assert summary
        assert len(feature_keys) <= 3
        embed = nav._help_topic_embed(key)
        assert embed.title.endswith(label)
        view = nav.HelpTopicView(123, key)
        assert len(embed.fields) == len(feature_keys)
        assert len([c for c in view.children if isinstance(c, nav._HelpGuideButton)]) == len(feature_keys)
        for fkey, field in zip(feature_keys, embed.fields):
            feature = navigation_registry.feature_by_key(fkey)
            assert feature is not None
            assert feature.key in nav._FEATURE_ROUTE_KEYS
            assert HELP_GUIDES[fkey].steps[0] in field.value
            assert HELP_GUIDES[fkey].purpose in field.value
            button = next(c for c in view.children
                          if getattr(c, "custom_id", "") == f"dank:help:guide:{fkey}:v3")
            assert button.label.startswith("How to use")


def test_every_registered_feature_is_browsable_by_category():
    rendered = set()
    for cat in navigation_registry.CATEGORIES:
        view = nav.HelpCategoryView(123, cat.key)
        picker = next(child for child in view.children if isinstance(child, nav._HelpFeatureSelect))
        keys = {choice.value for choice in picker.options}
        expected = {feature.key for feature in navigation_registry.features_for_category(cat.key)}
        assert keys == expected
        rendered.update(keys)
        assert nav._help_category_embed(cat.key).title.endswith(cat.label)
        assert len(picker.options) <= 25
        _assert_mobile_components(view)
    assert rendered == set(HELP_GUIDES)


def _assert_mobile_components(view: discord.ui.View):
    assert len(view.children) <= 25
    ids = _ids(view)
    assert len(ids) == len(set(ids))
    assert all(len(item) <= 100 for item in ids)
    assert max((getattr(item, "row", None) or 0) for item in view.children) <= 4


def test_guides_keep_component_counts_and_custom_ids_safe():
    _assert_mobile_components(nav.CompactHelpView(123))
    for topic in nav._HELP_TOPICS:
        _assert_mobile_components(nav.HelpTopicView(123, topic[0]))
    for feature in navigation_registry.FEATURES:
        _assert_mobile_components(nav.HelpGuideView(123, feature.key))
    _assert_mobile_components(nav.HelpSearchResultsView(123, []))
    _assert_mobile_components(nav.HelpSearchResultsView(
        123, list(navigation_registry.FEATURES[:20]),
    ))


def test_search_matches_everyday_questions_not_only_exact_command_labels():
    for question, key in [
        ("How do I set up verification reminders?", "verification_reminders"),
        ("How do I send a reminder now?", "verification_reminders"),
        ("help me with invite shield", "protection"),
        ("ticket", "tickets"),
        ("how do i use live captions?", "live_captions"),
    ]:
        assert key in [feature.key for feature in nav._search_help_guides(question)]
    assert nav._search_help_guides("moonwalk spaceship broccoli") == []


def test_guide_button_explains_before_any_feature_callback(monkeypatch):
    replacements, actions = [], []

    async def fake_replace(interaction, **kwargs):
        replacements.append((interaction, kwargs))

    async def fake_route(interaction, key):
        actions.append((interaction, key))

    monkeypatch.setattr(nav, "_replace_panel", fake_replace)
    monkeypatch.setattr(nav, "_route_feature", fake_route)
    who = SimpleNamespace(user=SimpleNamespace(id=42))
    feature = navigation_registry.feature_by_key("tickets")
    assert feature is not None

    button = nav._HelpGuideButton(feature, "community")
    asyncio.run(button.callback(who))
    assert not actions
    assert replacements[-1][1]["embed"].title.endswith("Tickets")
    assert isinstance(replacements[-1][1]["view"], nav.HelpGuideView)

    open_button = nav._HelpOpenFeatureButton("tickets")
    asyncio.run(open_button.callback(who))
    assert actions == [(who, "tickets")]


def test_topic_selector_and_category_selector_do_not_send_public_messages(monkeypatch):
    seen = []

    async def fake_replace(interaction, **kwargs):
        seen.append(kwargs)

    monkeypatch.setattr(nav, "_replace_panel", fake_replace)
    interaction = SimpleNamespace(user=SimpleNamespace(id=42))

    picker = nav._HelpTopicSelect()
    picker._values = ["safety"]
    asyncio.run(picker.callback(interaction))
    assert seen[-1]["embed"].title.endswith("Members & safety")
    assert isinstance(seen[-1]["view"], nav.HelpTopicView)

    category = nav._HelpCategorySelect()
    category._values = ["tickets"]
    asyncio.run(category.callback(interaction))
    assert isinstance(seen[-1]["view"], nav.HelpCategoryView)


def test_member_guides_are_owner_scoped(monkeypatch):
    messages = []

    async def fake_private(interaction, text):
        messages.append(text)

    monkeypatch.setattr(nav, "_private", fake_private)
    other = SimpleNamespace(user=SimpleNamespace(id=99))
    view = nav.HelpGuideView(42, "tickets")
    assert asyncio.run(view.interaction_check(other)) is False
    assert len(messages) == 1
    assert "Open your own" in messages[0]


def test_legacy_help_entry_delegates_and_no_second_command_list():
    hub = (ROOT / "stoney_verify/commands_ext/public_command_hub.py").read_text()
    start = hub.index("async def open_help_entry(")
    end = hub.index("class _OwnedView(", start)
    snippet = hub[start:end]
    assert 'await _route_feature(interaction, "help")' in snippet
    assert "embed.add_field" not in snippet
    assert "DankHelpView" not in hub

    surface = (ROOT / "stoney_verify/commands_ext/public_command_surface_v2.py").read_text()
    assert 'if key == "help":' in surface
    assert "CompactHelpView(int(interaction.user.id))" in surface
    assert "class HelpGuideView" in surface
    assert "await _route_feature(interaction, self.feature_key)" in surface
