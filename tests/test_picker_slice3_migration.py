"""Slice 3 picker migration (setup flows) — routing parity, paging, source scan.

Felix's standing rule: every picker uses the house framework
(``stoney_verify/ui/picker.py``). Slice 3 migrated the setup flows:
the three generations of SaveRoleSelect/SaveChannelSelect pairs, the
guided/template option selects, the compact area pickers, the cleanup
and find pickers, the fresh-choice selects, the 020 entitled preset
guard, and the three startup-guard setup shims.

These tests prove, for every migrated surface:
- the raw select classes are gone from the source files;
- host panels construct with opener buttons and no native selects;
- driving an opener and picking a value routes to the same save /
  navigation behavior the old select callback had;
- lists longer than Discord's 25-option select limit stay fully
  reachable through chunked paging.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import discord
import pytest

ROOT = Path(__file__).resolve().parents[1]


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
    def __init__(self, *, guild: Any = None, user_id: int = 77) -> None:
        self.guild = guild
        self.user = SimpleNamespace(id=user_id)
        self.response = FakeResponse()
        self.followup = FakeFollowup()
        self.message = SimpleNamespace(embeds=[discord.Embed(title="Host Panel")])
        self.original_edits: list[dict] = []

    async def edit_original_response(self, *args: Any, **kwargs: Any) -> None:
        self.original_edits.append(kwargs)


def make_role(index: int, *, name: str | None = None) -> Any:
    return SimpleNamespace(
        id=1000 + index,
        name=name or f"role-{index}",
        mention=f"<@&{1000 + index}>",
        members=[],
        managed=False,
        position=500 - index,
        is_default=lambda: False,
    )


def make_channel(index: int, *, name: str | None = None, ctype: Any = None) -> Any:
    return SimpleNamespace(
        id=2000 + index,
        name=name or f"chan-{index}",
        mention=f"<#{2000 + index}>",
        type=ctype if ctype is not None else discord.ChannelType.text,
        category=None,
        channels=[],
    )


class FakeGuild:
    def __init__(self, *, roles: Any = (), channels: Any = (), guild_id: int = 4242) -> None:
        self.id = guild_id
        self.name = "Fake Guild"
        self.roles = list(roles)
        self.channels = list(channels)
        self.me = SimpleNamespace(
            id=999,
            mention="<@999>",
            top_role=SimpleNamespace(position=9999),
        )
        self._roles = {int(r.id): r for r in self.roles}
        self._channels = {int(c.id): c for c in self.channels}

    def get_role(self, role_id: int) -> Any:
        return self._roles.get(int(role_id))

    def get_channel(self, channel_id: int) -> Any:
        return self._channels.get(int(channel_id))


def child_by_custom_id(view: Any, needle: str) -> Any:
    matches = [
        child for child in view.children
        if needle in str(getattr(child, "custom_id", "") or "")
    ]
    assert len(matches) == 1, f"expected one child matching {needle!r}, got {len(matches)}"
    return matches[0]


def open_picker(view: Any, opener: Any, guild: Any = None) -> Any:
    """Drive an opener button and return the picker/browser view it shows."""
    interaction = FakeInteraction(guild=guild)
    run(opener.callback(interaction))
    assert interaction.response.edits, "opener did not edit the message"
    return interaction.response.edits[-1]["view"]


def select_of(picker: Any) -> Any:
    selects = [c for c in picker.children if isinstance(c, discord.ui.Select)]
    assert len(selects) == 1
    return selects[0]


def no_native_selects(view: Any) -> None:
    bad = [
        child for child in view.children
        if isinstance(child, (discord.ui.RoleSelect, discord.ui.ChannelSelect, discord.ui.UserSelect))
        or type(child) is discord.ui.Select
    ]
    assert not bad, f"native select survived migration: {bad!r}"


async def _allow(*args: Any, **kwargs: Any) -> bool:
    return True


# ---------------------------------------------------------------------------
# Source scan — the migrated raw select classes are gone
# ---------------------------------------------------------------------------

MIGRATED_CLASSES: dict[str, tuple[str, ...]] = {
    "stoney_verify/commands_ext/public_setup_picker.py": ("ChannelPick", "RolePick"),
    "stoney_verify/commands_ext/public_setup_start.py": (
        "SaveRoleSelect", "SaveChannelSelect", "CategoryActionSelect",
    ),
    "stoney_verify/commands_ext/public_setup_solid.py": (
        "SaveRoleSelect", "SaveChannelSelect", "VerificationModeSelect", "CategorySelect",
    ),
    "stoney_verify/commands_ext/public_setup_full_customization.py": (
        "SaveRoleSelect", "SaveChannelSelect",
    ),
    "stoney_verify/commands_ext/public_setup_recommend.py": (
        "SetupChoiceSelect", "GuidedExistingRoleSelect", "GuidedExistingChannelSelect",
    ),
    "stoney_verify/commands_ext/public_setup_cleanup.py": ("RemoveOneSelect",),
    "stoney_verify/commands_ext/public_setup_find.py": ("SetupSearchSelect",),
    "stoney_verify/commands_ext/public_setup_fresh_choice.py": (
        "CustomServicePresetSelect", "SetupTypeChoiceSelect",
    ),
    "stoney_verify/setup_020_entitled_id_guard.py": ("_CustomServicePresetSelect",),
    "stoney_verify/setup_ui/public_setup_compact.py": ("FeatureAreaSelect", "TestAreaSelect"),
    "stoney_verify/startup_guards/setup_category_modal_compat.py": ("CategoryActionSelect",),
    "stoney_verify/startup_guards/setup_channel_font_mode_guard.py": ("FontStyleSelect",),
    "stoney_verify/startup_guards/setup_service_modes.py": ("ServicePresetSelect",),
}


def test_migrated_raw_select_classes_are_gone() -> None:
    for rel, class_names in MIGRATED_CLASSES.items():
        text = (ROOT / rel).read_text(encoding="utf-8")
        for name in class_names:
            assert not re.search(rf"^class {re.escape(name)}\b", text, re.M), (
                f"{rel}: raw select class {name} survived the migration"
            )
        for base in ("discord.ui.Select", "discord.ui.RoleSelect", "discord.ui.ChannelSelect"):
            assert not re.search(rf"class \w+\({re.escape(base)}\)", text), (
                f"{rel}: a class still subclasses {base}"
            )


# ---------------------------------------------------------------------------
# Host panels construct with openers and no native selects
# ---------------------------------------------------------------------------


def test_start_host_views_have_openers_not_selects() -> None:
    from stoney_verify.commands_ext import public_setup_start as start

    for view in (
        start.TicketBasicsPickerView(),
        start.VerificationRolesPickerView(),
        start.VerificationChannelsPickerView(),
        start.LogsStatusPickerView(),
        start.CategorySelectActionView(
            rows=[{"slug": "general", "name": "General", "intake_type": "general"}],
            action="edit",
        ),
    ):
        no_native_selects(view)


def test_solid_host_views_have_openers_not_selects() -> None:
    from stoney_verify.commands_ext import public_setup_solid as solid

    rows = [{"slug": "general", "name": "General", "intake_type": "custom"}]
    views: list[Any] = [
        solid.TicketBasicsPickerView(),
        solid.AccessRolesPickerView(),
        solid.VerificationChannelsPickerView(),
        solid.AdvancedLogsPickerView(),
        solid.LogsStatusPickerView(),
        solid.BehaviorSettingsView(),
    ]
    # In production the ticket category setup guard owns the
    # CategoryManagerView name (CategorySetupManagerView, Slice 4's
    # surface); only assert on the native manager when it is installed.
    if solid.CategoryManagerView.__name__ == "CategoryManagerView":
        views.append(solid.CategoryManagerView(rows=rows))
    for view in views:
        no_native_selects(view)


def test_full_customization_views_have_openers_not_selects() -> None:
    from stoney_verify.commands_ext import public_setup_full_customization as full

    for view in (
        full.RoleCustomizationPageOne(parent="features"),
        full.RoleCustomizationPageTwo(parent="features"),
        full.DiscordCategoryCustomizationView(parent="features"),
        full.ChannelCustomizationPageOne(parent="features"),
        full.ChannelCustomizationPageTwo(parent="features"),
        full.LogStatusCustomizationView(parent="features"),
    ):
        no_native_selects(view)


def test_recommend_views_have_openers_not_selects() -> None:
    from stoney_verify.commands_ext import public_setup_recommend as recommend

    no_native_selects(recommend.SetupChoiceView())
    role_view = recommend.GuidedOneItemView(requirement_key="ticket_staff_role")
    no_native_selects(role_view)
    opener = child_by_custom_id(role_view, "dank_setup_guided_existing_role:ticket_staff_role")
    assert isinstance(opener, discord.ui.Button)


def test_guard_and_shim_views_have_openers_not_selects() -> None:
    from stoney_verify.startup_guards import setup_channel_font_mode_guard as font_guard
    from stoney_verify.startup_guards import setup_service_modes as service_modes

    font_view = font_guard.ChannelFontModeView(
        {"unicodeStyle": "normal", "unicodeStyleScope": "whole_name"}
    )
    no_native_selects(font_view)
    child_by_custom_id(font_view, "dank_setup_font:style:open:v1")

    state = service_modes.ServiceState(
        tickets=True, verification=False, voice=False, spamguard=False, moderation=True
    )
    no_native_selects(service_modes.ServiceModeView(state))


# ---------------------------------------------------------------------------
# Routing parity — public_setup_start
# ---------------------------------------------------------------------------


def test_start_role_pick_saves_same_columns(monkeypatch: pytest.MonkeyPatch) -> None:
    from stoney_verify.commands_ext import public_setup_start as start

    saved: list[dict] = []

    async def fake_save(interaction: Any, payload: Any) -> None:
        saved.append(dict(payload))

    monkeypatch.setattr(start, "_require_setup_permission", _allow)
    monkeypatch.setattr(start, "_save_config", fake_save)

    role = make_role(1)
    guild = FakeGuild(roles=[role])
    view = start.TicketBasicsPickerView()
    opener = child_by_custom_id(view, "dank:setup_start:pick:staff_role_id:open:v1")
    picker = open_picker(view, opener, guild=guild)

    pick = FakeInteraction(guild=guild)
    run(picker.handle_pick(pick, str(role.id)))
    assert saved == [{"staff_role_id": str(role.id), "vc_staff_role_id": str(role.id)}]
    embed = pick.response.sent[0][1]["embed"]
    assert embed.title == "✅ Saved Setup Role"


def test_start_category_edit_pick_opens_modal(monkeypatch: pytest.MonkeyPatch) -> None:
    from stoney_verify.commands_ext import public_setup_start as start

    monkeypatch.setattr(start, "_require_setup_permission", _allow)
    rows = [{"slug": "general", "name": "General", "intake_type": "general", "is_default": True}]
    view = start.CategorySelectActionView(rows=rows, action="edit")
    opener = child_by_custom_id(view, "dank:setup_start:category_action:edit:open:v1")
    picker = open_picker(view, opener, guild=FakeGuild())

    pick = FakeInteraction(guild=FakeGuild())
    run(picker.handle_pick(pick, "general"))
    assert len(pick.response.modals) == 1


def test_start_channel_choices_page_past_25() -> None:
    from stoney_verify.commands_ext import public_setup_start as start

    channels = [make_channel(i) for i in range(40)]
    guild = FakeGuild(channels=channels)
    choices = start._start_channel_choices(guild, [discord.ChannelType.text])
    assert len(choices) == 40

    async def _noop(interaction: Any, value: str) -> None:
        return None

    picker = start._StartPickerView(
        choices=choices, on_pick=_noop, custom_id="dank:setup_start:pick:x:v1",
        placeholder="Pick", title="Pick",
    )
    assert picker.pages == 2
    page2 = picker._for_page(1)
    assert str(page2.children[0].custom_id).endswith(":1")
    labels = [str(o.label) for o in select_of(page2).options]
    assert "#chan-39" in labels


# ---------------------------------------------------------------------------
# Routing parity — public_setup_solid
# ---------------------------------------------------------------------------


def test_solid_verification_mode_pick_saves_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    from stoney_verify.commands_ext import public_setup_solid as solid

    saved: list[dict] = []
    shown: list[str] = []

    async def fake_save(interaction: Any, payload: Any) -> None:
        saved.append(dict(payload))

    async def fake_shown(interaction: Any, **kwargs: Any) -> None:
        shown.append(str(kwargs.get("title")))

    monkeypatch.setattr(solid, "_require_setup_permission", _allow)
    monkeypatch.setattr(solid, "_save_config", fake_save)
    monkeypatch.setattr(solid, "_show_saved_section_screen", fake_shown)

    view = solid.BehaviorSettingsView()
    opener = child_by_custom_id(view, "dank:setup_solid:verification_mode:open:v1")
    picker = open_picker(view, opener, guild=FakeGuild())
    values = [str(c.value) for c in picker._all_choices]
    assert values == [
        "none", "single_approved_role", "waiting_to_approved",
        "waiting_to_approved_to_member", "custom",
    ]
    run(picker.handle_pick(FakeInteraction(guild=FakeGuild()), "custom"))
    assert saved == [{"verification_mode": "custom"}]
    assert shown == ["✅ Saved Access Role Style"]


def test_solid_category_default_pick_writes_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from stoney_verify.commands_ext import public_setup_solid as solid

    writes: list[dict] = []

    async def fake_write(guild: Any, item: Any, *, set_default: bool = False) -> Any:
        writes.append(dict(item))
        return True, "ok"

    async def fake_payload(guild: Any, *, title: str = "") -> Any:
        return discord.Embed(title=title), solid.SetupNavView()

    monkeypatch.setattr(solid, "_require_setup_permission", _allow)
    monkeypatch.setattr(solid, "_write_category", fake_write)
    monkeypatch.setattr(solid, "_build_category_manager_payload", fake_payload)

    rows = [
        {"slug": "general", "name": "General", "intake_type": "custom"},
        {"slug": "support", "name": "Support", "intake_type": "custom"},
    ]
    # Construct the opener directly: the CategoryManagerView module
    # name is owned by the ticket category guard in production.
    opener = solid._CategoryPickOpenButton(
        rows, action="default",
        label="Choose default fallback ticket choice", emoji="⭐", row=1,
    )
    picker = open_picker(None, opener, guild=FakeGuild())
    run(picker.handle_pick(FakeInteraction(guild=FakeGuild()), "support"))
    assert writes and writes[0]["slug"] == "support" and writes[0]["is_default"] is True


def test_solid_category_choices_never_truncate() -> None:
    from stoney_verify.commands_ext import public_setup_solid as solid

    rows = [
        {"slug": f"cat-{i}", "name": f"Cat {i}", "intake_type": "custom"}
        for i in range(30)
    ]
    choices = solid._solid_category_choices(rows, placeholder="pick")
    assert len(choices) == 30
    opener = solid._CategoryPickOpenButton(
        rows, action="edit",
        label="Edit an existing ticket choice", emoji="✏️", row=0,
    )
    picker = open_picker(None, opener, guild=FakeGuild())
    assert picker.pages == 2
    page2_values = [str(o.value) for o in select_of(picker._for_page(1)).options]
    assert "cat-29" in page2_values


def test_solid_role_browser_pick_saves_role(monkeypatch: pytest.MonkeyPatch) -> None:
    from stoney_verify.commands_ext import public_setup_solid as solid
    from stoney_verify.ui import DankGuildResourceBrowserView

    saved: list[dict] = []

    async def fake_save(interaction: Any, payload: Any) -> None:
        saved.append(dict(payload))

    async def fake_shown(interaction: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(solid, "_require_setup_permission", _allow)
    monkeypatch.setattr(solid, "_save_config", fake_save)
    monkeypatch.setattr(solid, "_show_saved_section_screen", fake_shown)
    monkeypatch.setattr(solid, "_can_manage_role", lambda guild, bot, role: (True, ""))

    role = make_role(3)
    guild = FakeGuild(roles=[role])
    view = solid.TicketBasicsPickerView()
    opener = child_by_custom_id(view, "dank_setup:role:staff_role_id")
    browser = open_picker(view, opener, guild=guild)
    assert isinstance(browser, DankGuildResourceBrowserView)
    assert str(browser.resource_custom_id).startswith("dank_setup:role_browser")

    pick = FakeInteraction(guild=guild)
    run(browser.handle_pick(pick, browser.choices[0].value))
    assert saved == [{"staff_role_id": str(role.id), "vc_staff_role_id": str(role.id)}]


# ---------------------------------------------------------------------------
# Routing parity — public_setup_full_customization (resource browser)
# ---------------------------------------------------------------------------


def test_full_channel_browser_pick_saves_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    from stoney_verify.commands_ext import public_setup_full_customization as full
    from stoney_verify.ui import DankGuildResourceBrowserView

    saved: list[tuple[dict, str]] = []

    async def fake_save(interaction: Any, payload: Any, *, source: str = "") -> None:
        saved.append((dict(payload), source))

    async def fake_sent(interaction: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(full, "_require_setup_permission", _allow)
    monkeypatch.setattr(full, "_save_config", fake_save)
    monkeypatch.setattr(full, "_send_saved", fake_sent)
    monkeypatch.setattr(full, "_channel_warnings", lambda guild, channel, need_files=False: (True, []))

    channel = make_channel(5)
    guild = FakeGuild(channels=[channel])
    view = full.ChannelCustomizationPageOne(parent="features")
    openers = [
        c for c in view.children
        if str(getattr(c, "custom_id", "") or "").startswith("dank_full:channel:")
    ]
    assert openers
    opener = openers[0]
    browser = open_picker(view, opener, guild=guild)
    assert isinstance(browser, DankGuildResourceBrowserView)

    pick = FakeInteraction(guild=guild)
    run(browser.handle_pick(pick, browser.choices[0].value))
    assert saved
    payload, source = saved[0]
    assert payload == {column: str(channel.id) for column in opener.columns + opener.also_same}
    assert "channel picker" in source


def test_full_role_browser_pick_saves_role(monkeypatch: pytest.MonkeyPatch) -> None:
    from stoney_verify.commands_ext import public_setup_full_customization as full

    saved: list[tuple[dict, str]] = []

    async def fake_save(interaction: Any, payload: Any, *, source: str = "") -> None:
        saved.append((dict(payload), source))

    async def fake_sent(interaction: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(full, "_require_setup_permission", _allow)
    monkeypatch.setattr(full, "_save_config", fake_save)
    monkeypatch.setattr(full, "_send_saved", fake_sent)
    monkeypatch.setattr(full, "_role_manage_warning", lambda guild, role, require_manage=True: (True, []))

    role = make_role(2)
    guild = FakeGuild(roles=[role])
    view = full.RoleCustomizationPageOne(parent="features")
    opener = child_by_custom_id(view, "dank_full:role:server_control_role_id")
    browser = open_picker(view, opener, guild=guild)
    pick = FakeInteraction(guild=guild)
    run(browser.handle_pick(pick, browser.choices[0].value))
    assert saved
    payload, source = saved[0]
    assert payload == {column: str(role.id) for column in opener.columns + opener.also_same}
    assert "role picker" in source


# ---------------------------------------------------------------------------
# Routing parity — public_setup_recommend
# ---------------------------------------------------------------------------


def test_recommend_template_pick_rerenders_choice_view(monkeypatch: pytest.MonkeyPatch) -> None:
    from stoney_verify.commands_ext import public_setup_recommend as recommend
    from stoney_verify.commands_ext import public_setup_solid as solid

    monkeypatch.setattr(solid, "_require_setup_permission", _allow)

    view = recommend.SetupChoiceView()
    opener = child_by_custom_id(view, "dank_setup_choice:open:v1")
    picker = open_picker(view, opener, guild=FakeGuild())
    first = picker._all_choices[0]

    pick = FakeInteraction(guild=FakeGuild())
    run(picker.handle_pick(pick, str(first.value)))
    new_view = pick.response.edits[-1]["view"]
    assert isinstance(new_view, recommend.SetupChoiceView)
    assert new_view.selected_key == str(first.value)
    assert pick.response.edits[-1]["embed"] is not None


def test_guided_existing_role_browser_pick_saves(monkeypatch: pytest.MonkeyPatch) -> None:
    from stoney_verify.commands_ext import public_setup_recommend as recommend
    from stoney_verify.commands_ext import public_setup_solid as solid
    from stoney_verify.ui import DankGuildResourceBrowserView

    calls: list[tuple[str, Any]] = []

    async def fake_save_item(interaction: Any, requirement_key: str, item: Any) -> None:
        calls.append((requirement_key, item))

    monkeypatch.setattr(solid, "_require_setup_permission", _allow)
    monkeypatch.setattr(recommend, "_guided_save_existing_item", fake_save_item)

    role = make_role(7)
    guild = FakeGuild(roles=[role])
    view = recommend.GuidedOneItemView(requirement_key="ticket_staff_role")
    opener = child_by_custom_id(view, "dank_setup_guided_existing_role:ticket_staff_role")
    browser = open_picker(view, opener, guild=guild)
    assert isinstance(browser, DankGuildResourceBrowserView)

    pick = FakeInteraction(guild=guild)
    run(browser.handle_pick(pick, browser.choices[0].value))
    assert calls == [("ticket_staff_role", role)]


# ---------------------------------------------------------------------------
# Routing parity — public_setup_fresh_choice + the 020 entitled guard
# ---------------------------------------------------------------------------


def _fresh_state() -> Any:
    from stoney_verify.setup_service_state import SetupServiceState

    return SetupServiceState(
        setup_choice="custom_setup",
        setup_label="Custom",
        tickets=True,
        simple_verify=False,
        voice_verify=False,
        id_verify=False,
        spam_guard=False,
        logs=True,
    )


def test_fresh_preset_pick_saves_preset_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    from stoney_verify import setup_020_entitled_id_guard as guard
    from stoney_verify.commands_ext import public_setup_fresh_choice as fresh
    from stoney_verify.commands_ext import public_setup_solid as solid

    # The 020 entitled guard replaces fresh's preset opener when it is
    # installed (production state); patch both layers so the routing
    # assertion holds whichever opener the view carries.
    saved: list[dict] = []

    async def fake_save_fresh(guild_id: int, flags: Any, actor: Any) -> None:
        saved.append(dict(flags))

    async def fake_save_guard(guild_id: int, flags: Any, actor: Any, *, allow_id_verify: bool = False) -> None:
        saved.append(dict(flags))

    async def fake_load(guild_id: int) -> Any:
        return _fresh_state()

    async def fake_edit(interaction: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(solid, "_require_setup_permission", _allow)
    monkeypatch.setattr(solid, "_edit_or_followup", fake_edit)
    monkeypatch.setattr(fresh, "_save_custom_services", fake_save_fresh)
    monkeypatch.setattr(fresh, "load_setup_service_state", fake_load)
    monkeypatch.setattr(fresh, "_custom_services_embed", lambda guild, state, **kwargs: discord.Embed(title="x"))
    monkeypatch.setattr(guard, "_save_custom_services", fake_save_guard)
    monkeypatch.setattr(guard, "load_setup_service_state", fake_load)

    guild = FakeGuild()
    view = fresh.CustomServiceModeView(_fresh_state())
    opener = child_by_custom_id(view, "dank_setup_custom:preset:open:v1")
    picker = open_picker(view, opener, guild=guild)
    values = [str(c.value) for c in picker._all_choices]
    assert "tickets" in values

    pick = FakeInteraction(guild=guild)
    run(picker.handle_pick(pick, "tickets"))
    expected = fresh.CUSTOM_PRESETS["tickets"][1]
    assert saved == [dict(expected)]

    if "__custom_current__" in values:
        # Native opener only: re-picking the current mix is a no-op.
        saved.clear()
        run(picker.handle_pick(FakeInteraction(guild=guild), "__custom_current__"))
        assert saved == []


def test_fresh_setup_type_pick_routes_to_save(monkeypatch: pytest.MonkeyPatch) -> None:
    from stoney_verify.commands_ext import public_setup_fresh_choice as fresh

    calls: list[Any] = []

    async def fake_save_and_show(self: Any, interaction: Any, choice: Any) -> None:
        calls.append(choice)

    monkeypatch.setattr(fresh.SetupTypeChoiceView, "_save_and_show", fake_save_and_show)

    guild = FakeGuild()
    view = fresh.SetupTypeChoiceView(guild=guild)
    opener = child_by_custom_id(view, "dank_setup_plans:choose:open:v1")
    picker = open_picker(view, opener, guild=guild)
    values = [str(c.value) for c in picker._all_choices]
    # ID-check plans are only offered to entitled guilds; this fake
    # guild is not entitled, so they are filtered out of the choices.
    assert values == [
        key for key in fresh.CHOICES_BY_KEY
        if key not in {"id_check", "id_voice_check"}
    ]

    pick = FakeInteraction(guild=guild)
    run(picker.handle_pick(pick, "custom_setup"))
    assert len(calls) == 1
    assert calls[0] is fresh.CHOICES_BY_KEY["custom_setup"]

    calls.clear()
    run(picker.handle_pick(FakeInteraction(guild=guild), "does_not_exist"))
    assert calls == []


def test_020_guard_preset_choices_are_entitlement_filtered(monkeypatch: pytest.MonkeyPatch) -> None:
    from stoney_verify import setup_020_entitled_id_guard as guard
    from stoney_verify.commands_ext import public_setup_fresh_choice as fresh
    from stoney_verify.commands_ext import public_setup_solid as solid

    saved: list[tuple[int, dict, Any, bool]] = []

    async def fake_save(guild_id: int, flags: Any, actor: Any, *, allow_id_verify: bool = False) -> None:
        saved.append((guild_id, dict(flags), actor, allow_id_verify))

    async def fake_load(guild_id: int) -> Any:
        return _fresh_state()

    async def fake_edit(interaction: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(solid, "_require_setup_permission", _allow)
    monkeypatch.setattr(guard, "_save_custom_services", fake_save)
    monkeypatch.setattr(guard, "load_setup_service_state", fake_load)
    monkeypatch.setattr(solid, "_edit_or_followup", fake_edit)
    monkeypatch.setattr(fresh, "_custom_services_embed", lambda guild, state, **kwargs: discord.Embed(title="x"))
    # In production the guard replaces fresh.CustomServiceModeView with
    # its own guild-aware subclass; mirror that here.
    monkeypatch.setattr(
        fresh, "CustomServiceModeView",
        lambda state, guild=None: discord.ui.View(),
    )

    guild = FakeGuild(guild_id=123)
    opener = guard._CustomServicePresetOpenButton(_fresh_state(), fresh=fresh, guild=guild)
    picker = open_picker(None, opener, guild=guild)
    values = [str(c.value) for c in picker._all_choices]
    assert "id_verify" not in values and "id_voice_verify" not in values
    assert str(picker.children[0].custom_id) == "dank_setup_custom_service_preset:v6"

    pick = FakeInteraction(guild=guild)
    run(picker.handle_pick(pick, "tickets"))
    assert saved and saved[0][3] is False
    assert saved[0][1] == dict(fresh.CUSTOM_PRESETS["tickets"][1])


def test_020_guard_patches_fresh_opener() -> None:
    # The guard's install replaces fresh's preset opener with its
    # entitlement-filtered variant (see install function patch site).
    text = (ROOT / "stoney_verify/setup_020_entitled_id_guard.py").read_text(encoding="utf-8")
    assert "fresh._CustomPresetOpenButton = lambda state, guild=None: _CustomServicePresetOpenButton" in text
    assert "fresh.CustomServicePresetSelect" not in text


# ---------------------------------------------------------------------------
# Routing parity — startup-guard shims
# ---------------------------------------------------------------------------


def test_service_modes_preset_pick_saves_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    from stoney_verify.startup_guards import setup_service_modes as modes

    saved: list[tuple[int, dict]] = []
    state = modes.ServiceState(
        tickets=False, verification=False, voice=False, spamguard=False, moderation=False
    )

    async def fake_save(guild_id: int, payload: Any, actor: Any = None) -> None:
        saved.append((guild_id, dict(payload)))

    async def fake_load(guild_id: int) -> Any:
        return state

    async def fake_embed(guild: Any, state_arg: Any, *, saved_message: str = "") -> Any:
        return discord.Embed(title="Services", description=saved_message)

    monkeypatch.setattr(modes, "_save_service_state", fake_save)
    monkeypatch.setattr(modes, "load_service_state", fake_load)
    monkeypatch.setattr(modes, "build_service_picker_embed", fake_embed)

    guild = FakeGuild()
    view = modes.ServiceModeView(state)
    opener = child_by_custom_id(view, "dank_setup_service_modes:preset:open:v1")
    picker = open_picker(view, opener, guild=guild)
    pick = FakeInteraction(guild=guild)
    run(picker.handle_pick(pick, "tickets"))
    assert saved == [(guild.id, dict(modes.PRESETS["tickets"][1]))]
    assert pick.original_edits


def test_font_style_pick_saves_style(monkeypatch: pytest.MonkeyPatch) -> None:
    from stoney_verify.startup_guards import setup_channel_font_mode_guard as font_guard

    saved: list[dict] = []

    async def fake_load(guild_id: int) -> dict:
        return {"unicodeStyle": "normal", "unicodeStyleScope": "whole_name"}

    async def fake_save(guild_id: int, options: Any) -> dict:
        saved.append(dict(options))
        return dict(options)

    async def fake_embed(guild_id: int, **kwargs: Any) -> Any:
        return discord.Embed(title="Fonts")

    monkeypatch.setattr(font_guard, "_require_setup", _allow)
    monkeypatch.setattr(font_guard, "load_channel_font_options", fake_load)
    monkeypatch.setattr(font_guard, "save_channel_font_options", fake_save)
    monkeypatch.setattr(font_guard, "build_channel_font_embed", fake_embed)

    view = font_guard.ChannelFontModeView(
        {"unicodeStyle": "normal", "unicodeStyleScope": "whole_name"}
    )
    opener = child_by_custom_id(view, "dank_setup_font:style:open:v1")
    picker = open_picker(view, opener, guild=FakeGuild())
    assert len(picker._all_choices) == len(font_guard._STYLE_LABELS)

    style_key = [k for k in font_guard._STYLE_LABELS if k != "normal"][0]
    pick = FakeInteraction(guild=FakeGuild())
    run(picker.handle_pick(pick, style_key))
    assert saved and saved[0]["unicodeStyle"] == style_key
    assert isinstance(pick.response.edits[-1]["view"], font_guard.ChannelFontModeView)


def test_category_compat_picker_pages_and_opens_modal(monkeypatch: pytest.MonkeyPatch) -> None:
    from stoney_verify.commands_ext import public_setup_solid as solid
    from stoney_verify.startup_guards import setup_category_modal_compat as compat

    monkeypatch.setattr(compat, "_require_setup_permission_best_effort", _allow)

    rows = [
        {"slug": f"cat-{i}", "name": f"Cat {i}", "intake_type": "general"}
        for i in range(30)
    ]
    view_cls = getattr(solid, "CategorySelectActionView")
    view = view_cls(rows=rows, action="edit")
    opener = child_by_custom_id(view, "dank_setup_category_compat:action:edit:open:v1")
    picker = open_picker(view, opener, guild=FakeGuild())
    assert picker.pages == 2
    page2_values = [str(o.value) for o in select_of(picker._for_page(1)).options]
    assert "cat-29" in page2_values

    pick = FakeInteraction(guild=FakeGuild())
    run(picker.handle_pick(pick, "cat-3"))
    assert len(pick.response.modals) == 1


# ---------------------------------------------------------------------------
# Routing parity — compact area pickers
# ---------------------------------------------------------------------------


def test_compact_area_pick_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    from stoney_verify.setup_ui import public_setup_compact as compact

    routed: list[str] = []

    async def fake_route(interaction: Any, value: str) -> None:
        routed.append(value)

    monkeypatch.setattr(compact, "_route_area", fake_route)

    view = compact.CompactManagerView()
    opener = child_by_custom_id(view, "dank_setup_compact:area:open:v1")
    interaction = FakeInteraction(guild=FakeGuild())
    run(opener.callback(interaction))
    picker = interaction.response.edits[-1]["view"]
    labels = [str(c.label) for c in picker._all_choices]
    assert labels == [label for _value, label, _emoji, _desc in compact.FEATURE_AREAS]

    first_value = str(picker._all_choices[0].value)
    run(picker.handle_pick(FakeInteraction(guild=FakeGuild()), first_value))
    assert routed == [first_value]


def test_compact_test_area_pick_opens_feature_test(monkeypatch: pytest.MonkeyPatch) -> None:
    from stoney_verify.setup_ui import public_setup_compact as compact
    from stoney_verify.setup_ui import public_setup_guided_test as guided_test

    opened: list[str] = []

    async def fake_open(interaction: Any, state: Any, confirmed: Any, key: str) -> None:
        opened.append(key)

    monkeypatch.setattr(compact, "_open_feature_test", fake_open)

    state = {
        "tickets": True, "verification": False, "basic_verify": False,
        "voice_verify": False, "id_verify": False, "spam_guard": False,
        "logs": False, "completed": False,
    }
    # GuidedTestView is the production-resolved CompactTestView; its
    # jump opener routes through compact._open_test_area_picker.
    view = guided_test.GuidedTestView(state)
    opener = child_by_custom_id(view, "dank_setup_guided_test:jump:open:v1")
    interaction = FakeInteraction(guild=FakeGuild())
    run(opener.callback(interaction))
    picker = interaction.response.edits[-1]["view"]
    assert [str(c.label) for c in picker._all_choices] == ["Tickets"]
    run(picker.handle_pick(FakeInteraction(guild=FakeGuild()), "tickets"))
    assert opened == ["tickets"]


# ---------------------------------------------------------------------------
# Routing parity — cleanup + find + the setup wizard
# ---------------------------------------------------------------------------


def test_cleanup_picker_pages_and_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    from stoney_verify.commands_ext import public_setup_cleanup as cleanup

    picked: list[str] = []

    async def fake_apply(interaction: Any, value: str) -> None:
        picked.append(value)

    monkeypatch.setattr(cleanup, "_apply_remove_one_pick", fake_apply)

    candidates = [
        cleanup.CleanupCandidate(
            kind="role", object_id=100 + i, name=f"r{i}", mention="",
            can_delete=True, reason="unused",
        )
        for i in range(30)
    ]
    view = cleanup.RemoveOneView(candidates)
    opener = child_by_custom_id(view, "dank:setup_cleanup:remove_one:open:v1")
    picker = open_picker(view, opener, guild=FakeGuild())
    assert picker.pages == 2
    page2_values = [str(o.value) for o in select_of(picker._for_page(1)).options]
    assert candidates[29].value in page2_values

    run(picker.handle_pick(FakeInteraction(guild=FakeGuild()), candidates[0].value))
    assert picked == [candidates[0].value]


def test_cleanup_none_pick_keeps_old_message(monkeypatch: pytest.MonkeyPatch) -> None:
    from stoney_verify.commands_ext import public_setup_cleanup as cleanup
    from stoney_verify.commands_ext import public_setup_solid as solid

    monkeypatch.setattr(solid, "_require_setup_permission", _allow)
    pick = FakeInteraction(guild=FakeGuild())
    run(cleanup._apply_remove_one_pick(pick, "__none__"))
    assert pick.response.sent[0][0][0] == "No removable setup items were found."


def test_find_picker_pages_and_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    from stoney_verify.commands_ext import public_setup_find as find

    monkeypatch.setattr(find, "_require_setup_permission", _allow)
    spec = next(iter(find.TARGETS.values()))
    matches = [
        SimpleNamespace(id=100 + i, name=f"match-{i}", mention=f"<#{100 + i}>")
        for i in range(30)
    ]
    view = find.SetupSearchResultView(owner_id=77, spec=spec, matches=matches)

    applied: list[int] = []

    async def fake_apply(interaction: Any, obj_id: int) -> None:
        applied.append(int(obj_id))

    view.apply = fake_apply  # type: ignore[method-assign]
    opener = child_by_custom_id(view, "dank:setup_find:result:open:v1")
    picker = open_picker(view, opener, guild=FakeGuild())
    assert picker.pages == 2

    page2 = picker._for_page(1)
    run(page2.handle_pick(FakeInteraction(guild=FakeGuild()), str(matches[29].id)))
    assert applied == [matches[29].id]


def test_wizard_pick_stages_state(monkeypatch: pytest.MonkeyPatch) -> None:
    from stoney_verify.commands_ext import public_setup_picker as wizard

    monkeypatch.setattr(wizard, "_require_setup_permission", _allow)
    channel = make_channel(9, ctype=discord.ChannelType.category)
    guild = FakeGuild(channels=[channel])
    view = wizard.TicketPicker(owner_id=77, cfg={})
    opener = child_by_custom_id(view, "ticket_category_id")
    picker = open_picker(view, opener, guild=guild)
    run(picker.handle_pick(FakeInteraction(guild=guild), str(channel.id)))
    assert int(view.state["ticket_category_id"]) == channel.id
