from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from stoney_verify import dead_server_clapbacks as clap
from stoney_verify import navigation_registry
from stoney_verify.commands_ext import public_command_surface_v2 as surface
from stoney_verify.commands_ext import public_dead_server_clapbacks as clapback_panel
from stoney_verify.commands_ext.public_community_tools import CommunityToolsView
from stoney_verify.dead_server_clapbacks import (
    BUILTIN_LINES,
    DeadServerClapbackRuntime,
    eligible_categories,
    matches_dead_server_complaint,
    parse_clapback_config,
    pick_clapback_line,
    render_line,
    resolve_ping_role_id,
)
from stoney_verify.commands_ext.public_dead_server_clapbacks import clapback_status_embed


# ---------------------------------------------------------------------------
# Matcher corpus
# ---------------------------------------------------------------------------

POSITIVES = [
    "dead server",
    "server is dead",
    "server's dead",
    "this server is dead",
    "dead chat",
    "chat is dead",
    "dead af",
    "so dead in here",
    "this place is dead",
    "server dead",
    "dead vc",
    "vc is dead",
    # variants
    "this chat is dead",
    "chat's dead",
    "vc dead",
    "it's so dead in here",
    "man this server is dead af",
    "the server is so dead",
    "server is really dead today",
    "dead in here",
    "DEAD SERVER",
    "wow, dead chat again",
]

NEGATIVES = [
    "i'm dead tired",
    "deadass that's funny",
    "dead ass, that was hilarious",
    "drop dead gorgeous",
    "the walking dead is a good show",
    "my phone is dead",
    "deadline is tomorrow",
    "i feel dead inside",
    "chat is deadass boring",
    "that song goes hard, i'm dead",
    "dead serious, stop it",
    "the battery died",
    "server is down",
    "this chat is great",
    "",
    "dead",
]


@pytest.mark.parametrize("text", POSITIVES)
def test_matcher_positives(text: str) -> None:
    assert matches_dead_server_complaint(text) is True


@pytest.mark.parametrize("text", NEGATIVES)
def test_matcher_negatives(text: str) -> None:
    assert matches_dead_server_complaint(text) is False


# ---------------------------------------------------------------------------
# Config + ping-role resolution
# ---------------------------------------------------------------------------


def test_config_defaults_off_with_default_cooldowns() -> None:
    config = parse_clapback_config({})
    assert config.enabled is False
    assert config.ping_role_id == 0
    assert config.channel_cooldown_seconds == clap.DEFAULT_CHANNEL_COOLDOWN_SECONDS
    assert config.user_cooldown_seconds == clap.DEFAULT_USER_COOLDOWN_SECONDS


def test_config_overrides_and_clamps() -> None:
    config = parse_clapback_config(
        {
            clap.ENABLED_KEY: True,
            clap.CHANNEL_COOLDOWN_KEY: 120,
            clap.USER_COOLDOWN_KEY: 999999999,
            clap.PING_ROLE_KEY: "42",
        }
    )
    assert config.enabled is True
    assert config.channel_cooldown_seconds == 120
    assert config.user_cooldown_seconds == clap.MAX_USER_COOLDOWN_SECONDS
    assert config.ping_role_id == 42


def test_ping_role_resolution_precedence() -> None:
    # Explicit clapback role wins over everything.
    assert resolve_ping_role_id({clap.PING_ROLE_KEY: 10, "stoner_role_id": 20}) == 10
    # Legacy Stoners role next.
    assert resolve_ping_role_id({"stoner_role_id": 20, "sesh_ping_role_id": 30}) == 20
    # Legacy sesh ping role last.
    assert resolve_ping_role_id({"sesh_ping_role_id": 30}) == 30
    # Nothing configured -> never guessed.
    assert resolve_ping_role_id({}) == 0


def test_ping_role_resolution_from_community_pings_v2() -> None:
    config = {
        "community_pings_v2": {
            "version": 2,
            "revision": 1,
            "groups": [{"key": "toke-community", "label": "Toke Community"}],
            "options": [
                {
                    "key": "stoners",
                    "role_id": "777",
                    "label": "Stoners",
                    "kind": "community",
                    "group_key": "toke-community",
                    "capabilities": ["toke_start", "toke_notify"],
                }
            ],
        }
    }
    assert resolve_ping_role_id(config) == 777


def test_custom_lines_replace_one_category_only() -> None:
    config = parse_clapback_config(
        {clap.LINES_KEY: {clap.CATEGORY_GENERIC: ["custom line"]}}
    )
    assert config.lines_for(clap.CATEGORY_GENERIC) == ("custom line",)
    assert config.lines_for(clap.CATEGORY_LIVES) == BUILTIN_LINES[clap.CATEGORY_LIVES]


