from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import Mock

import discord
import pytest

from stoney_verify.navigation_registry import feature_by_key, search_features
from stoney_verify.somewhere_420_clock import PLACES, live_cities, next_wave, world_420
from stoney_verify.commands_ext import public_somewhere_420 as ui


def at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def city(places, name: str):
    return next(place for place in places if place.city == name)


def test_420_world_times_use_real_dst_not_a_static_utc_offset() -> None:
    summer = world_420(at("2026-07-01T20:20:15Z"))
    winter = world_420(at("2026-01-15T21:20:15Z"))
    assert "New York" in live_cities(summer)
    assert "New York" in live_cities(winter)
    assert city(summer, "New York").local_time.hour == 16
    assert city(winter, "New York").local_time.hour == 16
    assert city(summer, "New York").next_utc == at("2026-07-02T20:20:00Z")
    assert city(winter, "New York").next_utc == at("2026-01-16T21:20:00Z")
    # London is on BST in July and GMT in January.
    assert city(summer, "London").local_time.hour == 21
    assert city(winter, "London").local_time.hour == 21


def test_420_world_handles_quarter_hour_offsets_and_am_pm() -> None:
    assert "Kathmandu" in live_cities(world_420(at("2026-04-01T10:35:30Z")))
    assert "Kathmandu" not in live_cities(world_420(at("2026-04-01T10:36:00Z")))
    assert "New York" not in live_cities(world_420(at("2026-07-01T08:20:00Z")))
    assert "New York" in live_cities(world_420(at("2026-07-01T08:20:00Z"), hour=4))
    assert len(PLACES) == len({name for name, _ in PLACES})
    assert len(PLACES) == len({zone for _, zone in PLACES})


def test_420_world_groups_same_instants_and_rejects_bad_time() -> None:
    places = world_420(at("2026-10-22T18:00:00Z"))
    waves = next_wave(places)
    assert waves and len(waves) <= 5
    assert [instant for instant, _ in waves] == sorted(instant for instant, _ in waves)
    assert all(instant > at("2026-10-22T18:00:00Z") for instant, _ in waves)
    assert all(names for _, names in waves)
    assert next_wave(places, limit=0) == ()
    with pytest.raises(ValueError):
        world_420(datetime(2026, 10, 22, 18, 0))
    with pytest.raises(ValueError):
        world_420(at("2026-10-22T18:00:00Z"), hour=20)


def test_420_board_and_navigation_are_discoverable_and_no_ping_by_default() -> None:
    feature = feature_by_key("somewhere_420")
    assert feature is not None and feature.category == "community"
    assert feature.manager_only is False
    assert search_features("4:20")[0].key == "somewhere_420"

    embed = ui.build_420_embed(now=at("2026-07-01T20:20:30Z"))
    assert "New York" in str(embed.fields[0].value)
    assert "4:20" in str(embed.title)
    assert "Start /toke" in str(embed.fields[-1].value)
    shared = ui.build_420_embed(now=at("2026-07-01T20:20:30Z"), public=True)
    assert "Run `/toke`" in str(shared.fields[-1].value)
    assert "shared snapshot" in str(shared.footer.text)
    assert len(embed.fields) <= 5
    assert all(len(str(f.value)) <= 1024 for f in embed.fields)
    favorite = ui.build_420_embed(now=at("2026-07-01T20:20:30Z"), favorite_city="Tokyo")
    assert "Your favorite: Tokyo" in [f.name for f in favorite.fields]
    view = ui.Somewhere420View(owner_id=123)
    labels = {item.label for item in view.children if isinstance(item, discord.ui.Button)}
    assert labels == {
        "Refresh World Clock", "Switch AM / PM", "Start /toke", "Share Here (staff)",
        "Quick Text-Only Sesh", "Join Active Sesh", "My 4:20 Preferences", "Daily Server Settings",
    }
    assert len(view.children) <= 25


def test_420_public_share_cooldown_uses_durable_claim_and_rejects_bad_state() -> None:
    now = 1_800_000_000
    assert ui.share_cooldown_remaining(None, now_timestamp=now) == 0
    assert ui.share_cooldown_remaining({"last_public_at": now - 3600}, now_timestamp=now) == 0
    assert ui.share_cooldown_remaining({"last_public_at": now - 100}, now_timestamp=now) == 3500
    assert ui.share_cooldown_remaining({"last_public_at": now + 10}, now_timestamp=now) == 3600
    assert ui.share_cooldown_remaining({"last_public_at": now + 86400}, now_timestamp=now) == 0
    assert ui.share_cooldown_remaining({"last_public_at": "bad"}, now_timestamp=now) == 0


