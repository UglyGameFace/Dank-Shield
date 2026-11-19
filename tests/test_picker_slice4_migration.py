"""Slice 4 picker migration (tickets) — routing parity, paging, source scan.

Felix's standing rule: every picker uses the house framework
(``stoney_verify/ui/picker.py``). Slice 4 migrated the ticket surfaces:
the Ticket Command Center (13 pickers), the contextual permission
repair channel picker, the ticket-category setup guard multi-select,
the two member-facing Create Ticket panels (class C), and the
in-ticket staff panels (class C).

These tests prove, for every migrated surface:
- the raw select classes are gone from the source files;
- host panels construct with house pickers / opener buttons;
- driving a picker routes to the same behavior the old select
  callback had (same saves, modals, and command invocations);
- lists longer than Discord's 25-option select limit stay fully
  reachable through chunked paging;
- class C surfaces keep their member/staff-shared fit: allow_anyone
  pickers with the permission checks inside the pick handler, stable
  custom_ids, and persistent-panel presses still routing after a
  simulated restart (a fresh persistent view instance).
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import discord

from stoney_verify.commands_ext import public_ticket_command_center as tcc
from stoney_verify.commands_ext import public_ticket_contextual_permission_repair as repair
from stoney_verify.commands_ext import public_ticket_panel_clean as panel_clean
from stoney_verify.commands_ext import public_tickettool_parity_polish as parity
from stoney_verify.startup_guards import ticket_category_setup_guard as guard
from stoney_verify.tickets_new import panel as ticket_panel

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
    def __init__(self, *, guild: Any = None, user_id: int = OWNER_ID, channel: Any = None) -> None:
        self.guild = guild
        self.guild_id = getattr(guild, "id", GUILD_ID) if guild is not None else GUILD_ID
        self.user = SimpleNamespace(id=user_id)
        self.channel = channel
        self.response = FakeResponse()
        self.followup = FakeFollowup()
        self.message = SimpleNamespace(embeds=[discord.Embed(title="Host Panel")])
        self.original_edits: list[dict] = []

    async def edit_original_response(self, *args: Any, **kwargs: Any) -> None:
        self.original_edits.append(kwargs)


def make_text_channel(index: int, *, guild: Any = None, category_id: int = 0) -> Any:
    channel = object.__new__(discord.TextChannel)
    channel.id = 5000 + index
    channel.name = f"ticket-{index:02}"
    channel.guild = guild
    channel.category_id = category_id
    return channel


def make_member(user_id: int) -> Any:
    member = object.__new__(discord.Member)
    member._user = SimpleNamespace(id=user_id)
    return member


def make_role(role_id: int, guild_id: int = GUILD_ID) -> Any:
    role = object.__new__(discord.Role)
    role.id = role_id
    role.guild = SimpleNamespace(id=guild_id)
    return role


class FakeGuild:
    def __init__(self, *, channels: Any = (), members: Any = None, roles: Any = None, guild_id: int = GUILD_ID) -> None:
        self.id = guild_id
        self.text_channels = list(channels)
        self._by_id = {int(c.id): c for c in channels}
        self._members = dict(members or {})
        self._roles = dict(roles or {})

    def get_channel(self, channel_id: int) -> Any:
        return self._by_id.get(int(channel_id))

    def get_member(self, user_id: int) -> Any:
        return self._members.get(int(user_id))

    def get_role(self, role_id: int) -> Any:
        return self._roles.get(int(role_id))


def child_by_custom_id(view: Any, custom_id: str) -> Any:
    return next(item for item in view.children if getattr(item, "custom_id", None) == custom_id)


def all_picker_values(picker: Any) -> list[str]:
    values: list[str] = []
    for page in range(picker.pages):
        page_view = picker._for_page(page)
        values.extend(str(choice.value) for choice in page_view.choices)
    return values


# ---------------------------------------------------------------------------
# Source scan — the migrated raw classes are gone
# ---------------------------------------------------------------------------

_SLICE4_FILES = {
    "stoney_verify/commands_ext/public_ticket_command_center.py": [
        "TicketChannelPicker", "TicketActionSelect", "TicketMemberSelect",
        "TicketPrioritySelect", "TicketHistoryUserSelect",
        "TicketOperationsSectionSelect", "TicketNotificationCategorySelect",
        "TicketNotificationChannelSelect", "TicketPanelChannelSelect",
        "TicketIntakeActionSelect", "IntakeActionsChannelSelect",
        "TicketCategoryActionSelect",
    ],
    "stoney_verify/commands_ext/public_ticket_contextual_permission_repair.py": [
        "ContextualTicketChannelPicker",
    ],
    "stoney_verify/commands_ext/public_ticket_panel_clean.py": ["TicketSelect"],
    "stoney_verify/commands_ext/public_tickettool_parity_polish.py": ["TicketCategorySelect"],
    "stoney_verify/tickets_new/panel.py": ["MacroSelect", "TicketActionSelect"],
    "stoney_verify/startup_guards/ticket_category_setup_guard.py": ["ManagedCategorySelection"],
}


def test_no_raw_select_classes_remain_in_slice4_files() -> None:
    raw_base = re.compile(
        r"class\s+\w+\(\s*(?:discord\.ui\.(?:Select|ChannelSelect|RoleSelect|UserSelect|MentionableSelect)"
        r"|center\.TicketChannelPicker)"
    )
    for rel, removed in _SLICE4_FILES.items():
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert not raw_base.search(text), f"raw select class remains in {rel}"
        assert "discord.ui.Select(" not in text, f"inline raw Select in {rel}"
        for name in removed:
            assert not re.search(rf"class\s+{name}\s*\(", text), f"{name} still defined in {rel}"


# ---------------------------------------------------------------------------
# Ticket Command Center — channel picker
# ---------------------------------------------------------------------------


def _center_with_guild(channel_count: int = 60) -> tuple[Any, FakeGuild, list]:
    guild = FakeGuild()
    channels = [make_text_channel(i, guild=guild) for i in range(channel_count)]
    guild.text_channels = channels
    guild._by_id = {int(c.id): c for c in channels}
    view = tcc.TicketActionCenterView(OWNER_ID)
    return view, guild, channels


def test_center_view_uses_opener_buttons_not_raw_selects() -> None:
    view = tcc.TicketActionCenterView(OWNER_ID)
    ids = {getattr(item, "custom_id", None) for item in view.children}
    assert "dank:ticket:center:channel:open:v1" in ids
    assert "dank:ticket:center:action:open:v1" in ids
    assert not any(isinstance(item, (discord.ui.Select, discord.ui.ChannelSelect)) for item in view.children)


def test_center_channel_picker_pages_cover_every_channel() -> None:
    view, guild, channels = _center_with_guild(60)
    opener = child_by_custom_id(view, "dank:ticket:center:channel:open:v1")
    picker = opener.build_picker(FakeInteraction(guild=guild))

    assert picker.pages == 3
    assert all_picker_values(picker) == [str(int(c.id)) for c in channels]
    first_select = picker.children[0]
    assert first_select.custom_id == "dank:ticket:center:channel:v1:0"
    assert first_select.options[0].label == "#ticket-00"


def test_center_channel_pick_sets_target_and_returns_home(monkeypatch) -> None:
    view, guild, channels = _center_with_guild(3)
    replaced: list[dict] = []

    async def fake_replace(interaction, **kwargs):
        replaced.append(kwargs)

    monkeypatch.setattr(tcc, "_replace", fake_replace)
    opener = child_by_custom_id(view, "dank:ticket:center:channel:open:v1")
    picker = opener.build_picker(FakeInteraction(guild=guild))
    run(picker.handle_pick(FakeInteraction(guild=guild), str(int(channels[2].id))))

    # The shown center targets the picked channel. (When the contextual
    # repair owner is installed it swaps in its subclass, whose pick
    # rebuilds the center view; either way the replacement view carries
    # the picked channel.)
    assert replaced and replaced[0]["view"].channel_id == int(channels[2].id)
    assert isinstance(replaced[0]["view"], tcc.TicketActionCenterView)
    assert replaced[0]["embed"].title == "🎫 Current Ticket"


# ---------------------------------------------------------------------------
# Ticket Command Center — action picker
# ---------------------------------------------------------------------------


def test_action_picker_choices_match_old_select_exactly() -> None:
    view = tcc.TicketActionCenterView(OWNER_ID)
    opener = child_by_custom_id(view, "dank:ticket:center:action:open:v1")
    picker = opener._build(FakeInteraction(guild=FakeGuild()))
    values = [choice.value for choice in picker.choices]
    labels = [choice.label for choice in picker.choices]
    assert values == [
        "info", "claim", "unclaim", "transfer", "priority", "close", "reopen",
        "transcript", "add", "remove", "rename", "lock", "unlock", "owner",
        "access", "delete",
    ]
    assert labels[0] == "Ticket Info" and labels[-1] == "Delete Ticket"
    assert picker.children[0].custom_id == "dank:ticket:center:action:v1"


def _dispatch_setup(monkeypatch, channel_count: int = 1):
    view, guild, channels = _center_with_guild(channel_count)
    view.channel_id = int(channels[0].id)
    return view, guild, channels


def test_action_dispatch_info_runs_ticket_command(monkeypatch) -> None:
    view, guild, channels = _dispatch_setup(monkeypatch)
    calls: list[dict] = []

    async def fake_run(interaction, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(tcc, "_run_ticket_command", fake_run)
    run(tcc._dispatch_ticket_action(FakeInteraction(guild=guild), view, "info"))
    assert calls and calls[0]["name"] == "info"
    assert calls[0]["channel"] is channels[0]


def test_action_dispatch_transfer_opens_member_view(monkeypatch) -> None:
    view, guild, channels = _dispatch_setup(monkeypatch)
    replaced: list[dict] = []

    async def fake_replace(interaction, **kwargs):
        replaced.append(kwargs)

    monkeypatch.setattr(tcc, "_replace", fake_replace)
    run(tcc._dispatch_ticket_action(FakeInteraction(guild=guild), view, "transfer"))
    assert isinstance(replaced[0]["view"], tcc.TicketMemberActionView)
    assert replaced[0]["view"].action == "transfer"


def test_action_dispatch_priority_opens_priority_picker(monkeypatch) -> None:
    view, guild, channels = _dispatch_setup(monkeypatch)
    replaced: list[dict] = []

    async def fake_replace(interaction, **kwargs):
        replaced.append(kwargs)

    monkeypatch.setattr(tcc, "_replace", fake_replace)
    run(tcc._dispatch_ticket_action(FakeInteraction(guild=guild), view, "priority"))
    assert isinstance(replaced[0]["view"], tcc.TicketPriorityView)


def test_action_dispatch_modal_actions(monkeypatch) -> None:
    view, guild, channels = _dispatch_setup(monkeypatch)
    for action, modal_type in (
        ("close", tcc.TicketReasonModal),
        ("rename", tcc.TicketRenameModal),
        ("delete", tcc.TicketDeleteModal),
    ):
        interaction = FakeInteraction(guild=guild)
        run(tcc._dispatch_ticket_action(interaction, view, action))
        assert len(interaction.response.modals) == 1
        assert isinstance(interaction.response.modals[0], modal_type)


# ---------------------------------------------------------------------------
# Ticket Command Center — member + priority + history pickers
# ---------------------------------------------------------------------------


def test_member_action_view_uses_house_user_select(monkeypatch) -> None:
    member = make_member(9001)
    channel = make_text_channel(0)
    guild = FakeGuild(channels=[channel], members={9001: member})
    channel.guild = guild
    view = tcc.TicketMemberActionView(owner_id=OWNER_ID, channel_id=int(channel.id), action="add")
    select = next(item for item in view.children if isinstance(item, tcc.DankUserSelect))
    assert select.placeholder == "Choose the member to add…"

    invoked: list[dict] = []

    async def fake_invoke(command, interaction, **kwargs):
        invoked.append(kwargs)

    async def allowed(*args, **kwargs):
        return True

    import stoney_verify.commands_ext.public_ticket_group as ticket_group_module

    monkeypatch.setattr(tcc, "_invoke", fake_invoke)
    monkeypatch.setattr(tcc, "_require_staff", allowed)
    monkeypatch.setattr(tcc, "_authorize_ticket_command", allowed)
    monkeypatch.setattr(
        ticket_group_module.ticket_group, "get_command",
        lambda name: SimpleNamespace(name=name),
    )
    select._values = [SimpleNamespace(id=9001)]
    run(select.callback(FakeInteraction(guild=guild)))
    assert invoked and invoked[0]["member"] is member
    assert invoked[0]["channel"] is channel


def test_priority_picker_pick_invokes_priority_command(monkeypatch) -> None:
    channel = make_text_channel(0)
    guild = FakeGuild(channels=[channel])
    channel.guild = guild
    view = tcc.TicketPriorityView(OWNER_ID, int(channel.id))
    assert isinstance(view, tcc.DankPickerView)
    assert [c.value for c in view.choices] == ["low", "medium", "high", "urgent"]

    invoked: list[dict] = []

    async def fake_invoke(command, interaction, **kwargs):
        invoked.append(kwargs)

    async def allowed(*args, **kwargs):
        return True

    import stoney_verify.commands_ext.public_ticket_group as ticket_group_module

    monkeypatch.setattr(tcc, "_invoke", fake_invoke)
    monkeypatch.setattr(tcc, "_require_staff", allowed)
    monkeypatch.setattr(tcc, "_authorize_ticket_command", allowed)
    monkeypatch.setattr(
        ticket_group_module.ticket_group, "get_command",
        lambda name: SimpleNamespace(name=name),
    )
    run(view.handle_pick(FakeInteraction(guild=guild), "high"))
    assert invoked and invoked[0]["priority"].value == "high"
    assert invoked[0]["priority"].name == "High"
    assert invoked[0]["channel"] is channel


def test_operations_history_picker_routes_to_for_user(monkeypatch) -> None:
    member = make_member(9002)
    guild = FakeGuild(members={9002: member})
    view = tcc.TicketOperationsView(OWNER_ID, section="queues")
    select = next(item for item in view.children if isinstance(item, tcc.DankUserSelect))

    invoked: list[dict] = []

    async def fake_invoke(command, interaction, **kwargs):
        invoked.append(kwargs)

    async def allowed(*args, **kwargs):
        return True

    import stoney_verify.commands_ext.public_tickets_group as tickets_group_module

    monkeypatch.setattr(tcc, "_invoke", fake_invoke)
    monkeypatch.setattr(tcc, "_require_staff", allowed)
    monkeypatch.setattr(
        tickets_group_module.tickets_group, "get_command",
        lambda name: SimpleNamespace(name=name),
    )
    select._values = [SimpleNamespace(id=9002)]
    run(select.callback(FakeInteraction(guild=guild)))
    assert invoked and invoked[0]["member"] is member


def test_operations_section_picker_switches_section(monkeypatch) -> None:
    replaced: list[dict] = []

    async def fake_replace(interaction, **kwargs):
        replaced.append(kwargs)

    async def allowed(*args, **kwargs):
        return True

    monkeypatch.setattr(tcc, "_replace", fake_replace)
    monkeypatch.setattr(tcc, "_require_staff", allowed)
    opener = tcc.TicketOperationsSectionOpenButton(OWNER_ID, "queues")
    picker = opener._build(FakeInteraction(guild=FakeGuild()))
    assert picker.children[0].custom_id == "dank:tickets:operations:section:v2"
    run(picker.handle_pick(FakeInteraction(guild=FakeGuild()), "setup"))
    assert isinstance(replaced[0]["view"], tcc.TicketOperationsView)
    assert replaced[0]["view"].section == "setup"


# ---------------------------------------------------------------------------
# Ticket Command Center — staff alert (notification) pickers
# ---------------------------------------------------------------------------


def _notification_view(categories: list[tuple[str, str]]) -> Any:
    return tcc.TicketNotificationSettingsView(OWNER_ID, categories)


def test_notification_category_picker_covers_all_categories(monkeypatch) -> None:
    categories = [(f"cat-{i}", f"Category {i}") for i in range(30)]
    view = _notification_view(categories)
    opener = child_by_custom_id(view, "dank:tickets:notifications:category:open:v1")
    picker = opener._build(FakeInteraction(guild=FakeGuild()))
    values = all_picker_values(picker)
    # No silent [:23] cap: every category plus All + Custom stays reachable.
    assert values[0] == "*"
    assert values[-1] == ":custom:"
    assert values[1:-1] == [slug for slug, _name in categories]
    assert picker.pages == 2


def test_notification_category_pick_sets_category(monkeypatch) -> None:
    view = _notification_view([("billing", "Billing")])
    replaced: list[dict] = []

    async def fake_replace(interaction, **kwargs):
        replaced.append(kwargs)

    async def allowed(*args, **kwargs):
        return True

    monkeypatch.setattr(tcc, "_replace", fake_replace)
    monkeypatch.setattr(tcc, "_require_ticket_notification_manager", allowed)
    opener = child_by_custom_id(view, "dank:tickets:notifications:category:open:v1")
    picker = opener._build(FakeInteraction(guild=FakeGuild()))
    run(picker.handle_pick(FakeInteraction(guild=FakeGuild()), "billing"))
    assert view.category == "billing"
    assert replaced and replaced[0]["view"] is view


def test_notification_category_custom_opens_modal(monkeypatch) -> None:
    async def allowed(*args, **kwargs):
        return True

    monkeypatch.setattr(tcc, "_require_ticket_notification_manager", allowed)
    view = _notification_view([])
    opener = child_by_custom_id(view, "dank:tickets:notifications:category:open:v1")
    picker = opener._build(FakeInteraction(guild=FakeGuild()))
    interaction = FakeInteraction(guild=FakeGuild())
    run(picker.handle_pick(interaction, ":custom:"))
    assert len(interaction.response.modals) == 1
    assert isinstance(interaction.response.modals[0], tcc.TicketNotificationCustomCategoryModal)


def test_notification_role_select_saves_all_picked_roles(monkeypatch) -> None:
    roles = [make_role(7001), make_role(7002)]
    guild = FakeGuild(roles={int(r.id): r for r in roles})
    view = _notification_view([])
    select = next(item for item in view.children if isinstance(item, tcc.TicketNotificationRoleSelect))
    assert isinstance(select, tcc.DankRoleSelect)
    assert select.max_values == 5

    saved: list[dict] = []

    async def fake_save(guild_id, patch):
        saved.append(patch)

    async def allowed(*args, **kwargs):
        return True

    import stoney_verify.tickets_new.open_notifications as notifications

    monkeypatch.setattr(notifications, "save_settings", fake_save)
    monkeypatch.setattr(tcc, "_require_ticket_notification_manager", allowed)
    select._values = [SimpleNamespace(id=7001), SimpleNamespace(id=7002)]
    interaction = FakeInteraction(guild=guild)
    interaction.response.defer = FakeResponse().defer  # keep simple defer stub
    run(select.callback(interaction))
    assert saved == [{"role_ids": [7001, 7002]}]


def test_notification_channel_pick_saves_alert_channel(monkeypatch) -> None:
    channel = make_text_channel(0)
    guild = FakeGuild(channels=[channel])
    channel.guild = guild
    view = _notification_view([])
    saved: list[tuple] = []

    async def fake_save_channel(interaction, picked, parent):
        saved.append((picked, parent))

    monkeypatch.setattr(tcc, "_save_ticket_staff_alert_channel", fake_save_channel)
    opener = child_by_custom_id(view, "dank:tickets:notifications:channel:open:v1")
    picker = opener._build(FakeInteraction(guild=guild))
    run(picker.handle_pick(FakeInteraction(guild=guild), str(int(channel.id))))
    assert saved and saved[0][0] is channel and saved[0][1] is view


# ---------------------------------------------------------------------------
# Ticket Command Center — panel / intake / category tool pickers
# ---------------------------------------------------------------------------


def test_panel_channel_pick_sets_panel_channel(monkeypatch) -> None:
    channel = make_text_channel(0)
    guild = FakeGuild(channels=[channel])
    channel.guild = guild
    view = tcc.TicketPanelToolsView(OWNER_ID)
    replaced: list[dict] = []

    async def fake_replace(interaction, **kwargs):
        replaced.append(kwargs)

    monkeypatch.setattr(tcc, "_replace", fake_replace)
    opener = child_by_custom_id(view, "dank:tickets:panel:channel:open:v1")
    picker = opener._build(FakeInteraction(guild=guild))
    run(picker.handle_pick(FakeInteraction(guild=guild), str(int(channel.id))))
    assert view.channel_id == int(channel.id)
    assert replaced and replaced[0]["view"] is view


def test_intake_action_dispatch(monkeypatch) -> None:
    view = tcc.TicketIntakeToolsView(OWNER_ID)
    invoked: list[Any] = []

    async def fake_invoke(command, interaction, **kwargs):
        invoked.append(command)

    monkeypatch.setattr(tcc, "_invoke", fake_invoke)
    run(tcc._dispatch_intake_action(FakeInteraction(guild=FakeGuild()), view, "categories"))
    from stoney_verify.commands_ext import public_ticket_intake_group as intake

    assert invoked == [intake.intake_categories]

    interaction = FakeInteraction(guild=FakeGuild())
    run(tcc._dispatch_intake_action(interaction, view, "match"))
    assert isinstance(interaction.response.modals[0], tcc.IntakeMatchModal)


def test_intake_post_actions_channel_pick_invokes(monkeypatch) -> None:
    channel = make_text_channel(0)
    guild = FakeGuild(channels=[channel])
    channel.guild = guild
    view = tcc.TicketIntakePostActionsView(OWNER_ID)
    invoked: list[dict] = []

    async def fake_invoke(command, interaction, **kwargs):
        invoked.append(kwargs)

    monkeypatch.setattr(tcc, "_invoke", fake_invoke)
    opener = child_by_custom_id(view, "dank:tickets:intake:post_actions:open:v1")
    picker = opener._build(FakeInteraction(guild=guild))
    run(picker.handle_pick(FakeInteraction(guild=guild), str(int(channel.id))))
    assert invoked and invoked[0]["channel"] is channel


def test_category_action_dispatch(monkeypatch) -> None:
    view = tcc.TicketCategoryToolsView(OWNER_ID)
    invoked: list[Any] = []

    async def fake_invoke(command, interaction, **kwargs):
        invoked.append(command)

    monkeypatch.setattr(tcc, "_invoke", fake_invoke)
    run(tcc._dispatch_category_action(FakeInteraction(guild=FakeGuild()), view, "sync"))
    from stoney_verify.commands_ext import public_ticket_category_group as categories

    assert invoked == [categories.category_sync]

    interaction = FakeInteraction(guild=FakeGuild())
    run(tcc._dispatch_category_action(interaction, view, "create"))
    assert isinstance(interaction.response.modals[0], tcc.CategoryCreateModal)

    interaction = FakeInteraction(guild=FakeGuild())
    run(tcc._dispatch_category_action(interaction, view, "delete"))
    modal = interaction.response.modals[0]
    assert isinstance(modal, tcc.CategorySlugModal) and modal.action == "delete"


# ---------------------------------------------------------------------------
# Contextual permission repair — pick rebuilds the contextual center
# ---------------------------------------------------------------------------


def test_contextual_center_has_contextual_opener_only() -> None:
    view = repair.ContextualTicketActionCenterView(OWNER_ID, channel_id=0, guild=None)
    openers = [
        item for item in view.children
        if getattr(item, "custom_id", None) == "dank:ticket:center:channel:open:v1"
    ]
    assert len(openers) == 1
    assert isinstance(openers[0], repair.ContextualTicketChannelPickOpenButton)


def test_contextual_channel_pick_rebuilds_contextual_view(monkeypatch) -> None:
    channel = make_text_channel(0)
    guild = FakeGuild(channels=[channel])
    channel.guild = guild
    view = repair.ContextualTicketActionCenterView(OWNER_ID, channel_id=0, guild=guild)
    opener = next(
        item for item in view.children
        if isinstance(item, repair.ContextualTicketChannelPickOpenButton)
    )
    replaced: list[dict] = []

    async def fake_replace(interaction, **kwargs):
        replaced.append(kwargs)

    monkeypatch.setattr(repair.center, "_replace", fake_replace)
    picker = opener.build_picker(FakeInteraction(guild=guild))
    run(picker.handle_pick(FakeInteraction(guild=guild), str(int(channel.id))))
    new_view = replaced[0]["view"]
    assert isinstance(new_view, repair.ContextualTicketActionCenterView)
    assert new_view.channel_id == int(channel.id)


# ---------------------------------------------------------------------------
# Ticket category setup guard — house multi-picker
# ---------------------------------------------------------------------------


def _guard_state(selected: tuple[str, ...] = ("support",)) -> Any:
    from stoney_verify.tickets_new import managed_category_service as categories

    rows = categories.catalog_category_rows()
    return categories.CategorySetupState(
        rows=rows,
        active_rows=[row for row in rows if row["category_key"] in set(selected)],
        selected_keys=selected,
        required=False,
        reason="",
        version=categories.CATEGORY_SETUP_VERSION,
    )


def test_guard_manager_view_opens_house_multi_picker() -> None:
    state = _guard_state()
    view = guard.CategorySetupManagerView(state=state)
    opener = child_by_custom_id(view, "dank_ticket_category_setup:managed_selection:open")
    assert isinstance(opener, guard.ManagedCategorySelectionOpenButton)

    picker = guard.ManagedCategorySelectionView(state, author_id=1)
    assert isinstance(picker, guard.DankMultiPickerView)
    assert len(picker.choices) == 16
    assert {c.value for c in picker.choices if c.default} == {"support"}
    select = picker.children[0]
    assert select.custom_id == "dank_ticket_category_setup:managed_selection"
    assert select.max_values == 16


def test_guard_multi_pick_saves_through_same_path(monkeypatch) -> None:
    state = _guard_state()
    picker = guard.ManagedCategorySelectionView(state, author_id=1)
    saved: list[Any] = []
    edits: list[dict] = []

    async def fake_allowed(interaction, *, context):
        return True

    async def fake_save(interaction, values, *, context):
        saved.append(list(values))
        return state

    async def fake_payload(guild, **kwargs):
        return discord.Embed(title="Saved"), SimpleNamespace()

    async def fake_edit(interaction, **kwargs):
        edits.append(kwargs)

    import stoney_verify.commands_ext.public_setup_solid as solid

    monkeypatch.setattr(guard, "_manager_allowed", fake_allowed)
    monkeypatch.setattr(guard, "_save_selection", fake_save)
    monkeypatch.setattr(guard, "_build_category_manager_payload", fake_payload)
    monkeypatch.setattr(solid, "_edit_or_followup", fake_edit)
    run(picker.handle_pick(FakeInteraction(guild=FakeGuild()), ["report", "support"]))
    assert saved == [["report", "support"]]
    assert edits and edits[0]["embed"].title == "Saved"


# ---------------------------------------------------------------------------
# Class C — member-facing Create Ticket panels (panel_clean)
# ---------------------------------------------------------------------------


def _panel_rows(count: int) -> list[dict]:
    return [
        {"slug": f"cat-{i}", "name": f"Category {i}", "description": f"Help {i}"}
        for i in range(count)
    ]


def test_panel_clean_picker_is_allow_anyone_house_picker() -> None:
    view = panel_clean.TicketSelectView(_panel_rows(3), OWNER_ID, "session-1")
    assert isinstance(view, panel_clean.DankPickerView)
    assert view.allow_anyone is True
    select = view.children[0]
    assert select.custom_id == "dank:tickets:panel_clean:ticket_type:v1"
    assert [c.value for c in view.choices] == ["cat-0", "cat-1", "cat-2"]
    # Stable id: a fresh instance (as after a restart) carries the same id.
    again = panel_clean.TicketSelectView(_panel_rows(3), OWNER_ID, "session-1")
    assert again.children[0].custom_id == select.custom_id


def test_panel_clean_picker_pages_all_categories() -> None:
    rows = _panel_rows(30)
    view = panel_clean.TicketSelectView(rows, OWNER_ID, "session-1")
    assert view.pages == 2
    values: list[str] = []
    for page in range(view.pages):
        values.extend(str(c.value) for c in view._for_page(page).choices)
    assert values == [row["slug"] for row in rows]


def test_panel_clean_pick_wrong_member_gets_stale_menu(monkeypatch) -> None:
    rows = _panel_rows(2)
    stale: list[Any] = []

    async def fake_stale(interaction):
        stale.append(interaction)

    monkeypatch.setattr(panel_clean, "_member_from_interaction", lambda i: SimpleNamespace(id=999))
    monkeypatch.setattr(panel_clean, "_stale_ticket_menu", fake_stale)
    view = panel_clean.TicketSelectView(rows, OWNER_ID, "session-1")
    run(view.handle_pick(FakeInteraction(guild=FakeGuild()), "cat-0"))
    assert len(stale) == 1


def test_panel_clean_pick_owner_reaches_confirm(monkeypatch) -> None:
    rows = _panel_rows(2)
    captured: list[dict] = []

    async def fake_edit(interaction, **kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(panel_clean, "_member_from_interaction", lambda i: SimpleNamespace(id=OWNER_ID))
    monkeypatch.setattr(panel_clean, "_menu_session_current", lambda *a: True)
    monkeypatch.setattr(panel_clean, "_edit_or_reply", fake_edit)
    view = panel_clean.TicketSelectView(rows, OWNER_ID, "session-1")
    run(view.handle_pick(FakeInteraction(guild=FakeGuild()), "cat-1"))
    assert captured and captured[0]["content"] == "Confirm this ticket type."
    confirm = captured[0]["view"]
    assert isinstance(confirm, panel_clean.TicketConfirmView)
    assert confirm.row["slug"] == "cat-1"


# ---------------------------------------------------------------------------
# Class C — public create panel (parity polish)
# ---------------------------------------------------------------------------


def test_parity_picker_is_allow_anyone_and_routes_to_create(monkeypatch) -> None:
    rows = _panel_rows(3)
    view = parity.TicketCategorySelectView(rows)
    assert isinstance(view, parity.DankPickerView)
    assert view.allow_anyone is True
    created: list[Any] = []

    async def fake_create(interaction, row):
        created.append(row)

    monkeypatch.setattr(parity, "_create_ticket_from_row", fake_create)
    run(view.handle_pick(FakeInteraction(guild=FakeGuild()), "cat-2"))
    assert created and created[0]["slug"] == "cat-2"


def test_parity_picker_empty_rows_fall_back_to_support() -> None:
    view = parity.TicketCategorySelectView([])
    assert [c.value for c in view.choices] == ["support"]


def test_parity_picker_pages_all_categories() -> None:
    rows = _panel_rows(30)
    view = parity.TicketCategorySelectView(rows)
    assert view.pages == 2
    values: list[str] = []
    for page in range(view.pages):
        values.extend(str(c.value) for c in view._for_page(page).choices)
    assert values == [row["slug"] for row in rows]


# ---------------------------------------------------------------------------
# Class C — in-ticket staff panels (tickets_new/panel.py)
# ---------------------------------------------------------------------------


def test_in_ticket_panel_is_persistent_with_opener_button() -> None:
    view = ticket_panel.TicketChannelActionsView()
    assert view.timeout is None
    opener = child_by_custom_id(view, "ticket_actions_more_open")
    assert isinstance(opener, ticket_panel.TicketMoreActionsOpenButton)
    assert not any(isinstance(item, discord.ui.Select) for item in view.children)


def test_in_ticket_more_actions_after_simulated_restart(monkeypatch) -> None:
    # A fresh persistent view instance, as registered by add_view on
    # ready, must still route a press: opener -> ephemeral house picker.
    view = ticket_panel.TicketChannelActionsView()
    opener = child_by_custom_id(view, "ticket_actions_more_open")
    interaction = FakeInteraction(guild=FakeGuild())
    run(opener.callback(interaction))
    assert len(interaction.response.sent) == 1
    picker = interaction.response.sent[0][1]["view"]
    assert isinstance(picker, ticket_panel.TicketMoreActionsPickerView)
    assert picker.allow_anyone is True
    assert picker.children[0].custom_id == "ticket_actions_more_select"
    assert [c.value for c in picker.choices] == [
        "transfer", "priority", "add_note", "view_notes", "list_macros", "use_macro",
    ]

    routed: list[str] = []

    async def fake_run(interaction, fn, label):
        routed.append(label)

    monkeypatch.setattr(ticket_panel, "_run_ticket_panel_action", fake_run)
    run(picker.handle_pick(FakeInteraction(guild=FakeGuild()), "transfer"))
    assert routed == ["transfer ticket"]


def _macro_rows(count: int) -> list[dict]:
    return [
        {"slug": f"macro-{i}", "name": f"Macro {i}", "description": f"Reply {i}"}
        for i in range(count)
    ]


def test_macro_picker_pages_and_keeps_custom_id_contract() -> None:
    view = ticket_panel.MacroPickerView(channel_id=3131, actor_id=OWNER_ID, macro_rows=_macro_rows(30))
    assert isinstance(view, ticket_panel.DankPickerView)
    assert view.allow_anyone is True
    assert view.children[0].custom_id.startswith("ticket_macro_select:3131:")
    assert view.pages == 2
    values: list[str] = []
    for page in range(view.pages):
        values.extend(str(c.value) for c in view._for_page(page).choices)
    assert values == [f"macro-{i}" for i in range(30)]


def test_macro_pick_wrong_actor_is_denied_with_old_wording(monkeypatch) -> None:
    channel = make_text_channel(0)
    channel.id = 3131
    followups: list[str] = []

    async def fake_followup(interaction, content=None, *args, **kwargs):
        followups.append(str(content or ""))

    monkeypatch.setattr(ticket_panel, "_resolve_member", lambda i: SimpleNamespace(id=999))
    monkeypatch.setattr(ticket_panel, "_safe_followup", fake_followup)
    interaction = FakeInteraction(guild=FakeGuild(), channel=channel)
    run(ticket_panel._apply_macro_pick(interaction, channel_id=3131, actor_id=OWNER_ID, slug_value="macro-0"))
    assert followups == ["Only the staff member who opened this picker can use it."]


def test_macro_pick_happy_path_sends_macro(monkeypatch) -> None:
    channel = make_text_channel(0)
    channel.id = 3131
    channel.guild = FakeGuild()
    member = SimpleNamespace(id=OWNER_ID)
    followups: list[str] = []

    async def fake_followup(interaction, content=None, *args, **kwargs):
        followups.append(str(content or ""))

    async def fake_row(_channel):
        return {}

    async def fake_defer(interaction):
        return None

    async def fake_send(**kwargs):
        return {"ok": True, "macro": {"name": "Welcome"}, "send_as_note": False}

    monkeypatch.setattr(ticket_panel, "_resolve_member", lambda i: member)
    monkeypatch.setattr(ticket_panel, "_is_staff_member", lambda m: True)
    monkeypatch.setattr(ticket_panel, "_ticket_row_for_channel", fake_row)
    monkeypatch.setattr(ticket_panel, "_ticket_is_deleted", lambda row: False)
    monkeypatch.setattr(ticket_panel, "_ticket_is_closed_or_stale_closed", lambda ch, row: False)
    monkeypatch.setattr(ticket_panel, "_safe_defer", fake_defer)
    monkeypatch.setattr(ticket_panel, "send_ticket_macro", fake_send)
    monkeypatch.setattr(ticket_panel, "_safe_followup", fake_followup)
    interaction = FakeInteraction(guild=FakeGuild(), channel=channel)
    run(ticket_panel._apply_macro_pick(interaction, channel_id=3131, actor_id=OWNER_ID, slug_value="macro-0"))
    assert followups == ["Sent macro **Welcome**."]
