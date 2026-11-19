from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import discord
import pytest

from stoney_verify.commands_ext import public_mod_command_center as mod
from stoney_verify.commands_ext import member_command_center as members
from stoney_verify.commands_ext import moderation
from stoney_verify.commands_ext import public_mod_group
from stoney_verify.commands_ext import public_mod_ban_toggle_patch


def run(coro):
    return asyncio.run(coro)


def _not_found(code: int) -> discord.NotFound:
    return discord.NotFound(
        SimpleNamespace(status=404, reason="Not Found"),
        {"code": code, "message": "Unknown Member"},
    )


def _forbidden() -> discord.Forbidden:
    return discord.Forbidden(
        SimpleNamespace(status=403, reason="Forbidden"),
        {"code": 50013, "message": "Missing Permissions"},
    )


def test_mod_member_id_parser_does_not_depend_on_picker():
    snowflake = "1531117218291646605"
    assert mod.parse_mod_member_id(snowflake) == int(snowflake)
    assert mod.parse_mod_member_id(f"<@{snowflake}>") == int(snowflake)
    assert mod.parse_mod_member_id(f"<@!{snowflake}>") == int(snowflake)
    for invalid in ("", "someone", "1234", "123456789012345678901",
                    "user: 1531117218291646605", "0" * 20, "1;DROP"):
        # Zero is allowed syntactically, but is never a valid actionable member ID.
        result = mod.parse_mod_member_id(invalid)
        assert result == 0 if invalid != "0" * 20 else result == 0


def test_live_discord_lookup_distinguishes_absent_from_api_failures():
    member = Mock(spec=discord.Member)
    member.id = 1531117218291646605
    class Guild:
        def __init__(self):
            self.expected = member
            self.calls = []
        async def fetch_member(self, user_id):
            self.calls.append(user_id)
            if isinstance(self.expected, Exception):
                raise self.expected
            return self.expected

    guild = Guild()
    found, status = run(mod.resolve_current_mod_member(guild, member.id))
    assert found is member and status == "present"
    assert guild.calls == [member.id]

    guild.expected = _not_found(10007)
    found, status = run(mod.resolve_current_mod_member(guild, member.id))
    assert found is None and status == "absent"

    guild.expected = _not_found(10004)
    assert run(mod.resolve_current_mod_member(guild, member.id))[1] == "unavailable"

    guild.expected = _forbidden()
    assert run(mod.resolve_current_mod_member(guild, member.id))[1] == "unavailable"

    assert "could not verify" in mod._lookup_failure("unavailable", member.id)
    assert "not currently" in mod._lookup_failure("absent", member.id)


def test_mod_has_picker_fallback_and_routes_not_cache():
    view = mod.ModerationCenterView(123)
    labels = {item.label for item in view.children if isinstance(item, discord.ui.Button)}
    assert {"Find Member by ID", "Ban / Unban by ID", "Unverified Enforcement"} <= labels
    assert "picker" in mod._center_embed().description.lower()
    assert len(mod._center_embed().description) <= 280


def test_member_center_id_fallback_is_exposed():
    view = members.LiveMembersMenuView(123)
    labels = {item.label for item in view.children if isinstance(item, discord.ui.Button)}
    assert "Find Member by ID" in labels
    assert "not a complete server roster" in members._live_members_embed([]).description


def test_mod_picker_passes_selected_id_to_rest_verified_open(monkeypatch):
    events = []
    async def staff(interaction):
        return True
    async def open_by_id(interaction, member_id, **kwargs):
        events.append((member_id, kwargs["replace_original"], kwargs["browser"]))
    monkeypatch.setattr(mod, "_require_staff", staff)
    monkeypatch.setattr(mod, "open_member_actions_by_id", open_by_id)
    view = mod.ModerationCenterView(123)
    picker = next(x for x in view.children if isinstance(x, mod.ModMemberSelect))
    run(picker.on_pick(SimpleNamespace(), SimpleNamespace(id=1531117218291646605)))
    assert events == [(1531117218291646605, True, view)]


def test_live_members_picker_queries_rest_even_when_cache_is_empty(monkeypatch):
    events = []
    async def staff(interaction):
        return True
    async def resolve(guild, member_id):
        events.append("rest")
        return None, "unavailable"
    async def reply(interaction, text):
        events.append(text)
    monkeypatch.setattr(members, "require_review", staff)
    monkeypatch.setattr(mod, "resolve_current_mod_member", resolve)
    monkeypatch.setattr(members, "reply_ephemeral", reply)
    view = members.LiveMembersMenuView(123)
    picker = next(x for x in view.children if isinstance(x, members.DirectMemberSelect))
    class Response:
        def is_done(self):
            return bool(events)
        async def defer(self, **kwargs):
            events.append("deferred")
    interaction = SimpleNamespace(response=Response(), guild=SimpleNamespace(
        get_member=lambda uid: None,
    ))
    run(picker.on_pick(interaction, SimpleNamespace(id=1531117218291646605)))
    assert events[0:2] == ["deferred", "rest"]
    assert any("could not verify" in str(item) for item in events)


def test_ban_id_resolution_does_not_treat_discord_errors_as_absence():
    class Guild:
        def __init__(self, failure):
            self.failure = failure
        async def fetch_ban(self, obj):
            raise _not_found(10026)
        async def fetch_member(self, member_id):
            raise self.failure
        def get_member(self, uid):
            raise AssertionError("Do not trust a possibly stale member cache")

    with pytest.raises(moderation.MemberLookupUnavailable):
        run(moderation._resolve_ban_toggle_target(
            Guild(_forbidden()), "1531117218291646605",
        ))
    with pytest.raises(moderation.MemberLookupUnavailable):
        run(moderation._resolve_ban_toggle_target(
            Guild(_not_found(10004)), "1531117218291646605",
        ))

    uid, member, ban_entry = run(moderation._resolve_ban_toggle_target(
        Guild(_not_found(10007)), "1531117218291646605",
    ))
    assert uid == 1531117218291646605 and member is None and ban_entry is None


def test_administrator_does_not_bypass_higher_discord_role():
    actor = Mock(spec=discord.Member)
    actor.id = 20
    actor.top_role = 10
    actor.guild_permissions.administrator = True
    target = Mock(spec=discord.Member)
    target.id = 30
    target.top_role = 15
    interaction = SimpleNamespace(
        user=actor, guild=SimpleNamespace(owner_id=99),
    )
    okay, message = public_mod_group._actor_can_moderate_target(
        interaction=interaction, target=target, action="kick",
    )
    assert not okay
    assert "higher role" in message


def test_modlog_uses_canonical_guild_config_id(monkeypatch):
    channel = Mock(spec=discord.TextChannel)
    channel.permissions_for = lambda me: SimpleNamespace(
        view_channel=True, send_messages=True, embed_links=True,
    )
    guild = SimpleNamespace(
        id=123, me=SimpleNamespace(id=333),
        get_channel=lambda cid: channel if cid == 789 else None,
    )
    reads = []
    async def channel_id(guild_id):
        reads.append(guild_id)
        return 789

    monkeypatch.setattr(moderation, "get_modlog_channel_id", channel_id)
    monkeypatch.setattr(public_mod_ban_toggle_patch, "get_modlog_channel_id", channel_id)
    assert run(moderation._configured_modlog_channel(guild)) is channel
    assert run(public_mod_ban_toggle_patch._configured_modlog_channel(guild)) is channel
    assert reads == [123, 123]


def test_ban_by_id_modal_requires_explicit_action():
    form = mod.BanUnbanByIdModal(123)
    assert form.action.default is None
    assert form.action.required
