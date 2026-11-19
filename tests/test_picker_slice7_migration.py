"""Slice 7 picker migration (Design Studio) — routing parity, saved-payload parity, paging, source scan.

Felix's standing rule: every picker uses the house framework
(``stoney_verify/ui/picker.py``). Slice 7 migrated every Design
Studio picker: the v1 format-lock channel/category pickers, the
exact editor's font/separator/frame/strength pickers and frame
browser, the channel page jump, the protection-mode picker and the
style-change separator picker (``public_design_studio.py``), plus
the v2 server theme/font/strength/separator/icon-mode/category-frame
pickers and the category-style category/template/font pickers
(``public_design_studio_v2.py``).

These tests prove, for every migrated surface:
- the raw select classes are gone from the source files and the
  replacements are house-framework classes with stable custom_ids;
- driving a picker routes to the same behavior the old select
  callback had, and the exact payload handed to the save/write
  function matches what the old callback produced for the same pick;
- the lists that can outgrow Discord's 25-option select limit — a
  server's categories (silently truncated to 25 by the raw
  category-style select) and the channel editor's page list
  (windowed to 25 by the raw page-jump select) — stay fully
  reachable through chunked paging.
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import discord
import pytest

from stoney_verify.commands_ext import public_design_studio as legacy
from stoney_verify.commands_ext import public_design_studio_v2 as design
from stoney_verify.services import server_design_studio as studio
from stoney_verify.services import server_design_style_zones as zones
from stoney_verify.ui.picker import DankChannelSelect, DankPickerView

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
    base = {"id": GUILD_ID, "categories": [], "get_channel": lambda channel_id: None}
    base.update(kwargs)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _allow_design(monkeypatch: pytest.MonkeyPatch) -> None:
    """Open the design permission gate in both studio modules."""
    monkeypatch.setattr(legacy, "_require_design_permission", AsyncMock(return_value=True))
    monkeypatch.setattr(design, "_require_design_permission", AsyncMock(return_value=True))
    monkeypatch.setattr(legacy, "_reject_reserved_design_target", AsyncMock(return_value=False))


def _opener(view: Any, custom_id: str) -> Any:
    return next(
        item for item in getattr(view, "children", [])
        if getattr(item, "custom_id", "") == custom_id
    )


# ---------------------------------------------------------------------------
# Source scan — the raw classes are gone, replacements are house classes
# ---------------------------------------------------------------------------

_MIGRATED_V1 = {
    "CategoryFormatLockSelect": "DankChannelSelect",
    "ChannelFormatLockSelect": "DankChannelSelect",
    "ExactFontSelect": "DankPickerView",
    "ExactSeparatorSelect": "DankPickerView",
    "ExactFrameSelect": "DankPickerView",
    "ExactFrameBrowserSelect": "DankPickerView",
    "ExactStrengthSelect": "DankPickerView",
    "ChannelPageJumpSelect": "DankPickerView",
    "ProtectionModeSelect": "DankPickerView",
    "StyleChangeSeparatorSelect": "DankPickerView",
}

_MIGRATED_V2 = {
    "DesignServerThemeSelect": "DankPickerView",
    "DesignServerFontSelect": "DankPickerView",
    "DesignServerStrengthSelect": "DankPickerView",
    "DesignServerSeparatorSelect": "DankPickerView",
    "DesignServerIconModeSelect": "DankPickerView",
    "DesignServerCategoryFrameSelect": "DankPickerView",
    "DesignCategoryStyleCategorySelect": "DankPickerView",
    "DesignCategoryStyleTemplateSelect": "DankPickerView",
    "DesignCategoryStyleFontSelect": "DankPickerView",
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


@pytest.mark.parametrize(
    "module_path,migrated",
    [
        ("stoney_verify/commands_ext/public_design_studio.py", _MIGRATED_V1),
        ("stoney_verify/commands_ext/public_design_studio_v2.py", _MIGRATED_V2),
    ],
)
def test_migrated_classes_are_house_framework_classes(module_path: str, migrated: dict[str, str]) -> None:
    bases = _class_bases(ROOT / module_path)
    for class_name, expected_base in migrated.items():
        assert class_name in bases, f"{class_name} missing from {module_path}"
        assert bases[class_name] == [expected_base], (
            f"{class_name} must be a {expected_base}, got bases {bases[class_name]}"
        )
        assert not (set(bases[class_name]) & _RAW_BASES)


# ---------------------------------------------------------------------------
# Format locks (v1) — DankChannelSelect wrappers keep their stable ids
# ---------------------------------------------------------------------------


def test_format_lock_selects_are_house_channel_selects_with_stable_ids() -> None:
    category = legacy.CategoryFormatLockSelect()
    channel = legacy.ChannelFormatLockSelect()
    assert isinstance(category, DankChannelSelect)
    assert isinstance(channel, DankChannelSelect)
    assert category.custom_id == "dank_design:lock_category_select"
    assert channel.custom_id == "dank_design:lock_channel_select"
    assert category.channel_types == [discord.ChannelType.category]
    assert discord.ChannelType.text in channel.channel_types
    assert discord.ChannelType.voice in channel.channel_types

    category_view = legacy.CategoryFormatLockPickerView()
    channel_view = legacy.ChannelFormatLockPickerView()
    assert any(isinstance(item, legacy.CategoryFormatLockSelect) for item in category_view.children)
    assert any(isinstance(item, legacy.ChannelFormatLockSelect) for item in channel_view.children)


def test_category_format_lock_pick_saves_the_same_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    saved: list[int] = []

    async def fake_save(interaction: Any, category_id: int) -> dict:
        saved.append(category_id)
        return {}

    monkeypatch.setattr(legacy, "_save_category_lock", fake_save)
    monkeypatch.setattr(legacy, "_format_locks_embed", lambda guild, options: discord.Embed(title="locks"))
    interaction = FakeInteraction(guild=fake_guild())
    target = SimpleNamespace(id=9001, mention="#staff")

    run(legacy.CategoryFormatLockSelect()._save_lock(interaction, target))

    assert saved == [9001]
    assert interaction.response.deferred is True
    edit = interaction.original_edits[-1]
    assert isinstance(edit["view"], legacy.FormatLocksView)
    assert edit["embed"].title == "✅ Category Format Lock Saved"
    assert "#staff" in str(edit["embed"].description)


def test_channel_format_lock_pick_saves_the_same_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    saved: list[int] = []

    async def fake_save(interaction: Any, channel_id: int) -> dict:
        saved.append(channel_id)
        return {}

    monkeypatch.setattr(legacy, "_save_channel_lock", fake_save)
    monkeypatch.setattr(legacy, "_format_locks_embed", lambda guild, options: discord.Embed(title="locks"))
    interaction = FakeInteraction(guild=fake_guild())
    target = SimpleNamespace(id=9002, mention="#general")

    run(legacy.ChannelFormatLockSelect()._save_lock(interaction, target))

    assert saved == [9002]
    edit = interaction.original_edits[-1]
    assert isinstance(edit["view"], legacy.FormatLocksView)
    assert edit["embed"].title == "✅ Channel Override Lock Saved"


# ---------------------------------------------------------------------------
# Exact editor pickers (v1) — same draft patches as the raw selects
# ---------------------------------------------------------------------------


@pytest.fixture()
def draft_patches(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    patches: list[dict] = []

    async def fake_update(interaction: Any, *, scope: str, target_id: int, patch: dict) -> None:
        patches.append({"scope": scope, "target_id": target_id, "patch": dict(patch)})

    monkeypatch.setattr(legacy, "_update_exact_draft", fake_update)
    return patches


def test_exact_font_pick_patches_font(draft_patches: list[dict]) -> None:
    picker = legacy.ExactFontSelect("channel", 42, "normal")
    assert isinstance(picker, DankPickerView)
    assert picker.custom_id == "dank_design:exact_font:picker:channel:42"
    run(picker.handle_pick(FakeInteraction(guild=fake_guild()), "fraktur"))
    assert draft_patches == [{"scope": "channel", "target_id": 42, "patch": {"font": "fraktur"}}]


def test_exact_separator_pick_patches_separator(draft_patches: list[dict]) -> None:
    picker = legacy.ExactSeparatorSelect("category", 42, "none")
    assert picker.placeholder == "2) Choose child-channel separator"
    run(picker.handle_pick(FakeInteraction(guild=fake_guild()), "dash"))
    assert draft_patches == [{"scope": "category", "target_id": 42, "patch": {"separator_id": "dash"}}]
    # The full 25-entry exact separator catalog fits one picker page.
    assert len(picker.choices) == len(legacy.EDITOR_SEPARATOR_IDS)


def test_exact_strength_pick_patches_int_strength(draft_patches: list[dict]) -> None:
    picker = legacy.ExactStrengthSelect("channel", 42, 4)
    run(picker.handle_pick(FakeInteraction(guild=fake_guild()), "2"))
    assert draft_patches == [{"scope": "channel", "target_id": 42, "patch": {"strength": 2}}]


def test_exact_frame_pick_patches_frame_and_browse_opens_browser(draft_patches: list[dict]) -> None:
    picker = legacy.ExactFrameSelect("category", 42, "plain")
    interaction = FakeInteraction(guild=fake_guild())
    run(picker.handle_pick(interaction, "line"))
    assert draft_patches == [{"scope": "category", "target_id": 42, "patch": {"category_frame_id": "line"}}]

    browser_interaction = FakeInteraction(guild=fake_guild())
    run(picker.handle_pick(browser_interaction, legacy.EXACT_FRAME_BROWSE_VALUE))
    edit = browser_interaction.response.edits[-1]
    assert isinstance(edit["view"], legacy.ExactFrameBrowserView)


def test_exact_frame_browser_pick_patches_frame(draft_patches: list[dict]) -> None:
    picker = legacy.ExactFrameBrowserSelect("category", 42, {"category_frame_id": "line"}, page=0)
    first_frame = picker.choices[0].value
    run(picker.handle_pick(FakeInteraction(guild=fake_guild()), first_frame))
    assert draft_patches == [{"scope": "category", "target_id": 42, "patch": {"category_frame_id": first_frame}}]


def test_exact_editor_factory_uses_openers_not_selects() -> None:
    lock = {
        "font": "fraktur",
        "separator_id": "bar_full",
        "category_frame_id": "line",
        "strength": 4,
        "editor_page": 0,
        "editor_category_filter_id": None,
    }
    view = legacy.ExactFormatEditorViewFactory(fake_guild(), "category", 42, lock)
    openers = {
        getattr(item, "custom_id", ""): item
        for item in view.children
        if isinstance(item, legacy.DesignPickerOpenButton)
    }
    assert set(openers) == {
        "dank_design:exact_font:open",
        "dank_design:exact_separator:open",
        "dank_design:exact_frame:open",
        "dank_design:exact_strength:open",
    }
    assert isinstance(openers["dank_design:exact_font:open"].build_picker(), legacy.ExactFontSelect)
    assert isinstance(openers["dank_design:exact_frame:open"].build_picker(), legacy.ExactFrameSelect)
    assert not any(isinstance(item, discord.ui.Select) for item in view.children)

    channel_view = legacy.ExactFormatEditorViewFactory(fake_guild(), "channel", 42, lock)
    channel_openers = {
        getattr(item, "custom_id", ""): item
        for item in channel_view.children
        if isinstance(item, legacy.DesignPickerOpenButton)
    }
    assert "dank_design:exact_frame:open" not in channel_openers
    assert channel_openers["dank_design:exact_strength:open"].row == 2


# ---------------------------------------------------------------------------
# Channel page jump (v1) — every editor page reachable, same destination
# ---------------------------------------------------------------------------


def _fake_channel_groups(count: int) -> list[dict]:
    return [
        {
            "category": None,
            "category_id": None,
            "channels": [],
            "part": 1,
            "parts": 1,
            "label": f"Category {index + 1}",
        }
        for index in range(count)
    ]


def test_channel_page_jump_pages_every_editor_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(legacy, "_channel_editor_groups", lambda guild: _fake_channel_groups(60))
    picker = legacy.ChannelPageJumpSelect(fake_guild(), page=0, category_id=None)
    assert picker.picker_pages == 3

    seen: list[str] = []
    for page_index in range(picker.picker_pages):
        chunk = picker if page_index == picker.picker_page else picker._for_picker_page(page_index)
        seen.extend(choice.value for choice in chunk.choices)
        assert chunk.custom_id.endswith(f":{page_index}") if page_index else True
    assert sorted(int(value) for value in seen) == list(range(60))


def test_channel_page_jump_pick_renders_the_same_editor_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(legacy, "_channel_editor_groups", lambda guild: _fake_channel_groups(60))
    monkeypatch.setattr(legacy, "_channel_editor_embed", lambda guild, page=0, category_id=None: discord.Embed(title=f"page {page}"))
    picker = legacy.ChannelPageJumpSelect(fake_guild(), page=0, category_id=None)
    interaction = FakeInteraction(guild=fake_guild())
    run(picker.handle_pick(interaction, "59"))
    edit = interaction.response.edits[-1]
    assert isinstance(edit["view"], legacy.ChannelEditorPickerView)
    assert edit["embed"].title == "page 59"


def test_channel_editor_view_offers_the_jump_opener(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(legacy, "_channel_editor_groups", lambda guild: _fake_channel_groups(7))
    view = legacy.ChannelEditorPickerView(fake_guild(), page=0)
    opener = _opener(view, "dank_design:channel_page_jump:open")
    assert isinstance(opener.build_picker(), legacy.ChannelPageJumpSelect)
    assert not any(isinstance(item, discord.ui.Select) for item in view.children)


# ---------------------------------------------------------------------------
# Protection mode + style change (v1)
# ---------------------------------------------------------------------------


def test_protection_mode_pick_saves_the_same_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    saved: list[dict] = []

    async def fake_save(interaction: Any, *, target_id: int, mode: Any) -> dict:
        saved.append({"target_id": target_id, "mode": mode})
        return {}

    monkeypatch.setattr(legacy, "_save_protection_rule", fake_save)
    monkeypatch.setattr(legacy, "_base_for_channel", lambda channel: "general")
    monkeypatch.setattr(legacy, "_inherited_protection_mode", lambda options, base: "full")
    monkeypatch.setattr(legacy, "_protection_item_rules", lambda options: {})
    monkeypatch.setattr(legacy, "_channel_action_embed", lambda channel: discord.Embed(title="channel"))
    channel = SimpleNamespace(id=42, name="general", category=None)
    guild = fake_guild(get_channel=lambda channel_id: channel)
    picker = legacy.ProtectionModeSelect(channel_id=42, current="full")
    assert picker.custom_id == "dank_design:protection_mode:picker:42"

    run(picker.handle_pick(FakeInteraction(guild=guild), "never"))
    assert saved == [{"target_id": 42, "mode": "never"}]

    run(picker.handle_pick(FakeInteraction(guild=guild), "__clear__"))
    assert saved[-1] == {"target_id": 42, "mode": None}

    view = legacy.ProtectionModeView(channel_id=42, current="full")
    opener = _opener(view, "dank_design:protection_mode:open")
    assert isinstance(opener.build_picker(), legacy.ProtectionModeSelect)


def test_style_change_separator_pick_rerenders_with_the_same_separator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(legacy, "_load_design_options", AsyncMock(return_value={}))
    monkeypatch.setattr(legacy, "_style_change_embed", lambda guild, options, separator_id: discord.Embed(title=f"sep {separator_id}"))
    picker = legacy.StyleChangeSeparatorSelect("bar_heavy")
    interaction = FakeInteraction(guild=fake_guild())
    run(picker.handle_pick(interaction, "dash"))
    edit = interaction.original_edits[-1]
    assert isinstance(edit["view"], legacy.StyleChangeView)
    assert edit["view"].separator_id == "dash"
    assert edit["embed"].title == "sep dash"

    view = legacy.StyleChangeView(separator_id="bar_heavy")
    opener = _opener(view, "dank_design:style_change_separator:open")
    assert isinstance(opener.build_picker(), legacy.StyleChangeSeparatorSelect)


# ---------------------------------------------------------------------------
# Server pickers (v2) — same saved options payloads as the raw selects
# ---------------------------------------------------------------------------

_BASE_SERVER_OPTIONS: dict[str, Any] = {
    "theme_id": "gothic_clean",
    "strength": 4,
    "font": "sans",
    "separator_id": "dash",
    "category_frame_id": "line",
    "icon_mode": "keep_existing",
}


@pytest.fixture()
def server_saves(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Capture the exact options dict each v2 picker hands to the save path."""
    saved: list[dict] = []

    async def fake_load(guild_id: int) -> dict:
        return dict(_BASE_SERVER_OPTIONS)

    async def fake_save(interaction: Any, options: dict) -> dict:
        saved.append(dict(options))
        return options

    monkeypatch.setattr(design, "_load_design_options", fake_load)
    monkeypatch.setattr(legacy, "_save_options", fake_save)
    monkeypatch.setattr(legacy, "_sync_enabled_global_lock", lambda options: None)
    return saved


