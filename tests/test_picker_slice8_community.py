"""Slice 8 picker migration (Community & Pings / Community Tools) — routing parity + source scan.

Felix's standing rule: every picker uses the house framework
(``stoney_verify/ui/picker.py``). Slice 8 migrated:

- ``CommunityOptionEditorSectionSelect`` and ``CommunityPingsSectionSelect``
  in ``public_community_pings.py`` — raw ``discord.ui.Select`` section
  pickers inside setup panels, now ``DankPickerView`` subclasses reached
  through opener buttons on their host views (the Protection Center /
  Member Setup section-picker idiom), keeping their original custom_ids;
- ``InfoMemberSelect`` in ``public_community_tools.py`` — a raw
  ``discord.ui.UserSelect``, now the framework's ``DankUserSelect``
  wrapper keeping its class name, no-arg construction, and stable
  custom_id.

These tests prove, for every migrated surface:
- the raw select classes are gone and the replacements are
  house-framework classes with the original stable custom_ids;
- driving a picker routes to the same section render / member-info
  render the old select callback produced, behind the same gates
  (staff authorization for the pings pickers, the host InfoView owner
  check for the member picker);
- ``InfoMemberSelect`` gained no owner lock it did not have: the
  wrapper is opened with ``author_id=0`` and a press by a non-opener
  is handled exactly as before — the host view's owner check denies
  it with the host view's own message.
"""
from __future__ import annotations

import ast
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import discord
import pytest

from stoney_verify.commands_ext import public_community_pings as pings
from stoney_verify.commands_ext import public_community_tools as tools
from stoney_verify.ui.picker import DankPickerView, DankUserSelect

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
        self.sent: list[dict] = []
        self.edits: list[dict] = []
        self.deferred = False

    def is_done(self) -> bool:
        return self.done

    async def send_message(self, *args: Any, **kwargs: Any) -> None:
        if args:
            kwargs.setdefault("content", args[0])
        self.sent.append(kwargs)
        self.done = True

    async def edit_message(self, *args: Any, **kwargs: Any) -> None:
        self.edits.append(kwargs)
        self.done = True

    async def defer(self, *args: Any, **kwargs: Any) -> None:
        self.deferred = True
        self.done = True


class FakeFollowup:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, *args: Any, **kwargs: Any) -> None:
        self.sent.append(kwargs)


class FakeInteraction:
    def __init__(self, *, guild: Any = None, user: Any = None) -> None:
        self.guild = guild
        self.guild_id = getattr(guild, "id", GUILD_ID) if guild is not None else GUILD_ID
        self.user = user if user is not None else SimpleNamespace(id=OWNER_ID)
        self.response = FakeResponse()
        self.followup = FakeFollowup()
        self.message = SimpleNamespace(
            id=31337,
            embeds=[discord.Embed(title="Host Panel")],
        )
        self.channel = SimpleNamespace(id=555)
        self.original_edits: list[dict] = []

    async def edit_original_response(self, *args: Any, **kwargs: Any) -> None:
        self.original_edits.append(kwargs)


def fake_guild(**kwargs: Any) -> Any:
    base: dict[str, Any] = {"id": GUILD_ID}
    base.update(kwargs)
    return SimpleNamespace(**base)


def _opener(view: Any, custom_id: str) -> Any:
    return next(
        item for item in getattr(view, "children", [])
        if getattr(item, "custom_id", "") == custom_id
    )


def _select_child(picker: DankPickerView) -> Any:
    return next(item for item in picker.children if isinstance(item, discord.ui.Select))


# ---------------------------------------------------------------------------
# Source scan — the raw classes are gone, replacements are house classes
# ---------------------------------------------------------------------------

_MIGRATED = {
    "stoney_verify/commands_ext/public_community_pings.py": {
        "CommunityOptionEditorSectionSelect": "DankPickerView",
        "CommunityPingsSectionSelect": "DankPickerView",
    },
    "stoney_verify/commands_ext/public_community_tools.py": {
        "InfoMemberSelect": "DankUserSelect",
    },
}

_RAW_BASES = {"Select", "ChannelSelect", "RoleSelect", "UserSelect", "MentionableSelect"}


