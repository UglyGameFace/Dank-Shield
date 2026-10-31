from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from stoney_verify import navigation_registry
from stoney_verify.commands_ext import public_command_surface_v2 as nav


ROOT = Path(__file__).resolve().parents[1]


def test_mobile_help_entry_is_a_short_topic_picker_not_command_dump():
    embed = nav._help_embed()
    assert len(embed.description) < 330
    assert len(embed.fields) == 0
    assert "Choose a topic" in embed.description
    assert "Find Feature" in embed.description
    assert "Quick commands" not in str(embed.to_dict())
    assert "Other shortcuts" not in str(embed.to_dict())
    assert chr(96) + "/dank" not in str(embed.to_dict())

    view = nav.CompactHelpView(123)
    assert view.timeout == nav.PRIVATE_MENU_TTL_SECONDS
    assert len(view.children) == 5
    assert any(getattr(item, "custom_id", "") == "dank:help:topics:v2"
               for item in view.children)
    assert {getattr(item, "label", "") for item in view.children if getattr(item, "label", "")} == {
        "Find Feature", "Directory", "Home", "Close",
    }


def test_every_help_topic_uses_real_registry_destinations_and_mobile_limits():
    assert 4 <= len(nav._HELP_TOPICS) <= 8
    keys = [item[0] for item in nav._HELP_TOPICS]
    assert len(keys) == len(set(keys))
    for key, label, summary, feature_keys in nav._HELP_TOPICS:
        assert label and summary
        assert 1 <= len(feature_keys) <= 3
        assert len(feature_keys) == len(set(feature_keys))
        embed = nav._help_topic_embed(key)
        assert embed.title.endswith(label)
        assert len(embed.fields) == len(feature_keys)
        view = nav.HelpTopicView(123, key)
        ids = [getattr(item, "custom_id", "") for item in view.children]
        assert len(ids) == len(set(ids))
        assert len(view.children) <= 9
        assert max(getattr(item, "row", 0) or 0 for item in view.children) <= 4
        assert any(getattr(item, "custom_id", "") == "dank:help:topics:v2" for item in view.children)
        for fkey, field in zip(feature_keys, embed.fields):
            feature = navigation_registry.feature_by_key(fkey)
            assert feature is not None
            assert feature.key in nav._FEATURE_ROUTE_KEYS
            assert feature.label in field.name
            assert feature.description[:220] == field.value
            assert ("Staff" in field.name) == feature.manager_only
            assert any(getattr(item, "custom_id", "") == f"dank:help:feature:{fkey}:v2"
                       for item in view.children)


def test_help_feature_button_uses_canonical_owner_not_duplicate_handlers(monkeypatch):
    feature = navigation_registry.feature_by_key("verification_reminders")
    assert feature is not None
    seen = []

    async def fake_route(interaction, key):
        seen.append((interaction, key))

    monkeypatch.setattr(nav, "_route_feature", fake_route)
    interaction = object()
    button = nav._HelpFeatureButton(feature)
    asyncio.run(button.callback(interaction))
    assert seen == [(interaction, "verification_reminders")]


def test_help_topics_switch_in_place_without_posting_a_new_public_message(monkeypatch):
    seen = []

    async def fake_replace(interaction, **kwargs):
        seen.append((interaction, kwargs))

    monkeypatch.setattr(nav, "_replace_panel", fake_replace)
    interaction = SimpleNamespace(user=SimpleNamespace(id=42))
    selector = nav._HelpTopicSelect()
    selector._values = ["safety"]
    asyncio.run(selector.callback(interaction))
    assert len(seen) == 1
    assert seen[0][1]["embed"].title == "❓ Members & safety"
    assert isinstance(seen[0][1]["view"], nav.HelpTopicView)
    assert seen[0][1]["view"].owner_id == 42


def test_help_view_rejects_other_users_and_does_not_route(monkeypatch):
    replies = []

    async def fake_private(interaction, text):
        replies.append(text)

    monkeypatch.setattr(nav, "_private", fake_private)
    view = nav.HelpTopicView(42, "community")
    unauthorized = SimpleNamespace(user=SimpleNamespace(id=43))
    assert asyncio.run(view.interaction_check(unauthorized)) is False
    assert len(replies) == 1
    assert "Open your own" in replies[0]


def test_legacy_help_entry_delegates_to_final_registry_owned_help():
    hub = (ROOT / "stoney_verify/commands_ext/public_command_hub.py").read_text()
    start = hub.index("async def open_help_entry(")
    end = hub.index("class _OwnedView(", start)
    snippet = hub[start:end]
    assert 'await _route_feature(interaction, "help")' in snippet
    assert "embed.add_field" not in snippet
    assert "DankHelpView" not in hub

    current = (ROOT / "stoney_verify/commands_ext/public_command_surface_v2.py").read_text()
    assert 'if key == "help":' in current
    assert "CompactHelpView(int(interaction.user.id))" in current