def _server_interaction() -> FakeInteraction:
    return FakeInteraction(guild=fake_guild())


def test_theme_pick_saves_theme_and_clears_font(server_saves: list[dict]) -> None:
    picker = design.DesignServerThemeSelect("gothic_clean")
    assert picker.custom_id == "dank_design_v2:server_theme:picker:v1"
    new_theme = studio.THEMES[1].id
    interaction = _server_interaction()
    run(picker.handle_pick(interaction, new_theme))
    assert server_saves[-1]["theme_id"] == new_theme
    assert "font" not in server_saves[-1]
    assert isinstance(interaction.original_edits[-1]["view"], design.DesignServerView)


def test_font_pick_saves_or_clears_the_font_override(server_saves: list[dict]) -> None:
    picker = design.DesignServerFontSelect(dict(_BASE_SERVER_OPTIONS))
    run(picker.handle_pick(_server_interaction(), "fraktur"))
    assert server_saves[-1]["font"] == "fraktur"

    run(picker.handle_pick(_server_interaction(), "__theme__"))
    assert "font" not in server_saves[-1]


def test_strength_pick_saves_int_strength(server_saves: list[dict]) -> None:
    picker = design.DesignServerStrengthSelect(4)
    run(picker.handle_pick(_server_interaction(), "2"))
    assert server_saves[-1]["strength"] == 2