# ---------------------------------------------------------------------------
# Category selection + line picking
# ---------------------------------------------------------------------------


def test_didnt_ping_only_when_role_configured_and_not_used() -> None:
    assert clap.CATEGORY_DIDNT_PING in eligible_categories(ping_role_id=5, did_ping=False)
    assert clap.CATEGORY_DIDNT_PING not in eligible_categories(ping_role_id=5, did_ping=True)
    assert clap.CATEGORY_DIDNT_PING not in eligible_categories(ping_role_id=0, did_ping=False)


def test_pick_never_uses_didnt_ping_without_role() -> None:
    config = parse_clapback_config({clap.ENABLED_KEY: True})
    rng = random.Random(7)
    for _ in range(60):
        picked = pick_clapback_line(config, did_ping=False, rng=rng)
        assert picked is not None
        assert picked[0] != clap.CATEGORY_DIDNT_PING


def test_pick_never_repeats_last_line_per_guild_state() -> None:
    config = parse_clapback_config(
        {clap.ENABLED_KEY: True, clap.LINES_KEY: {c: [f"{c}-a", f"{c}-b"] for c in clap.CATEGORIES}}
    )
    rng = random.Random(3)
    last = ""
    for _ in range(60):
        picked = pick_clapback_line(config, did_ping=True, last_line=last, rng=rng)
        assert picked is not None
        assert picked[1] != last
        last = picked[1]


def test_render_line_uses_configured_role_name_as_plain_text() -> None:
    line = render_line(BUILTIN_LINES[clap.CATEGORY_DIDNT_PING][0], ping_role_name="Sesh Squad")
    assert "@Sesh Squad" in line
    assert "<@&" not in line
    # Default name stays exactly as written.
    assert render_line(BUILTIN_LINES[clap.CATEGORY_DIDNT_PING][0]) == BUILTIN_LINES[clap.CATEGORY_DIDNT_PING][0]


def test_builtin_lines_never_contain_a_real_mention() -> None:
    for lines in BUILTIN_LINES.values():
        for line in lines:
            assert "<@&" not in line
            assert "<@" not in line


# ---------------------------------------------------------------------------
# New joiner category
# ---------------------------------------------------------------------------


def test_builtin_new_joiner_lines_verbatim() -> None:
    assert BUILTIN_LINES[clap.CATEGORY_NEW_JOINER] == (
        "you've been here 4 minutes and you're already leaving a yelp review. incredible",
        "you just joined. you haven't even seen the server yet and it's dead? ok",
        "walked in 2 minutes ago and declared the party over. stay awhile, you ARE the party",
        "brand new and already complaining. at least look around first, damn",
    )
    assert clap.CATEGORY_NEW_JOINER in clap.CATEGORIES
    assert clap.NEW_JOINER_WINDOW == timedelta(hours=24)


def test_is_new_joiner_window() -> None:
    now = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
    recent = SimpleNamespace(joined_at=now - timedelta(minutes=5))
    assert clap.is_new_joiner(recent, now=now) is True
    edge = SimpleNamespace(joined_at=now - timedelta(hours=24))
    assert clap.is_new_joiner(edge, now=now) is True
    old = SimpleNamespace(joined_at=now - timedelta(hours=25))
    assert clap.is_new_joiner(old, now=now) is False
    # Uncached / partial members are never treated as new joiners.
    assert clap.is_new_joiner(SimpleNamespace(joined_at=None), now=now) is False
    assert clap.is_new_joiner(SimpleNamespace(), now=now) is False


def test_new_joiner_eligible_categories_are_exclusive() -> None:
    # Even with a ping role configured and unused, only NEW_JOINER is eligible.
    assert eligible_categories(ping_role_id=5, did_ping=False, new_joiner=True) == (
        clap.CATEGORY_NEW_JOINER,
    )
    assert eligible_categories(ping_role_id=0, did_ping=True, new_joiner=True) == (
        clap.CATEGORY_NEW_JOINER,
    )
    # And it never leaks into the normal rotation.
    assert clap.CATEGORY_NEW_JOINER not in eligible_categories(
        ping_role_id=5, did_ping=False
    )


def test_pick_new_joiner_never_uses_didnt_ping() -> None:
    config = parse_clapback_config(
        {clap.ENABLED_KEY: True, clap.PING_ROLE_KEY: str(400)}
    )
    rng = random.Random(5)
    for _ in range(60):
        picked = pick_clapback_line(config, did_ping=False, new_joiner=True, rng=rng)
        assert picked is not None
        assert picked[0] == clap.CATEGORY_NEW_JOINER
        assert picked[1] in BUILTIN_LINES[clap.CATEGORY_NEW_JOINER]