def _class_bases(path: Path) -> dict[str, list[str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            bases = []
            for base in node.bases:
                if isinstance(base, ast.Attribute):
                    bases.append(base.attr)
                elif isinstance(base, ast.Name):
                    bases.append(base.id)
            out[node.name] = bases
    return out


@pytest.mark.parametrize("module_path,migrated", list(_MIGRATED.items()))
def test_migrated_classes_are_house_framework_classes(module_path: str, migrated: dict[str, str]) -> None:
    bases = _class_bases(ROOT / module_path)
    for class_name, expected_base in migrated.items():
        assert class_name in bases, f"{class_name} missing from {module_path}"
        assert bases[class_name] == [expected_base], (
            f"{class_name} must be a {expected_base}, got bases {bases[class_name]}"
        )
        assert not (set(bases[class_name]) & _RAW_BASES)


# ---------------------------------------------------------------------------
# Community & Pings — option editor section picker
# ---------------------------------------------------------------------------


@pytest.fixture()
def staff_allowed(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    gate = AsyncMock(return_value=True)
    monkeypatch.setattr(pings, "_staff_authorized", gate)
    return gate


def test_option_editor_picker_choices_and_custom_id_parity() -> None:
    picker = pings.CommunityOptionEditorSectionSelect(OWNER_ID, "opt-key", "rules")
    assert isinstance(picker, DankPickerView)
    assert picker.custom_id == "dank:pings:option:section:v1"
    assert _select_child(picker).custom_id == "dank:pings:option:section:v1"
    assert picker.placeholder == "Choose what to change"
    assert [(choice.value, choice.label, choice.default) for choice in picker.choices] == [
        ("details", "Option Details", False),
        ("rules", "Access Rules", True),
        ("notifications", "Toke & Movie Notifications", False),
        ("order", "Order & Position", False),
    ]
    assert picker.owner_id == OWNER_ID
    assert picker.option_key == "opt-key"


def test_option_editor_pick_routes_to_the_same_editor_render(
    monkeypatch: pytest.MonkeyPatch, staff_allowed: AsyncMock,
) -> None:
    opened = AsyncMock()
    monkeypatch.setattr(pings, "_open_option_editor", opened)
    picker = pings.CommunityOptionEditorSectionSelect(OWNER_ID, "opt-key", "details")
    interaction = FakeInteraction(guild=fake_guild())
    run(picker.handle_pick(interaction, "order"))
    opened.assert_awaited_once_with(interaction, OWNER_ID, "opt-key", section="order")


def test_option_editor_pick_still_requires_staff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pings, "_staff_authorized", AsyncMock(return_value=False))
    opened = AsyncMock()
    monkeypatch.setattr(pings, "_open_option_editor", opened)
    picker = pings.CommunityOptionEditorSectionSelect(OWNER_ID, "opt-key", "details")
    run(picker.handle_pick(FakeInteraction(guild=fake_guild()), "rules"))
    opened.assert_not_awaited()


def test_option_editor_home_returns_to_the_editor_at_the_current_section(
    monkeypatch: pytest.MonkeyPatch, staff_allowed: AsyncMock,
) -> None:
    opened = AsyncMock()
    monkeypatch.setattr(pings, "_open_option_editor", opened)
    picker = pings.CommunityOptionEditorSectionSelect(OWNER_ID, "opt-key", "notifications")
    interaction = FakeInteraction(guild=fake_guild())
    run(picker.on_home(interaction))
    opened.assert_awaited_once_with(interaction, OWNER_ID, "opt-key", section="notifications")


def test_option_editor_picker_owner_check_matches_the_host_view() -> None:
    picker = pings.CommunityOptionEditorSectionSelect(OWNER_ID, "opt-key", "details")
    assert run(picker.interaction_check(FakeInteraction(guild=fake_guild()))) is True

    stranger = FakeInteraction(
        guild=fake_guild(), user=SimpleNamespace(id=OWNER_ID + 1),
    )
    assert run(picker.interaction_check(stranger)) is False
    assert stranger.response.sent[-1]["content"] == (
        "❌ Open your own Community & Pings panel to use these controls."
    )


def test_option_editor_view_uses_the_opener_not_a_select() -> None:
    view = pings.CommunityOptionEditorView(OWNER_ID, "opt-key", section="rules")
    assert not any(isinstance(item, discord.ui.Select) for item in view.children)
    opener = _opener(view, "dank:pings:option:section:open:v1")
    assert opener.label == "Switch Section"

    interaction = FakeInteraction(guild=fake_guild())
    run(opener.callback(interaction))
    swapped = interaction.response.edits[-1]
    assert isinstance(swapped["view"], pings.CommunityOptionEditorSectionSelect)
    assert swapped["view"].option_key == "opt-key"
    assert swapped["view"].section == "rules"
    assert swapped["embed"].title == "Host Panel"


def test_option_editor_opener_denies_a_non_owner_like_the_host_view() -> None:
    view = pings.CommunityOptionEditorView(OWNER_ID, "opt-key", section="details")
    opener = _opener(view, "dank:pings:option:section:open:v1")
    stranger = FakeInteraction(
        guild=fake_guild(), user=SimpleNamespace(id=OWNER_ID + 1),
    )
    run(opener.callback(stranger))
    assert stranger.response.edits == []
    assert stranger.response.sent[-1]["content"] == (
        "❌ Open your own Community & Pings panel to use these controls."
    )


# ---------------------------------------------------------------------------
# Community & Pings — manager section picker
# ---------------------------------------------------------------------------


def test_manager_picker_choices_and_custom_id_parity() -> None:
    picker = pings.CommunityPingsSectionSelect(OWNER_ID, "toke")
    assert isinstance(picker, DankPickerView)
    assert picker.custom_id == "dank:pings:manager:section:v2"
    assert _select_child(picker).custom_id == "dank:pings:manager:section:v2"
    assert picker.placeholder == "Choose Community & Pings settings"
    assert [(choice.value, choice.label, choice.default) for choice in picker.choices] == [
        ("options", "Member Options", False),
        ("groups", "Role Groups", False),
        ("toke", "Toke Integration", True),
        ("movie", "Movie Night", False),
    ]


def test_manager_pick_renders_the_manager_at_the_chosen_section(
    monkeypatch: pytest.MonkeyPatch, staff_allowed: AsyncMock,
) -> None:
    model = object()
    monkeypatch.setattr(pings, "_load", AsyncMock(return_value=({"raw": 1}, model)))
    monkeypatch.setattr(
        pings, "_manager_embed",
        lambda guild, raw, model, section="options": discord.Embed(title=f"manager {section}"),
    )
    replaced = AsyncMock()
    monkeypatch.setattr(pings, "_replace", replaced)

    picker = pings.CommunityPingsSectionSelect(OWNER_ID, "options")
    interaction = FakeInteraction(guild=fake_guild())
    run(picker.handle_pick(interaction, "groups"))

    # The old callback deferred before reloading and re-rendering.
    assert interaction.response.deferred is True
    replaced.assert_awaited_once()
    args, kwargs = replaced.call_args
    assert args == (interaction,)
    assert kwargs["embed"].title == "manager groups"
    assert isinstance(kwargs["view"], pings.CommunityPingsManagerView)
    assert kwargs["view"].owner_id == OWNER_ID
    assert kwargs["view"].section == "groups"


def test_manager_pick_still_requires_staff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pings, "_staff_authorized", AsyncMock(return_value=False))
    loaded = AsyncMock(return_value=({}, object()))
    monkeypatch.setattr(pings, "_load", loaded)
    replaced = AsyncMock()
    monkeypatch.setattr(pings, "_replace", replaced)

    picker = pings.CommunityPingsSectionSelect(OWNER_ID, "options")
    run(picker.handle_pick(FakeInteraction(guild=fake_guild()), "movie"))
    loaded.assert_not_awaited()
    replaced.assert_not_awaited()


def test_manager_pick_outside_a_server_keeps_the_old_denial(
    monkeypatch: pytest.MonkeyPatch, staff_allowed: AsyncMock,
) -> None:
    replaced = AsyncMock()
    monkeypatch.setattr(pings, "_replace", replaced)
    picker = pings.CommunityPingsSectionSelect(OWNER_ID, "options")
    interaction = FakeInteraction(guild=None)
    run(picker.handle_pick(interaction, "movie"))
    replaced.assert_not_awaited()
    assert interaction.response.sent[-1]["content"] == "❌ Use this inside a server."


def test_manager_home_returns_to_the_manager_at_the_current_section(
    monkeypatch: pytest.MonkeyPatch, staff_allowed: AsyncMock,
) -> None:
    opened = AsyncMock()
    monkeypatch.setattr(pings, "_open_manager_message", opened)
    picker = pings.CommunityPingsSectionSelect(OWNER_ID, "movie")
    interaction = FakeInteraction(guild=fake_guild())
    run(picker.on_home(interaction))
    opened.assert_awaited_once_with(interaction, owner_id=OWNER_ID, section="movie")


def test_manager_view_uses_the_opener_not_a_select() -> None:
    view = pings.CommunityPingsManagerView(OWNER_ID, section="toke")
    assert not any(isinstance(item, discord.ui.Select) for item in view.children)
    assert len(view.children) <= 9
    opener = _opener(view, "dank:pings:manager:section:open:v1")
    assert opener.label == "Switch Section"

    interaction = FakeInteraction(guild=fake_guild())
    run(opener.callback(interaction))
    swapped = interaction.response.edits[-1]
    assert isinstance(swapped["view"], pings.CommunityPingsSectionSelect)
    assert swapped["view"].current == "toke"
    assert swapped["embed"].title == "Host Panel"


# ---------------------------------------------------------------------------
# Community Tools — InfoMemberSelect (DankUserSelect wrapper)
# ---------------------------------------------------------------------------

_CREATED = datetime(2021, 5, 4, 12, 0, tzinfo=timezone.utc)
_JOINED = datetime(2023, 2, 3, 12, 0, tzinfo=timezone.utc)


def _fake_target(user_id: int = 555, *, bot: bool = False) -> Any:
    return SimpleNamespace(
        id=user_id,
        display_name="Target Person",
        display_avatar=SimpleNamespace(url="https://example.com/avatar.png"),
        created_at=_CREATED,
        bot=bot,
    )


def _fake_member() -> Any:
    return SimpleNamespace(
        id=555,
        display_name="Target Person",
        display_avatar=SimpleNamespace(url="https://example.com/avatar.png"),
        created_at=_CREATED,
        bot=False,
        color=discord.Color.green(),
        joined_at=_JOINED,
        top_role=SimpleNamespace(mention="<@&900>"),
        roles=[
            SimpleNamespace(mention="<@&1>", managed=False),
            SimpleNamespace(mention="<@&2>", managed=False),
            SimpleNamespace(mention="<@&3>", managed=True),
        ],
    )


def _info_select(view: tools.InfoView) -> tools.InfoMemberSelect:
    return next(item for item in view.children if isinstance(item, tools.InfoMemberSelect))


def test_info_member_select_is_a_house_user_select_with_no_owner_lock() -> None:
    select = tools.InfoMemberSelect()
    assert isinstance(select, DankUserSelect)
    assert select.custom_id == "dank:community:info:user:v2"
    assert select.placeholder == "Choose a member"
    # The original select had no owner binding; the wrapper must not add one.
    assert select.author_id == 0

    view = tools.InfoView(OWNER_ID)
    assert _info_select(view).custom_id == "dank:community:info:user:v2"


def test_info_member_pick_renders_the_same_member_info() -> None:
    member = _fake_member()
    guild = fake_guild(get_member=lambda user_id: member if user_id == 555 else None)
    view = tools.InfoView(OWNER_ID)
    select = _info_select(view)
    select._values = [_fake_target()]
    interaction = FakeInteraction(guild=guild)
    run(select.callback(interaction))

    edit = interaction.response.edits[-1]
    embed = edit["embed"]
    assert embed.title == "👤 Target Person"
    fields = {field.name: field.value for field in embed.fields}
    assert fields["User ID"] == "`555`"
    assert fields["Account created"] == discord.utils.format_dt(_CREATED, style="R")
    assert fields["Account type"] == "Member"
    assert fields["Joined server"] == discord.utils.format_dt(_JOINED, style="R")
    assert fields["Top role"] == "<@&900>"
    # roles[0] is @everyone; the managed role is hidden, as before.
    assert fields["Roles (1)"] == "<@&2>"
    assert isinstance(edit["view"], tools.InfoView)
    assert edit["view"].owner_id == OWNER_ID


def test_info_member_pick_for_a_non_member_target_skips_member_fields() -> None:
    guild = fake_guild(get_member=lambda user_id: None)
    view = tools.InfoView(OWNER_ID)
    select = _info_select(view)
    select._values = [_fake_target(556, bot=True)]
    interaction = FakeInteraction(guild=guild)
    run(select.callback(interaction))

    embed = interaction.response.edits[-1]["embed"]
    fields = {field.name: field.value for field in embed.fields}
    assert fields["User ID"] == "`556`"
    assert fields["Account type"] == "Bot"
    assert "Joined server" not in fields
    assert "Top role" not in fields


def test_info_member_press_by_non_opener_matches_the_original_denial() -> None:
    """The original callback ran the host view's owner check first:
    a non-opener got the InfoView denial and no member-info render.
    The wrapper adds no lock of its own, so the same check — and the
    same message — still decides."""
    member = _fake_member()
    guild = fake_guild(get_member=lambda user_id: member if user_id == 555 else None)
    view = tools.InfoView(OWNER_ID)
    select = _info_select(view)
    select._values = [_fake_target()]
    stranger = FakeInteraction(
        guild=guild, user=SimpleNamespace(id=OWNER_ID + 1),
    )
    run(select.callback(stranger))

    assert stranger.response.edits == []
    assert stranger.response.sent[-1]["content"] == (
        "❌ Open your own `/dank home` panel to use these controls."
    )
    assert stranger.response.sent[-1]["ephemeral"] is True


def test_info_member_select_detached_from_info_view_stays_silent() -> None:
    """The original callback returned silently when its view was not
    an InfoView; the migrated pick does the same."""
    select = tools.InfoMemberSelect()
    select._values = [_fake_target()]
    interaction = FakeInteraction(guild=fake_guild(get_member=lambda user_id: None))
    run(select.callback(interaction))
    assert interaction.response.sent == []
    assert interaction.response.edits == []