def test_separator_pick_saves_or_clears_the_separator_override(server_saves: list[dict]) -> None:
    picker = design.DesignServerSeparatorSelect(dict(_BASE_SERVER_OPTIONS))
    run(picker.handle_pick(_server_interaction(), "middle_dot"))
    assert server_saves[-1]["separator_id"] == "middle_dot"

    run(picker.handle_pick(_server_interaction(), "__theme__"))
    assert "separator_id" not in server_saves[-1]


def test_icon_mode_pick_saves_mode_and_returns_to_frame_view(server_saves: list[dict]) -> None:
    picker = design.DesignServerIconModeSelect(dict(_BASE_SERVER_OPTIONS))
    interaction = _server_interaction()
    run(picker.handle_pick(interaction, "clear"))
    assert server_saves[-1]["icon_mode"] == "clear"
    assert isinstance(interaction.original_edits[-1]["view"], design.DesignServerCategoryFrameView)


def test_category_frame_pick_saves_or_clears_the_frame_override(server_saves: list[dict]) -> None:
    picker = design.DesignServerCategoryFrameSelect(dict(_BASE_SERVER_OPTIONS), page=0)
    frame_value = next(choice.value for choice in picker.choices if choice.value != "__theme__")
    interaction = _server_interaction()
    run(picker.handle_pick(interaction, frame_value))
    assert server_saves[-1]["category_frame_id"] == frame_value
    assert isinstance(interaction.original_edits[-1]["view"], design.DesignServerView)

    run(picker.handle_pick(_server_interaction(), "__theme__"))
    assert "category_frame_id" not in server_saves[-1]


