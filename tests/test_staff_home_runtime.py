from __future__ import annotations

import asyncio
from types import SimpleNamespace

import discord

from stoney_verify import guild_config
from stoney_verify.commands_ext import public_command_hub as hub
from stoney_verify.commands_ext import public_command_surface_v2 as surface
from stoney_verify.commands_ext import public_home_status as home_status
from stoney_verify.commands_ext import public_protection_center as protection
from stoney_verify.tickets_new import repository as tickets


def _status(**overrides) -> home_status.HomeStatus:
    values = dict(
        guild_name="The Smoke Room",
        setup_done=True,
        protection="safe",
        open_tickets=0,
        pending_verification=0,
    )
    values.update(overrides)
    return home_status.HomeStatus(**values)


def _rows(view: discord.ui.View) -> dict[int, list[discord.ui.Item]]:
    rows: dict[int, list[discord.ui.Item]] = {}
    for item in view.children:
        rows.setdefault(int(item.row or 0), []).append(item)
    return rows


def _ids(view: discord.ui.View) -> list[str]:
    return [str(getattr(item, "custom_id", "")) for item in view.children]


# --- status model -----------------------------------------------------------


def test_healthy_server_has_nothing_needing_attention() -> None:
    status = _status()

    assert status.attention == []
    assert status.headline == "Everything looks good"
    assert (status.setup_label, status.protection_label, status.tickets_label) == ("Done", "On · Safe", "0")


def test_attention_lists_real_problems_with_their_owner_route() -> None:
    status = _status(setup_done=False, protection="off", open_tickets=3, pending_verification=2)

    assert [item.feature_key for item in status.attention] == [
        "setup_center",
        "verification",
        "protection",
        "tickets",
    ]
    assert status.headline == "4 things need you"
    assert "2 people are waiting" in status.attention[1].text
    assert all(key in surface._FEATURE_ROUTE_KEYS for key in (i.feature_key for i in status.attention))


def test_unknown_sources_show_as_unknown_not_as_problems() -> None:
    status = _status(setup_done=None, protection=None, open_tickets=None, pending_verification=None)

    assert status.attention == []
    assert {status.setup_label, status.protection_label, status.tickets_label} == {"Unknown"}


def test_pending_verification_counts_humans_with_the_saved_role() -> None:
    role = SimpleNamespace(members=[SimpleNamespace(bot=False), SimpleNamespace(bot=False), SimpleNamespace(bot=True)])
    guild = SimpleNamespace(get_role=lambda role_id: role if role_id == 55 else None)

    assert home_status.pending_verification_count(guild, {"unverified_role_id": "55"}) == 2
    assert home_status.pending_verification_count(guild, {"unverified_role_id": "99"}) is None
    assert home_status.pending_verification_count(guild, {}) is None


def test_load_home_status_reads_each_source(monkeypatch) -> None:
    async def config(_guild_id, **_kwargs):
        return {"setup_completed": True, "unverified_role_id": "0"}

    async def spam(_guild_id):
        return {"enabled": True}, "loaded"

    async def open_tickets(*, guild_id, **_kwargs):
        return [{"id": 1}, {"id": 2}]

    monkeypatch.setattr(guild_config, "get_guild_config", config)
    monkeypatch.setattr(protection, "_load_spam_settings", spam)
    monkeypatch.setattr(protection, "_protection_state", lambda cfg, spam: "strict")
    monkeypatch.setattr(tickets, "list_open_tickets_for_guild", open_tickets)

    guild = SimpleNamespace(id=1, name="The Smoke Room", get_role=lambda _id: None)
    status = asyncio.run(home_status.load_home_status(guild))

    assert status.setup_done is True
    assert status.protection == "strict"
    assert status.open_tickets == 2
    assert status.guild_name == "The Smoke Room"


def test_slow_or_broken_sources_never_block_the_home(monkeypatch) -> None:
    async def slow_config(_guild_id, **_kwargs):
        await asyncio.sleep(5)

    async def broken_tickets(**_kwargs):
        raise RuntimeError("database down")

    monkeypatch.setattr(guild_config, "get_guild_config", slow_config)
    monkeypatch.setattr(tickets, "list_open_tickets_for_guild", broken_tickets)

    guild = SimpleNamespace(id=1, name="S", get_role=lambda _id: None)
    status = asyncio.run(home_status.load_home_status(guild, timeout=0.05))

    assert (status.setup_done, status.protection, status.open_tickets) == (None, None, None)