def test_custom_lines_replace_new_joiner_category() -> None:
    config = parse_clapback_config(
        {clap.LINES_KEY: {clap.CATEGORY_NEW_JOINER: ["fresh face, bold take"]}}
    )
    assert config.lines_for(clap.CATEGORY_NEW_JOINER) == ("fresh face, bold take",)
    # Other categories still fall back to the built-ins.
    assert config.lines_for(clap.CATEGORY_LIVES) == BUILTIN_LINES[clap.CATEGORY_LIVES]
    picked = pick_clapback_line(
        config, did_ping=False, new_joiner=True, rng=random.Random(1)
    )
    assert picked == (clap.CATEGORY_NEW_JOINER, "fresh face, bold take")


# ---------------------------------------------------------------------------
# Runtime with fakes
# ---------------------------------------------------------------------------

GUILD_ID = 100
CHANNEL_ID = 200
USER_ID = 300
ROLE_ID = 400


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _member(user_id: int, *, bot: bool = False, manage: bool = False, admin: bool = False, joined_at=None):
    return SimpleNamespace(
        id=user_id,
        bot=bot,
        guild_permissions=SimpleNamespace(manage_messages=manage, administrator=admin),
        joined_at=joined_at,
    )


def _guild(*, owner_id: int = 999, roles: dict | None = None):
    role_map = roles or {}
    return SimpleNamespace(
        id=GUILD_ID,
        owner_id=owner_id,
        owner=None,
        get_role=lambda rid: role_map.get(int(rid)),
        get_channel=lambda cid: None,
    )


def _channel(*, channel_id: int = CHANNEL_ID, type_=discord.ChannelType.text, category_id=None, parent_id=None, parent=None, name="general"):
    return SimpleNamespace(
        id=channel_id,
        type=type_,
        category_id=category_id,
        parent_id=parent_id,
        parent=parent,
        name=name,
        permissions_for=lambda member: SimpleNamespace(manage_messages=False, administrator=False),
    )


_DEFAULT = object()


def _message(content: str, *, author=None, guild=_DEFAULT, channel=None, webhook_id=None, role_mentions=()):
    message = SimpleNamespace(
        guild=_guild() if guild is _DEFAULT else guild,
        author=author if author is not None else _member(USER_ID),
        channel=channel if channel is not None else _channel(),
        content=content,
        webhook_id=webhook_id,
        type=discord.MessageType.default,
        role_mentions=list(role_mentions),
        reply=AsyncMock(),
    )
    return message


def _runtime(config: dict, clock: _Clock | None = None):
    async def loader(_guild_id: int):
        return dict(config)

    return DeadServerClapbackRuntime(
        SimpleNamespace(),
        config_loader=loader,
        rng=random.Random(11),
        clock=clock or _Clock(),
    )


ENABLED = {clap.ENABLED_KEY: True, "source": "supabase:guild_configs"}


def _reply_line(message) -> str:
    """The clapback line with the leading author mention stripped off."""
    content = message.reply.await_args.kwargs["content"]
    prefix = f"<@{message.author.id}> "
    assert content.startswith(prefix)
    return content[len(prefix):]


def test_runtime_fires_and_tags_the_complainer() -> None:
    runtime = _runtime(ENABLED)
    message = _message("dead server")
    assert asyncio.run(runtime.on_message(message)) is True
    message.reply.assert_awaited_once()
    kwargs = message.reply.await_args.kwargs
    # The author is tagged in the text, naturally leading the line.
    assert kwargs["content"].startswith(f"<@{USER_ID}> ")
    assert _reply_line(message) in {
        line for lines in BUILTIN_LINES.values() for line in lines
    }
    # allowed_mentions permits exactly that author -- and no roles/everyone.
    mentions = kwargs["allowed_mentions"]
    assert [int(getattr(user, "id", user)) for user in mentions.users] == [USER_ID]
    assert mentions.roles is False
    assert mentions.everyone is False
    assert mentions.replied_user is False
    assert mentions.to_dict() == {"users": [USER_ID], "parse": []}
    # The role is never pinged: no role-mention syntax anywhere in the text.
    assert "<@&" not in kwargs["content"]


def test_runtime_silent_when_toggle_off() -> None:
    runtime = _runtime({clap.ENABLED_KEY: False})
    message = _message("dead server")
    assert asyncio.run(runtime.on_message(message)) is False
    message.reply.assert_not_awaited()