def test_design_server_view_uses_openers_not_selects() -> None:
    view = design.DesignServerView(dict(_BASE_SERVER_OPTIONS))
    expected = {
        "dank_design_v2:server_theme:open": design.DesignServerThemeSelect,
        "dank_design_v2:server_font:open": design.DesignServerFontSelect,
        "dank_design_v2:server_strength:open": design.DesignServerStrengthSelect,
        "dank_design_v2:server_separator:open": design.DesignServerSeparatorSelect,
    }
    for custom_id, picker_type in expected.items():
        opener = _opener(view, custom_id)
        assert isinstance(opener.build_picker(), picker_type)
    assert not any(isinstance(item, discord.ui.Select) for item in view.children)

    frame_view = design.DesignServerCategoryFrameView(dict(_BASE_SERVER_OPTIONS), page=0)
    assert isinstance(_opener(frame_view, "dank_design_v2:category_frame:open").build_picker(), design.DesignServerCategoryFrameSelect)
    assert isinstance(_opener(frame_view, "dank_design_v2:icon_mode:open").build_picker(), design.DesignServerIconModeSelect)
    assert not any(isinstance(item, discord.ui.Select) for item in frame_view.children)


# ---------------------------------------------------------------------------
# Category styles (v2) — paged categories, same draft mutations
# ---------------------------------------------------------------------------


