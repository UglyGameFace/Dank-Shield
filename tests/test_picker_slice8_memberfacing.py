"""Slice 8 picker migration (member-facing) — routing parity, paging, class C.

Felix's standing rule: every picker uses the house framework
(``stoney_verify/ui/picker.py``). This slice migrated:

- ``ProfileMemberListSelect`` + ``ProfileCategorySelectView`` in
  ``public_self_roles_group.py`` (paged member picker; class C
  self-role category multi-picker on the legacy custom_id);
- ``ShareRouterTargetLandingView.content_choice`` +
  ``DownloadExpirySelect`` in ``public_share_router.py``;
- ``_SoundSelect`` in ``public_soundboard_studio.py``;
- ``VisibleStatsSelect`` + ``StatLabelSelect`` in
  ``public_server_stats.py``;
- ``TokeHostPickerView.choose`` in ``toke_live_ui.py`` (class C,
  DankUserSelect wrapper);
- ``_TrustedAdsSectionSelect`` in ``trusted_advert_ui.py``.

These tests prove, for every migrated surface:

- the raw select classes / decorator selects are gone (AST scan);
- driving a picker routes to the same destination the old select
  callback used (same saves, modals, follow-ups);
- lists longer than Discord's 25-option limit stay fully reachable
  through chunk_choices paging;
- class C surfaces accept a press from a user other than the opener
  and rely on in-handler validation, including after a simulated
  restart (a fresh view instance with the same custom_id).
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import discord
import pytest

from stoney_verify.commands_ext import public_self_roles_group as profiles
from stoney_verify.commands_ext import public_server_stats as stats
from stoney_verify.commands_ext import public_share_router as share
from stoney_verify.commands_ext import public_soundboard_studio as sound
from stoney_verify.commands_ext import trusted_advert_ui as ads
from stoney_verify import toke_live_ui as toke
from stoney_verify.ui.picker import (
    DankMultiPickerView, DankPickerView, DankUserSelect,
)

ROOT = Path(__file__).resolve().parents[1]
OWNER_ID = 424242
GUILD_ID = 777


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self) -> None:
        self.done = False
        self.sent: list[tuple[tuple, dict]] = []
        self.edits: list[dict] = []
        self.modals: list[Any] = []
        self.deferred = False

    def is_done(self) -> bool:
        return self.done

    async def send_message(self, *args: Any, **kwargs: Any) -> None:
        self.sent.append((args, kwargs))
        self.done = True

    async def edit_message(self, *args: Any, **kwargs: Any) -> None:
        self.edits.append(kwargs)
        self.done = True

    async def defer(self, *args: Any, **kwargs: Any) -> None:
        self.deferred = True
        self.done = True

    async def send_modal(self, modal: Any) -> None:
        self.modals.append(modal)
        self.done = True


class FakeFollowup:
    def __init__(self) -> None:
        self.sent: list[tuple[tuple, dict]] = []

    async def send(self, *args: Any, **kwargs: Any) -> None:
        self.sent.append((args, kwargs))


class FakeInteraction:
    def __init__(
        self, *, guild: Any = None, user: Any = None,
        guild_id: Any = None, data: Any = None,
    ) -> None:
        self.guild = guild
        self.user = user if user is not None else SimpleNamespace(id=OWNER_ID)
        if guild_id is not None:
            self.guild_id = guild_id
        else:
            self.guild_id = getattr(guild, "id", GUILD_ID)
        self.data = data or {}
        self.response = FakeResponse()
        self.followup = FakeFollowup()
        self.message = SimpleNamespace(embeds=[discord.Embed(title="Host Panel")])
        self.client = SimpleNamespace()
        self.original_edits: list[dict] = []

    async def edit_original_response(self, *args: Any, **kwargs: Any) -> None:
        self.original_edits.append(kwargs)


def make_member(user_id: int, *, bot: bool = False) -> Any:
    member = object.__new__(discord.Member)
    member._user = SimpleNamespace(
        id=user_id, name=f"user{user_id}", global_name=None,
        bot=bot, discriminator="0", avatar=None,
    )
    member.nick = None
    member.joined_at = None
    member._roles = []
    return member


def fake_member_ns(index: int) -> Any:
    return SimpleNamespace(
        id=1000 + index, bot=False,
        display_name=f"Member {index}", name=f"member{index}",
        joined_at=None,
    )


def sent_texts(interaction: FakeInteraction) -> list[str]:
    texts = [str(args[0]) for args, _ in interaction.response.sent if args]
    texts += [str(args[0]) for args, _ in interaction.followup.sent if args]
    return texts


# ---------------------------------------------------------------------------
# Source scan: the migrated raw selects are gone from all six files
# ---------------------------------------------------------------------------

SIX_FILES = [
    "stoney_verify/commands_ext/public_self_roles_group.py",
    "stoney_verify/commands_ext/public_share_router.py",
    "stoney_verify/commands_ext/public_soundboard_studio.py",
    "stoney_verify/commands_ext/public_server_stats.py",
    "stoney_verify/toke_live_ui.py",
    "stoney_verify/commands_ext/trusted_advert_ui.py",
]


def test_no_raw_selects_remain_in_the_six_files() -> None:
    for rel in SIX_FILES:
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for base in node.bases:
                    text = ast.unparse(base)
                    assert "discord.ui.Select" not in text, (rel, node.name)
                    assert "discord.ui.UserSelect" not in text, (rel, node.name)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    assert "discord.ui.select" not in ast.unparse(decorator), (rel, node.name)
            if isinstance(node, ast.Call):
                assert ast.unparse(node.func) not in {
                    "discord.ui.Select", "discord.ui.UserSelect",
                }, (rel, node.lineno)


def test_migrated_classes_use_the_house_framework() -> None:
    assert issubclass(profiles.ProfileMemberListSelect, DankPickerView)
    assert issubclass(profiles.ProfileCategorySelectView, DankMultiPickerView)
    assert issubclass(share.DownloadExpirySelect, DankPickerView)
    assert issubclass(share.ShareRouterContentFilterPickerView, DankPickerView)
    assert issubclass(sound._SoundSelect, DankPickerView)
    assert issubclass(stats.VisibleStatsSelect, DankMultiPickerView)
    assert issubclass(stats.StatLabelSelect, DankPickerView)
    assert issubclass(ads._TrustedAdsSectionSelect, DankPickerView)


# ---------------------------------------------------------------------------
# 1. ProfileMemberListSelect — routing parity + >25 paging
# ---------------------------------------------------------------------------


def test_member_picker_pages_every_member_past_25() -> None:
    members = [fake_member_ns(i) for i in range(30)]
    picker = profiles.ProfileMemberListSelect(members)
    assert picker.pages == 2
    assert picker.custom_id == "dank:profile:v1:member_list_select:0"
    page_two = picker._for_page(1)
    assert page_two.custom_id == "dank:profile:v1:member_list_select:1"
    reachable = {choice.value for choice in picker.choices}
    reachable |= {choice.value for choice in page_two.choices}
    assert reachable == {str(1000 + i) for i in range(30)}
    # Labels/descriptions match the raw select's option rendering.
    first = picker.choices[0]
    assert first.label == profiles._profile_member_label(members[0])
    assert first.description == profiles._profile_member_description(members[0])


def test_member_picker_single_page_keeps_the_legacy_custom_id() -> None:
    picker = profiles.ProfileMemberListSelect([fake_member_ns(i) for i in range(10)])
    assert picker.pages == 1
    assert picker.custom_id == "dank:profile:v1:member_list_select"


def test_member_picker_pick_sends_the_privacy_aware_profile(monkeypatch) -> None:
    from stoney_verify.commands_ext import public_profile_cards as cards

    seen: list[tuple[Any, Any]] = []

    async def fake_send(interaction: Any, member: Any) -> None:
        seen.append((interaction, member))

    monkeypatch.setattr(cards, "send_privacy_aware_profile", fake_send)
    target = make_member(1005)
    guild = SimpleNamespace(id=GUILD_ID, get_member=lambda uid: target if uid == 1005 else None)
    picker = profiles.ProfileMemberListSelect([fake_member_ns(5)])
    interaction = FakeInteraction(guild=guild)
    run(picker.handle_pick(interaction, "1005"))
    assert seen == [(interaction, target)]


def test_member_picker_pick_for_a_gone_member_keeps_the_old_denial() -> None:
    guild = SimpleNamespace(id=GUILD_ID, get_member=lambda uid: None)
    picker = profiles.ProfileMemberListSelect([fake_member_ns(5)])
    interaction = FakeInteraction(guild=guild)
    run(picker.handle_pick(interaction, "1005"))
    assert any("That human member is no longer available." in text for text in sent_texts(interaction))


def test_member_list_host_view_uses_an_opener_not_a_select() -> None:
    guild = SimpleNamespace(
        id=GUILD_ID, members=[make_member(2000 + i) for i in range(30)],
    )
    view = profiles.ProfileMemberListView(guild, page=1)
    assert view.total == 30 and view.page == 1
    assert not any(isinstance(item, discord.ui.Select) for item in view.children)
    openers = [
        item for item in view.children
        if getattr(item, "custom_id", "") == "dank:profile:v1:member_list_open"
    ]
    assert len(openers) == 1
    interaction = FakeInteraction(guild=guild)
    run(openers[0].callback(interaction))
    picker = interaction.response.edits[-1]["view"]
    assert isinstance(picker, profiles.ProfileMemberListSelect)
    assert picker.page == 1  # opens on the host's current page


# ---------------------------------------------------------------------------
# 2. ProfileCategorySelectView — class C, legacy id, >25 paging
# ---------------------------------------------------------------------------


def _category_roles(count: int) -> list[Any]:
    return [
        SimpleNamespace(id=2000 + i, name=f"Interest: thing{i}", mention=f"<@&{2000 + i}>")
        for i in range(count)
    ]


def test_category_picker_pages_every_role_and_keeps_legacy_id(monkeypatch) -> None:
    roles = _category_roles(30)
    monkeypatch.setattr(profiles, "_category_roles", lambda guild, key: roles)
    member = SimpleNamespace(id=42, roles=[roles[3], roles[27]])
    picker = profiles.ProfileCategorySelectView(SimpleNamespace(id=GUILD_ID), member, "interests")
    assert picker.pages == 2
    assert picker.custom_id == "dank:profile:v1:select:interests"
    assert [choice.value for choice in picker.choices if choice.default] == ["2003"]
    select_item = picker.children[0]
    assert select_item.min_values == 0 and select_item.max_values == 25
    page_two = picker._for_page(1)
    assert page_two.custom_id == "dank:profile:v1:select:interests:p1"
    assert [choice.value for choice in page_two.choices if choice.default] == ["2027"]
    reachable = {choice.value for choice in picker.choices}
    reachable |= {choice.value for choice in page_two.choices}
    assert reachable == {str(2000 + i) for i in range(30)}


def test_category_picker_single_page_keeps_the_exact_legacy_id(monkeypatch) -> None:
    roles = _category_roles(3)
    monkeypatch.setattr(profiles, "_category_roles", lambda guild, key: roles)
    member = SimpleNamespace(id=42, roles=[])
    picker = profiles.ProfileCategorySelectView(SimpleNamespace(id=GUILD_ID), member, "pronouns")
    assert picker.pages == 1
    assert picker.custom_id == "dank:profile:v1:select:pronouns"
    assert picker.allow_anyone is True


@pytest.fixture
def category_handler(monkeypatch):
    """Drive _handle_profile_interaction with faked role plumbing."""
    from stoney_verify.commands_ext import public_profile_cards as cards

    roles = _category_roles(30)
    monkeypatch.setattr(profiles, "_category_roles", lambda guild, key: roles)
    monkeypatch.setattr(profiles, "_can_manage", lambda role, guild: (True, ""))
    held: dict[int, list[Any]] = {}
    monkeypatch.setattr(
        discord.Member, "roles",
        property(lambda self: list(held.get(id(self), []))),
    )
    calls: dict[str, list[list[int]]] = {"add": [], "remove": []}

    async def fake_add(self: Any, *picked: Any, reason: Any = None) -> None:
        calls["add"].append([int(role.id) for role in picked])

    async def fake_remove(self: Any, *picked: Any, reason: Any = None) -> None:
        calls["remove"].append([int(role.id) for role in picked])

    monkeypatch.setattr(discord.Member, "add_roles", fake_add)
    monkeypatch.setattr(discord.Member, "remove_roles", fake_remove)

    async def fake_invalidate(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(cards, "invalidate_member_live_cards", fake_invalidate)
    return roles, held, calls


def _profile_press(user: Any, custom_id: str, values: list[str]) -> FakeInteraction:
    return FakeInteraction(
        guild=SimpleNamespace(id=GUILD_ID), user=user,
        data={"custom_id": custom_id, "values": values},
    )


def test_category_paged_press_after_restart_applies_only_that_page(category_handler) -> None:
    roles, held, calls = category_handler
    # The picker was opened for member 42; after a "restart" a fresh
    # view instance carries the same paged custom_id, and a DIFFERENT
    # member (43) presses it. The press must be accepted and decided
    # by the handler's own per-member validation (the presser is the
    # member whose roles change).
    opener = SimpleNamespace(id=42, roles=[])
    fresh = profiles.ProfileCategorySelectView(
        SimpleNamespace(id=GUILD_ID), opener, "interests", page=1,
    )
    assert fresh.custom_id == "dank:profile:v1:select:interests:p1"

    presser = make_member(43)
    held[id(presser)] = [roles[0]]  # holds a page-0 role
    interaction = _profile_press(presser, fresh.custom_id, ["2027"])
    assert run(profiles._handle_profile_interaction(interaction)) is True
    assert calls["add"] == [[2027]]
    # Page-scoped diff: the page-0 role the presser holds is NOT
    # removed by a page-1 press.
    assert calls["remove"] == []
    assert any("Added: <@&2027>" in text for text in sent_texts(interaction))


def test_category_legacy_press_keeps_the_whole_category_diff(category_handler) -> None:
    roles, held, calls = category_handler
    presser = make_member(44)
    held[id(presser)] = [roles[0]]
    interaction = _profile_press(
        presser, "dank:profile:v1:select:interests", ["2001"],
    )
    assert run(profiles._handle_profile_interaction(interaction)) is True
    assert calls["add"] == [[2001]]
    assert calls["remove"] == [[2000]]


# ---------------------------------------------------------------------------
# 3a. Share Router content filter picker — routing parity
# ---------------------------------------------------------------------------


def _landing(content_filter: str = "videos", delete_source: bool = False) -> Any:
    source = SimpleNamespace(id=55, name="clips", guild=SimpleNamespace(id=GUILD_ID))
    return share.ShareRouterTargetLandingView(
        OWNER_ID, source=source, browser=SimpleNamespace(),
        delete_source=delete_source, content_filter=content_filter,
    )


def test_content_filter_picker_choices_and_defaults_match_the_old_select() -> None:
    picker = share.ShareRouterContentFilterPickerView(_landing("videos"))
    assert {choice.value for choice in picker.choices} == {
        "videos", "images", "links", "everything",
    }
    assert [choice.value for choice in picker.choices if choice.default] == ["videos"]
    assert picker.custom_id == "dank:share_router:content_filter:v1"


def test_content_filter_pick_reopens_the_target_browser(monkeypatch) -> None:
    async def allowed(interaction: Any) -> bool:
        return True

    opened: list[dict[str, Any]] = []

    async def fake_open(interaction: Any, source: Any, **kwargs: Any) -> None:
        opened.append({"source": source, **kwargs})

    monkeypatch.setattr(share, "_require_manage", allowed)
    monkeypatch.setattr(share, "_open_target_browser", fake_open)
    landing = _landing("videos", delete_source=False)
    picker = share.ShareRouterContentFilterPickerView(landing)
    interaction = FakeInteraction(guild_id=GUILD_ID)
    run(picker.handle_pick(interaction, "images"))
    assert opened == [{
        "source": landing.source, "delete_source": False, "content_filter": "images",
    }]


def test_target_landing_view_uses_an_opener_not_a_select() -> None:
    landing = _landing("everything", delete_source=False)
    assert not any(isinstance(item, discord.ui.Select) for item in landing.children)
    assert landing.back.label == "Back to Existing Channels"
    openers = [item for item in landing.children if getattr(item, "label", "") == "Content Filter"]
    assert len(openers) == 1
    interaction = FakeInteraction(guild_id=GUILD_ID)
    run(openers[0].callback(interaction))
    assert isinstance(
        interaction.response.edits[-1]["view"],
        share.ShareRouterContentFilterPickerView,
    )


# ---------------------------------------------------------------------------
# 3b. DownloadExpirySelect — routing parity (same saved values)
# ---------------------------------------------------------------------------


def test_download_expiry_choices_and_defaults_match_the_old_select() -> None:
    picker = share.DownloadExpirySelect(86400, owner_id=OWNER_ID)
    assert [(choice.label, choice.value) for choice in picker.choices] == [
        ("1 hour", "3600"), ("24 hours", "86400"),
        ("7 days (recommended)", "604800"), ("30 days (maximum)", "2592000"),
    ]
    assert [choice.value for choice in picker.choices if choice.default] == ["86400"]


def test_download_expiry_pick_saves_the_same_seconds(monkeypatch) -> None:
    async def allowed(interaction: Any) -> bool:
        return True

    saved: list[tuple[int, int]] = []

    async def fake_save(guild_id: int, seconds: int) -> None:
        saved.append((guild_id, seconds))

    async def fake_embed(guild: Any) -> discord.Embed:
        return discord.Embed(title="Private Video Downloads")

    monkeypatch.setattr(share, "_require_manage", allowed)
    monkeypatch.setattr(share, "save_guild_download_expiry_seconds", fake_save)
    monkeypatch.setattr(share, "_download_settings_embed", fake_embed)
    picker = share.DownloadExpirySelect(604800, owner_id=OWNER_ID)
    interaction = FakeInteraction(
        guild=SimpleNamespace(id=GUILD_ID), user=SimpleNamespace(id=OWNER_ID),
    )
    run(picker.handle_pick(interaction, "3600"))
    assert saved == [(GUILD_ID, 3600)]
    redraw = interaction.original_edits[-1]["view"]
    assert isinstance(redraw, share.ShareRouterDownloadSettingsView)
    assert redraw.current == 3600


def test_download_settings_view_uses_an_opener_not_a_select() -> None:
    view = share.ShareRouterDownloadSettingsView(OWNER_ID, 86400)
    assert not any(isinstance(item, discord.ui.Select) for item in view.children)
    openers = [item for item in view.children if getattr(item, "label", "") == "Link Expiration"]
    assert len(openers) == 1


# ---------------------------------------------------------------------------
# 4. Soundboard _SoundSelect — routing parity + host subset pager
# ---------------------------------------------------------------------------


def _fake_sounds(count: int) -> list[Any]:
    return [
        SimpleNamespace(id=3000 + i, name=f"sound{i:02}", volume=0.5, emoji=None)
        for i in range(count)
    ]


def test_sound_picker_pick_opens_the_same_detail_view() -> None:
    sounds = _fake_sounds(20)

    async def fetch(sound_id: int) -> Any:
        return next(item for item in sounds if item.id == sound_id)

    guild = SimpleNamespace(id=99, fetch_soundboard_sound=fetch)
    picker = sound._SoundSelect(sounds, owner_id=7, guild_id=99, all_sounds=sounds, page=0)
    assert len(picker.choices) == 20
    assert picker.custom_id == "dank:soundboard:browse_select:v1"
    interaction = FakeInteraction(
        guild=guild, user=SimpleNamespace(id=7),
    )
    run(picker.handle_pick(interaction, "3003"))
    sent = interaction.response.sent[-1]
    assert isinstance(sent[1]["view"], sound.SoundDetailView)
    assert sent[1]["view"].sound_id == 3003
    assert "sound03" in str(sent[1]["embed"].title)


def test_sound_picker_stale_value_keeps_the_old_notice() -> None:
    picker = sound._SoundSelect(_fake_sounds(3), owner_id=7, guild_id=99)
    interaction = FakeInteraction(guild=SimpleNamespace(id=99), user=SimpleNamespace(id=7))
    run(picker.handle_pick(interaction, "999999"))
    assert any(
        "That sound is no longer on this page." in text for text in sent_texts(interaction)
    )


def test_soundboard_browse_view_uses_an_opener_and_pages_subsets() -> None:
    sounds = _fake_sounds(45)
    first = sound.SoundboardBrowseView(7, 99, sounds, page=0)
    assert not any(isinstance(item, discord.ui.Select) for item in first.children)
    openers = [
        item for item in first.children
        if getattr(item, "custom_id", "") == "dank:soundboard:inspect_open:v1"
    ]
    assert len(openers) == 1
    last = sound.SoundboardBrowseView(7, 99, sounds, page=2)
    subset = last.sounds[last.page * 20: last.page * 20 + 20]
    assert len(subset) == 5  # every sound stays reachable via the host pager
    interaction = FakeInteraction(guild=SimpleNamespace(id=99), user=SimpleNamespace(id=7))
    run(openers[0].callback(interaction))
    picker = interaction.response.edits[-1]["view"]
    assert isinstance(picker, sound._SoundSelect)
    assert {choice.value for choice in picker.choices} == {
        str(item.id) for item in sounds[:20]
    }


# ---------------------------------------------------------------------------
# 5. Server Stats pickers — routing parity (same saved config)
# ---------------------------------------------------------------------------


def test_visible_stats_picker_choices_cover_every_counter() -> None:
    from stoney_verify import security_stats

    picker = stats.VisibleStatsSelect(owner_id=1, cfg={})
    assert {choice.value for choice in picker.choices} == set(
        security_stats.DEFAULT_SECURITY_STATS_VISIBLE_KEYS
    )
    assert picker.custom_id == "dank_server_stats:visible"
    assert picker.min_values == 1
    assert picker.max_values == len(picker.choices)
    by_key = {choice.value: choice for choice in picker.choices}
    assert "GUILD_CREATE member_count" in str(by_key["members"].description)


def test_visible_stats_pick_saves_the_same_visible_keys(monkeypatch) -> None:
    async def allowed(interaction: Any) -> bool:
        return True

    async def fake_cfg(guild_id: int, *, refresh: bool = False) -> dict:
        return {}

    saved: list[tuple[int, dict]] = []

    async def fake_save(guild_id: int, updates: dict) -> None:
        saved.append((guild_id, dict(updates)))

    rendered: list[Any] = []

    async def fake_render(interaction: Any, *, content: Any = None) -> None:
        rendered.append(content)

    monkeypatch.setattr(stats, "_require_setup_permission", allowed)
    monkeypatch.setattr(stats, "get_guild_config", fake_cfg)
    monkeypatch.setattr(stats, "_save_preferences", fake_save)
    monkeypatch.setattr(stats, "_render_center", fake_render)
    monkeypatch.setattr(
        stats, "security_stat_metric_capability",
        lambda key, guild=None: {
            "available": True, "missing_intents": [], "missing_permissions": [],
        },
    )
    picker = stats.VisibleStatsSelect(owner_id=1, cfg={})
    interaction = FakeInteraction(
        guild=SimpleNamespace(id=GUILD_ID), user=SimpleNamespace(id=1),
    )
    run(picker.handle_pick(interaction, ["members", "status"]))
    assert saved == [(
        GUILD_ID,
        {stats.SECURITY_STATS_VISIBLE_KEYS_KEY: ["status", "members"]},
    )]
    assert rendered and "Showing **2**" in str(rendered[-1])


def test_visible_stats_pick_denies_a_non_owner_like_the_old_select(monkeypatch) -> None:
    saved: list[Any] = []

    async def fake_save(guild_id: int, updates: dict) -> None:
        saved.append(updates)

    monkeypatch.setattr(stats, "_save_preferences", fake_save)
    picker = stats.VisibleStatsSelect(owner_id=1, cfg={})
    interaction = FakeInteraction(
        guild=SimpleNamespace(id=GUILD_ID), user=SimpleNamespace(id=2),
    )
    run(picker.handle_pick(interaction, ["members"]))
    assert saved == []
    assert any(
        "Open your own Server Stats panel." in text for text in sent_texts(interaction)
    )


def test_stat_label_pick_opens_the_same_modal(monkeypatch) -> None:
    async def allowed(interaction: Any) -> bool:
        return True

    async def fake_cfg(guild_id: int, *, refresh: bool = False) -> dict:
        return {}

    monkeypatch.setattr(stats, "_require_setup_permission", allowed)
    monkeypatch.setattr(stats, "get_guild_config", fake_cfg)
    picker = stats.StatLabelSelect(owner_id=1, cfg={})
    assert picker.custom_id == "dank_server_stats:label"
    interaction = FakeInteraction(
        guild=SimpleNamespace(id=GUILD_ID), user=SimpleNamespace(id=1),
    )
    run(picker.handle_pick(interaction, "members"))
    assert len(interaction.response.modals) == 1
    modal = interaction.response.modals[0]
    assert isinstance(modal, stats.StatLabelModal)
    assert modal.key == "members" and modal.owner_id == 1


def test_server_stats_view_uses_openers_not_selects() -> None:
    view = stats.ServerStatsView(owner_id=1, cfg={})
    assert not any(isinstance(item, discord.ui.Select) for item in view.children)
    ids = {getattr(item, "custom_id", "") for item in view.children}
    assert {"dank_server_stats:visible_open", "dank_server_stats:label_open"} <= ids
    opener = next(
        item for item in view.children
        if getattr(item, "custom_id", "") == "dank_server_stats:visible_open"
    )
    interaction = FakeInteraction(
        guild=SimpleNamespace(id=GUILD_ID), user=SimpleNamespace(id=1),
    )
    run(opener.callback(interaction))
    assert isinstance(
        interaction.response.edits[-1]["view"], stats.VisibleStatsSelect,
    )


# ---------------------------------------------------------------------------
# 6. Toke host picker — class C (DankUserSelect, in-handler authority)
# ---------------------------------------------------------------------------


def test_toke_host_picker_is_a_house_user_select_with_stable_id() -> None:
    picker = toke.TokeHostPickerView(actor_id=111, guild_id=123, message_id=456)
    assert len(picker.children) == 1
    select = picker.children[0]
    assert isinstance(select, DankUserSelect)
    assert isinstance(select, discord.ui.UserSelect)
    assert select.allow_anyone is True
    assert select.custom_id == "dank:toke:host_picker:v1"


@pytest.fixture
def toke_session(monkeypatch):
    session = {
        "active": True, "starter_id": 111, "starter_role_id": 15,
    }

    async def fake_get(guild_id: int, message_id: int) -> dict:
        return dict(session)

    candidate = SimpleNamespace(
        id=222, bot=False, roles=[SimpleNamespace(id=15)],
    )

    async def fake_resolve(guild: Any, user_id: int) -> Any:
        return candidate if user_id == 222 else None

    monkeypatch.setattr(toke.toke_sessions, "get", fake_get)
    monkeypatch.setattr(toke, "_resolve_toke_handoff_target", fake_resolve)
    return session


def _toke_press(user_id: int, *, manage_guild: bool = False) -> FakeInteraction:
    return FakeInteraction(
        guild=SimpleNamespace(id=123),
        user=SimpleNamespace(
            id=user_id,
            guild_permissions=SimpleNamespace(manage_guild=manage_guild),
        ),
    )


def test_toke_host_pick_by_the_opener_reaches_confirmation(toke_session) -> None:
    picker = toke.TokeHostPickerView(actor_id=111, guild_id=123, message_id=456)
    interaction = _toke_press(111)
    run(picker.choose(interaction, SimpleNamespace(id=222)))
    confirm = interaction.original_edits[-1]["view"]
    assert isinstance(confirm, toke.TokeHostConfirmView)
    assert confirm.new_starter_id == 222


def test_toke_host_pick_after_restart_by_a_manager_not_the_opener(toke_session) -> None:
    # Simulated restart: a fresh view instance with the same stable
    # custom_id. The presser is neither the opener (111) nor the
    # starter — a Manage Server member — and the in-handler
    # starter/manager check is the authority that lets them through.
    fresh = toke.TokeHostPickerView(actor_id=111, guild_id=123, message_id=456)
    assert fresh.children[0].custom_id == "dank:toke:host_picker:v1"
    assert run(fresh.interaction_check(_toke_press(999, manage_guild=True))) is True
    interaction = _toke_press(999, manage_guild=True)
    run(fresh.choose(interaction, SimpleNamespace(id=222)))
    confirm = interaction.original_edits[-1]["view"]
    assert isinstance(confirm, toke.TokeHostConfirmView)


def test_toke_host_pick_by_an_unrelated_member_is_denied_in_handler(toke_session) -> None:
    picker = toke.TokeHostPickerView(actor_id=111, guild_id=123, message_id=456)
    interaction = _toke_press(555)
    run(picker.choose(interaction, SimpleNamespace(id=222)))
    assert interaction.original_edits[-1]["content"] == "You can no longer manage this sesh."
    assert "view" not in interaction.original_edits[-1] or (
        interaction.original_edits[-1]["view"] is None
    )


# ---------------------------------------------------------------------------
# 7. Trusted Ads section picker — routing parity
# ---------------------------------------------------------------------------


def test_trusted_ads_picker_choices_and_legacy_id() -> None:
    picker = ads._TrustedAdsSectionSelect(33, 456, "approval")
    assert [(choice.label, choice.value) for choice in picker.choices] == [
        ("Bot Approvals", "approval"), ("Cleanup & Previews", "retention"),
    ]
    assert [choice.value for choice in picker.choices if choice.default] == ["approval"]
    assert picker.custom_id == "dank_protection:trusted_ads:section:v2"


def test_trusted_ads_pick_routes_to_the_same_section(monkeypatch) -> None:
    events: list[tuple[Any, dict]] = []

    async def show(interaction: Any, owner: Any, **kwargs: Any) -> None:
        events.append((owner, kwargs))

    monkeypatch.setattr(ads, "_staff", lambda *args: True)
    monkeypatch.setattr(ads, "show_trusted_ads", show)
    picker = ads._TrustedAdsSectionSelect(33, 456, "approval")
    interaction = FakeInteraction(user=SimpleNamespace(id=33))
    run(picker.handle_pick(interaction, "retention"))
    assert events == [(33, {"channel_id": 456, "section": "retention"})]

    # The old raw-select callback shape still works (compat entry).
    picker._values = ["retention"]
    run(picker.callback(SimpleNamespace(user=SimpleNamespace(id=33))))
    assert events[-1] == (33, {"channel_id": 456, "section": "retention"})


def test_trusted_ads_pick_without_staff_keeps_the_old_denial(monkeypatch) -> None:
    events: list[Any] = []

    async def show(interaction: Any, owner: Any, **kwargs: Any) -> None:
        events.append(owner)

    monkeypatch.setattr(ads, "show_trusted_ads", show)
    picker = ads._TrustedAdsSectionSelect(33, 456, "approval")
    interaction = FakeInteraction(guild=None, user=SimpleNamespace(id=33))
    run(picker.handle_pick(interaction, "retention"))
    assert events == []
    assert any(
        "Manage Server permission required." in text for text in sent_texts(interaction)
    )


def test_trusted_ads_view_uses_an_opener_not_a_select() -> None:
    guild = SimpleNamespace(get_channel=lambda _id: None, me=None, default_role=None)
    view = ads.TrustedAdsView(10, guild, 123, rules=[], section="approval")
    assert not any(isinstance(item, discord.ui.Select) for item in view.children)
    openers = [
        item for item in view.children
        if getattr(item, "custom_id", "") == "dank_protection:trusted_ads:section:open:v1"
    ]
    assert len(openers) == 1
    interaction = FakeInteraction(
        guild=guild,
        user=SimpleNamespace(
            id=10, guild_permissions=SimpleNamespace(manage_guild=True, administrator=False),
        ),
    )
    run(openers[0].callback(interaction))
    assert isinstance(
        interaction.response.edits[-1]["view"], ads._TrustedAdsSectionSelect,
    )