def test_runtime_silent_for_non_matching_text() -> None:
    runtime = _runtime(ENABLED)
    message = _message("hello everyone, what's up")
    assert asyncio.run(runtime.on_message(message)) is False
    message.reply.assert_not_awaited()


@pytest.mark.parametrize(
    "message",
    [
        _message("dead server", author=_member(USER_ID, bot=True)),
        _message("dead server", author=_member(999), guild=_guild(owner_id=999)),
        _message("dead server", author=_member(USER_ID, manage=True)),
        _message("dead server", author=_member(USER_ID, admin=True)),
        _message("dead server", webhook_id=12345),
        _message("dead server", guild=None),
        _message("dead server", channel=_channel(type_=discord.ChannelType.news)),
        _message("dead server", channel=_channel(type_=discord.ChannelType.forum)),
    ],
    ids=["bot", "owner", "manage-messages", "admin", "webhook", "dm", "news-channel", "forum-channel"],
)
def test_runtime_exemptions(message) -> None:
    runtime = _runtime(ENABLED)
    assert asyncio.run(runtime.on_message(message)) is False
    message.reply.assert_not_awaited()


def test_runtime_skips_ticket_thread() -> None:
    parent = _channel(channel_id=201, category_id=555, name="ticket-42")
    thread = _channel(
        channel_id=202,
        type_=discord.ChannelType.public_thread,
        parent_id=201,
        parent=parent,
        name="ticket-42",
    )
    runtime = _runtime({**ENABLED, "ticket_category_id": "555"})
    message = _message("dead server", channel=thread)
    assert asyncio.run(runtime.on_message(message)) is False
    message.reply.assert_not_awaited()


def test_runtime_channel_and_user_cooldowns() -> None:
    clock = _Clock()
    runtime = _runtime(ENABLED, clock=clock)
    first = _message("dead server")
    assert asyncio.run(runtime.on_message(first)) is True

    # Same channel, different user: channel cooldown blocks.
    second = _message("chat is dead", author=_member(301))
    assert asyncio.run(runtime.on_message(second)) is False

    # Different channel, same user: user cooldown blocks.
    third = _message("chat is dead", channel=_channel(channel_id=201))
    assert asyncio.run(runtime.on_message(third)) is False

    # After both cooldowns pass, it fires again -- with a different line.
    clock.now += clap.DEFAULT_USER_COOLDOWN_SECONDS + 1
    fourth = _message("chat is dead", channel=_channel(channel_id=201))
    assert asyncio.run(runtime.on_message(fourth)) is True
    assert fourth.reply.await_args.kwargs["content"] != first.reply.await_args.kwargs["content"]


def test_runtime_didnt_ping_lines_only_without_role_mention() -> None:
    config = {**ENABLED, clap.PING_ROLE_KEY: str(ROLE_ID)}
    guild = _guild(roles={ROLE_ID: SimpleNamespace(id=ROLE_ID, name="Stoners")})

    runtime = _runtime(config)
    seen_categories = set()
    clock = _Clock()
    runtime._clock = clock
    for index in range(40):
        clock.now += 7200
        message = _message(
            "dead server",
            author=_member(1000 + index),
            guild=guild,
            channel=_channel(channel_id=500 + index),
        )
        assert asyncio.run(runtime.on_message(message)) is True
        assert "<@&" not in message.reply.await_args.kwargs["content"]
        if _reply_line(message) in BUILTIN_LINES[clap.CATEGORY_DIDNT_PING]:
            seen_categories.add(clap.CATEGORY_DIDNT_PING)
    assert clap.CATEGORY_DIDNT_PING in seen_categories

    # When the complainer DID ping the role, those lines never appear.
    runtime2 = _runtime(config)
    runtime2._clock = clock
    role = SimpleNamespace(id=ROLE_ID, name="Stoners")
    for index in range(40):
        clock.now += 7200
        message = _message(
            "dead server",
            author=_member(2000 + index),
            guild=guild,
            channel=_channel(channel_id=900 + index),
            role_mentions=[role],
        )
        assert asyncio.run(runtime2.on_message(message)) is True
        assert _reply_line(message) not in BUILTIN_LINES[clap.CATEGORY_DIDNT_PING]