def _categories_guild(count: int) -> Any:
    categories = [
        SimpleNamespace(
            id=1000 + index,
            name=f"Category {index + 1}",
            position=index,
            channels=[],
        )
        for index in range(count)
    ]
    return SimpleNamespace(id=GUILD_ID, categories=categories, get_channel=lambda channel_id: None)


def test_category_style_category_picker_pages_every_category() -> None:
    guild = _categories_guild(40)
    host = design.DesignCategoryStylesView(guild, {})
    opener = _opener(host, "dank_design_v2:category_style_category:open")
    picker = opener.build_picker()
    assert isinstance(picker, design.DesignCategoryStyleCategorySelect)
    assert picker.pages == 2

    seen: list[str] = []
    for page_index in range(picker.pages):
        chunk = picker if page_index == picker.page else picker._for_page(page_index)
        seen.extend(choice.value for choice in chunk.choices)
    assert sorted(int(value) for value in seen) == [1000 + index for index in range(40)]

    # The old raw select silently stopped at 25 categories.
    first_page_values = {choice.value for choice in picker._for_page(0).choices}
    assert len(first_page_values) == 25


def test_category_style_category_pick_loads_the_saved_draft() -> None:
    guild = _categories_guild(40)
    options = {
        "category_styles": {
            "1039": {"font": "monospace", "category_font": None, "separator_id": None, "category_frame_id": None},
        }
    }
    host = design.DesignCategoryStylesView(guild, options, category_id=1000)
    picker = design.DesignCategoryStyleCategorySelect(host, page=1)
    interaction = FakeInteraction(guild=guild)
    run(picker.handle_pick(interaction, "1039"))
    assert host.category_id == 1039
    assert host.draft["font"] == "monospace"
    respawned = interaction.response.edits[-1]["view"]
    assert isinstance(respawned, design.DesignCategoryStylesView)
    assert respawned.category_id == 1039