def test_420_share_denies_non_managers_before_touching_config(monkeypatch) -> None:
    monkeypatch.setattr(ui, "interaction_has_manage_guild_authority", lambda _i: False)
    events: list[dict] = []

    class FakeResponse:
        def is_done(self):
            return False

        async def send_message(self, **kwargs):
            events.append(kwargs)

    interaction = Mock()
    interaction.response = FakeResponse()
    interaction.guild = Mock()
    interaction.channel = Mock()
    asyncio.run(ui.publish_420_board(interaction, hour=16))
    assert len(events) == 1
    assert "Only the server owner or managers" in events[0]["content"]
    assert events[0]["allowed_mentions"].everyone is False
    assert events[0]["ephemeral"] is True


def test_420_clock_stays_on_demand_without_unapproved_scheduler() -> None:
    # Nobody is opted in by default; the feature installs no new ready worker,
    # startup guard or registered slash-command root.
    assert not hasattr(ui, "register_somewhere_420_worker")
    assert ui.SHARE_COOLDOWN_SECONDS >= 3600


def test_420_staff_share_is_one_post_per_guild_cooldown(monkeypatch) -> None:
    from types import SimpleNamespace
    from stoney_verify import guild_config

    class FakeMember:
        pass

    class FakeChannel:
        def __init__(self):
            self.mention = "#sesh"
            self.sent = []

        def permissions_for(self, member):
            return SimpleNamespace(view_channel=True, send_messages=True, embed_links=True)

        async def send(self, **kwargs):
            self.sent.append(kwargs)
            return SimpleNamespace(jump_url="https://discord.com/channels/111/222/333")

    class FakeResponse:
        def __init__(self):
            self.deferred = False

        def is_done(self):
            return self.deferred

        async def defer(self, **kwargs):
            self.deferred = True

    class FakeFollowup:
        def __init__(self):
            self.responses = []

        async def send(self, **kwargs):
            self.responses.append(kwargs)

    monkeypatch.setattr(ui.discord, "Member", FakeMember)
    monkeypatch.setattr(ui.discord, "TextChannel", FakeChannel)
    monkeypatch.setattr(ui, "interaction_has_manage_guild_authority", lambda _: True)

    state = {}
    claims = []

    async def load(gid, *, refresh=False):
        return dict(state, source="guild_configs")

    async def cas(gid, key, *, expected, value, source):
        claims.append((expected, value, source))
        if state.get(key) != expected:
            return False, {}
        state[key] = value
        return True, state.copy()

    monkeypatch.setattr(guild_config, "get_guild_config", load)
    monkeypatch.setattr(guild_config, "compare_and_swap_guild_config_key", cas)
    channel = FakeChannel()
    guild = SimpleNamespace(id=1234, me=FakeMember())

    async def fire():
        interaction = SimpleNamespace(
            guild=guild, channel=channel, response=FakeResponse(), followup=FakeFollowup(),
        )
        await ui.publish_420_board(interaction, hour=16)
        return interaction.followup.responses

    first = asyncio.run(fire())
    second = asyncio.run(fire())
    assert len(channel.sent) == 1
    assert "No roles were pinged" in first[0]["content"]
    assert "Try again" in second[0]["content"]
    assert channel.sent[0]["allowed_mentions"].roles is False
    assert len(claims) == 1


def test_420_button_hands_off_to_authoritative_toke(monkeypatch) -> None:
    from stoney_verify.commands_ext import public_toke

    recorded = []

    async def canonical(interaction, *, message):
        recorded.append((interaction, message))

    monkeypatch.setattr(public_toke, "open_toke_command", canonical)
    view = ui.Somewhere420View(owner_id=42)
    button = next(item for item in view.children if getattr(item, "label", None) == "Start /toke")
    interaction = object()
    asyncio.run(button.callback(interaction))
    assert recorded == [(interaction, "It's 4:20 somewhere! 🌍💨")]