def test_runtime_new_joiner_gets_only_new_joiner_lines() -> None:
    # Ping role configured and NOT used: DIDNT_PING would normally be
    # eligible, but a brand-new member must draw only new-joiner lines.
    config = {**ENABLED, clap.PING_ROLE_KEY: str(ROLE_ID)}
    guild = _guild(roles={ROLE_ID: SimpleNamespace(id=ROLE_ID, name="Stoners")})
    runtime = _runtime(config)
    clock = _Clock()
    runtime._clock = clock
    joined = datetime.now(timezone.utc) - timedelta(minutes=10)
    for index in range(30):
        clock.now += 7200
        message = _message(
            "dead server",
            author=_member(3000 + index, joined_at=joined),
            guild=guild,
            channel=_channel(channel_id=700 + index),
        )
        assert asyncio.run(runtime.on_message(message)) is True
        line = _reply_line(message)
        assert line in BUILTIN_LINES[clap.CATEGORY_NEW_JOINER]
        assert line not in BUILTIN_LINES[clap.CATEGORY_DIDNT_PING]


def test_runtime_joined_25h_ago_uses_normal_rotation() -> None:
    runtime = _runtime(ENABLED)
    joined = datetime.now(timezone.utc) - timedelta(hours=25)
    message = _message("dead server", author=_member(USER_ID, joined_at=joined))
    assert asyncio.run(runtime.on_message(message)) is True
    line = _reply_line(message)
    assert line not in BUILTIN_LINES[clap.CATEGORY_NEW_JOINER]
    normal = {
        text
        for category, lines in BUILTIN_LINES.items()
        if category != clap.CATEGORY_NEW_JOINER
        for text in lines
    }
    assert line in normal


def test_runtime_joined_at_none_uses_normal_rotation() -> None:
    runtime = _runtime(ENABLED)
    message = _message("dead server", author=_member(USER_ID, joined_at=None))
    assert asyncio.run(runtime.on_message(message)) is True
    assert _reply_line(message) not in BUILTIN_LINES[clap.CATEGORY_NEW_JOINER]


def test_runtime_unavailable_config_is_silent() -> None:
    runtime = _runtime({**ENABLED, "source": "unavailable:db_read_failed"})
    message = _message("dead server")
    assert asyncio.run(runtime.on_message(message)) is False
    message.reply.assert_not_awaited()


def test_ensure_runtime_registers_one_listener() -> None:
    calls: list = []
    bot = SimpleNamespace(add_listener=lambda func, event: calls.append((func, event)))
    first = clap.ensure_dead_server_clapback_runtime(bot)
    second = clap.ensure_dead_server_clapback_runtime(bot)
    assert first is second
    assert len(calls) == 1
    assert calls[0][1] == "on_message"


# ---------------------------------------------------------------------------
# Panel surface
# ---------------------------------------------------------------------------


def test_status_embed_reflects_state_and_role() -> None:
    off = clapback_status_embed(parse_clapback_config({}))
    assert "Off" in off.description
    on = clapback_status_embed(
        parse_clapback_config({clap.ENABLED_KEY: True, clap.PING_ROLE_KEY: "5"}),
        ping_role_name="Stoners",
    )
    assert "On" in on.description
    joined = "\n".join(str(field.value) for field in on.fields)
    assert "@Stoners" in joined
    assert "<@&" not in joined


def test_community_tools_view_contains_clapback_button() -> None:
    # Menu Phase 0 (2026-11-21): the registry groups Clapbacks under
    # Community Tools' "Server tools", so the tools menu links it again.
    view = CommunityToolsView(123)
    labels = {
        str(getattr(item, "label", "") or "")
        for item in view.children
        if str(getattr(item, "label", "") or "")
    }
    assert "Dead Server Clapbacks" in labels
    assert sum(isinstance(item, discord.ui.Button) for item in view.children) == 10


def test_clapback_destination_is_registered_with_community_server_tools() -> None:
    feature = navigation_registry.feature_by_key("dead_server_clapbacks")
    assert feature is not None
    assert feature.label == "Dead Server Clapbacks"
    assert feature.category == "community"
    assert feature.manager_only is True
    assert navigation_registry.FEATURE_GROUPS[feature.key] == "Server tools"
    assert feature.key in surface._FEATURE_ROUTE_KEYS
    grouped = dict(navigation_registry.grouped_features("community"))
    assert feature in grouped["Server tools"]
    assert navigation_registry.search_features("dead server clapbacks")[0].key == feature.key


def test_clapback_destination_routes_to_existing_panel(monkeypatch) -> None:
    fake_open = AsyncMock()
    monkeypatch.setattr(clapback_panel, "open_dead_server_clapbacks", fake_open)
    interaction = SimpleNamespace(user=SimpleNamespace(id=42))

    asyncio.run(surface._route_feature(interaction, "dead_server_clapbacks"))

    fake_open.assert_awaited_once_with(interaction)