def test_category_style_template_and_font_picks_mutate_the_same_draft() -> None:
    guild = _categories_guild(3)
    host = design.DesignCategoryStylesView(guild, {})
    interaction = FakeInteraction(guild=guild)

    template_picker = design.DesignCategoryStyleTemplateSelect(host)
    garden = zones.category_style_from_template("garden")
    run(template_picker.handle_pick(interaction, "garden"))
    assert host.template_id == "garden"
    assert host.draft["font"] == garden["font"]
    assert host.draft["category_font"] == garden["category_font"]

    font_picker = design.DesignCategoryStyleFontSelect(host, field="font")
    run(font_picker.handle_pick(interaction, "monospace"))
    assert host.draft["font"] == "monospace"
    assert host.template_id == ""
    assert host.clear_style is False

    run(font_picker.handle_pick(interaction, "__server__"))
    assert host.draft["font"] is None

    none_picker = design.DesignCategoryStyleTemplateSelect(host)
    run(none_picker.handle_pick(interaction, "__none__"))
    assert host.clear_style is True
    assert host.template_id == "__none__"


def test_category_styles_view_uses_openers_not_selects() -> None:
    guild = _categories_guild(3)
    host = design.DesignCategoryStylesView(guild, {})
    expected = {
        "dank_design_v2:category_style_category:open": design.DesignCategoryStyleCategorySelect,
        "dank_design_v2:category_style_template:open": design.DesignCategoryStyleTemplateSelect,
        "dank_design_v2:category_style_font:open": design.DesignCategoryStyleFontSelect,
        "dank_design_v2:category_style_header_font:open": design.DesignCategoryStyleFontSelect,
    }
    for custom_id, picker_type in expected.items():
        opener = _opener(host, custom_id)
        assert isinstance(opener.build_picker(), picker_type)
    assert not any(isinstance(item, discord.ui.Select) for item in host.children)