# --- view and embed -------------------------------------------------------------


def test_member_home_is_unchanged_compact_section_picker() -> None:
    view = surface.CompactDankHomeView(1)

    assert len(view.children) == 6
    assert not any(cid.startswith("dank:home:task:") for cid in _ids(view))


def test_staff_home_leads_with_four_tasks_then_fixes_then_sections() -> None:
    status = _status(setup_done=False, pending_verification=1)
    view = surface.CompactDankHomeView(1, status=status)
    rows = _rows(view)

    assert [item.label for item in rows[0]] == ["Moderate", "Tickets", "Safety", "Setup"]
    assert [item.label for item in rows[1]] == ["Finish setup", "Review verifications"]
    assert isinstance(rows[2][0], discord.ui.Select)
    assert [item.label for item in rows[3]] == ["Find Feature", "Directory", "Help", "New & Updated", "Close"]
    assert len(_ids(view)) == len(set(_ids(view)))
    assert max(rows) <= 4 and all(len(items) <= 5 for items in rows.values())


def test_staff_home_without_problems_skips_the_fix_row() -> None:
    rows = _rows(surface.CompactDankHomeView(1, status=_status()))

    assert len(rows[0]) == 4
    assert isinstance(rows[1][0], discord.ui.Select)
    assert "Close" in [getattr(item, "label", "") for item in rows[2]]


def test_every_home_task_has_a_canonical_route() -> None:
    assert all(key in surface._FEATURE_ROUTE_KEYS for _label, _emoji, key in surface.HOME_TASKS)


def test_staff_home_embed_shows_status_then_needs() -> None:
    embed = surface._home_embed(_status(open_tickets=3))

    assert embed.title == "🛡️ The Smoke Room"
    assert "1 thing needs you" in embed.description
    assert [(f.name, f.value) for f in embed.fields[:3]] == [
        ("Setup", "Done"),
        ("Protection", "On · Safe"),
        ("Open tickets", "3"),
    ]
    assert embed.fields[3].name == "Needs you"


# --- entry points ---------------------------------------------------------------


class _Response:
    def __init__(self) -> None:
        self.done = False
        self.deferred: list[dict] = []

    def is_done(self) -> bool:
        return self.done

    async def defer(self, **kwargs) -> None:
        self.deferred.append(kwargs)
        self.done = True


def _interaction(*, message: bool) -> SimpleNamespace:
    return SimpleNamespace(
        user=SimpleNamespace(id=7),
        guild=SimpleNamespace(id=1, name="S"),
        message=object() if message else None,
        type=discord.InteractionType.component if message else discord.InteractionType.application_command,
        response=_Response(),
    )


def test_members_get_the_plain_home_without_loading_status(monkeypatch) -> None:
    async def unexpected(_guild):
        raise AssertionError("members must not load server status")

    monkeypatch.setattr(surface, "_home_viewer_is_staff", lambda _i: False)
    monkeypatch.setattr(home_status, "load_home_status", unexpected)

    interaction = _interaction(message=False)
    embed, view = asyncio.run(surface._home_payload(interaction))

    assert view.status is None
    assert interaction.response.deferred == []


def test_staff_home_acknowledges_before_loading_status(monkeypatch) -> None:
    async def fake_load(_guild):
        assert interaction.response.done, "must acknowledge before slow loads"
        return _status()

    monkeypatch.setattr(surface, "_home_viewer_is_staff", lambda _i: True)
    monkeypatch.setattr(home_status, "load_home_status", fake_load)

    interaction = _interaction(message=False)
    embed, view = asyncio.run(surface._home_payload(interaction))

    assert view.status is not None
    assert interaction.response.deferred == [{"ephemeral": True, "thinking": True}]

    interaction = _interaction(message=True)
    asyncio.run(surface._home_payload(interaction))
    assert interaction.response.deferred == [{}], "button presses defer as an in-place update"


def test_compat_dank_home_entry_delegates_to_the_one_home(monkeypatch) -> None:
    calls: list[object] = []

    async def fake_home(interaction, **_kwargs):
        calls.append(interaction)

    monkeypatch.setattr(surface, "open_compact_dank_home", fake_home)
    marker = object()
    asyncio.run(hub.open_dank_home(marker))

    assert calls == [marker]
    assert not hasattr(hub, "DankHomeView")