# ---------------------------------------------------------------------------
# Stable custom_ids — every migrated picker surface carries one
# ---------------------------------------------------------------------------


def test_every_migrated_picker_has_a_stable_custom_id() -> None:
    pickers: list[DankPickerView] = [
        legacy.ExactFontSelect("channel", 42, "normal"),
        legacy.ExactSeparatorSelect("channel", 42, "none"),
        legacy.ExactFrameSelect("category", 42, "plain"),
        legacy.ExactFrameBrowserSelect("category", 42, {"category_frame_id": "line"}, page=0),
        legacy.ExactStrengthSelect("channel", 42, 4),
        legacy.ProtectionModeSelect(channel_id=42, current="full"),
        legacy.StyleChangeSeparatorSelect("bar_heavy"),
        design.DesignServerThemeSelect("gothic_clean"),
        design.DesignServerFontSelect(dict(_BASE_SERVER_OPTIONS)),
        design.DesignServerStrengthSelect(4),
        design.DesignServerSeparatorSelect(dict(_BASE_SERVER_OPTIONS)),
        design.DesignServerIconModeSelect(dict(_BASE_SERVER_OPTIONS)),
        design.DesignServerCategoryFrameSelect(dict(_BASE_SERVER_OPTIONS), page=0),
    ]
    guild = _categories_guild(3)
    host = design.DesignCategoryStylesView(guild, {})
    pickers.extend(
        [
            design.DesignCategoryStyleCategorySelect(host),
            design.DesignCategoryStyleTemplateSelect(host),
            design.DesignCategoryStyleFontSelect(host, field="font"),
            design.DesignCategoryStyleFontSelect(host, field="category_font"),
        ]
    )
    for picker in pickers:
        assert picker.custom_id, type(picker).__name__
        select_child = next(item for item in picker.children if isinstance(item, discord.ui.Select))
        assert select_child.custom_id == picker.custom_id
