from __future__ import annotations

import asyncio
from itertools import count
from pathlib import Path

import pytest

from stoney_verify import share_router_runtime as router
from stoney_verify.share_router_resources import (
    DEFAULT_SHARE_CHANNELS,
    custom_share_source_name,
    is_share_router_design_resource,
    is_share_router_managed_category_name,
)


class FakeCategory:
    def __init__(self, category_id: int, name: str) -> None:
        self.id = category_id
        self.name = name
        self.channels: list[FakeTextChannel] = []

    @property
    def text_channels(self) -> list[FakeTextChannel]:
        return self.channels


class FakeTextChannel:
    def __init__(self, guild: FakeGuild, name: str, category: FakeCategory, channel_id: int) -> None:
        self.id = channel_id
        self.guild = guild
        self.name = name
        self.category = category
        self.deleted = False

    async def delete(self, **_kwargs) -> None:
        self.deleted = True
        self.category.channels.remove(self)
        self.guild.channels.pop(self.id, None)


class FakeGuild:
    _ids = count(10000)

    def __init__(self, guild_id: int) -> None:
        self.id = guild_id
        self.default_role = object()
        self.me = None
        self.channels: dict[int, FakeTextChannel] = {}
        self.categories = [FakeCategory(next(self._ids), "🔗 SHARE ROUTES")]

    def get_channel(self, channel_id: int):
        return self.channels.get(channel_id)

    async def create_category(self, name: str, **_kwargs) -> FakeCategory:
        category = FakeCategory(next(self._ids), name)
        self.categories.append(category)
        return category

    async def create_text_channel(self, name: str, *, category: FakeCategory, **_kwargs) -> FakeTextChannel:
        channel = FakeTextChannel(self, name, category, next(self._ids))
        category.channels.append(channel)
        self.channels[channel.id] = channel
        return channel


@pytest.fixture
def isolated_router(monkeypatch, tmp_path):
    monkeypatch.setattr(router, "ROUTES_FILE", tmp_path / "share_routes.json")
    monkeypatch.setattr(router.discord, "TextChannel", FakeTextChannel)
    monkeypatch.setattr(router.discord, "CategoryChannel", FakeCategory)
    async def no_staff(_guild, overwrites):
        return overwrites
    monkeypatch.setattr(router, "_add_configured_staff_overwrites", no_staff)
    router._GUILD_SOURCE_LOCKS.clear()
    return router


def test_custom_proxy_names_and_reserved_resources():
    assert custom_share_source_name("Gaming Clips") == "share-gaming-clips"
    assert custom_share_source_name("share-gaming-clips") == "share-gaming-clips"
    assert custom_share_source_name("📹 Gaming Clips") == "share-gaming-clips"
    for value in ("", "!!!", "share-memes", "share-" + "x" * 100):
        with pytest.raises(ValueError):
            custom_share_source_name(value)
    assert is_share_router_managed_category_name("🔗 SHARE ROUTES 2")
    assert is_share_router_managed_category_name("🔗 SHARE ROUTES 12")
    assert not is_share_router_managed_category_name("normal-channel")
    custom = type("Channel", (), {"name": "share-gaming-clips", "category": type("Category", (), {"name": "🔗 SHARE ROUTES 2"})()})()
    assert is_share_router_design_resource(custom)
    unrelated = type("Channel", (), {"name": "share-gaming-clips", "category": type("Category", (), {"name": "general"})()})()
    assert not is_share_router_design_resource(unrelated)


def test_custom_source_registration_is_per_guild_and_preserves_legacy_routes(isolated_router):
    a, b = FakeGuild(111), FakeGuild(222)

    async def check():
        await router.save_route(111, source_channel_id=333, target_channel_id=444)
        source_a = await router.create_custom_share_source(a, None, "Gaming Clips")
        source_b = await router.create_custom_share_source(b, None, "Gaming Clips")
        assert source_a.name == source_b.name == "share-gaming-clips"
        assert source_a.id != source_b.id
        assert await router.guild_custom_share_sources(111) == [source_a.id]
        assert await router.guild_custom_share_sources(222) == [source_b.id]
        assert (await router.guild_routes(111))[0]["target_channel_id"] == "444"
        assert await router.guild_routes(222) == []
        assert is_share_router_design_resource(source_a)
        with pytest.raises(ValueError, match="already exists"):
            await router.create_custom_share_source(a, None, "gaming clips")
        return source_a, source_b

    a_source, b_source = asyncio.run(check())
    saved = router._load_all_unlocked()
    assert set(saved) == {"111", "222"}
    assert saved["111"]["custom_sources"] == [str(a_source.id)]
    assert saved["222"]["custom_sources"] == [str(b_source.id)]


def test_proxy_category_overflow_keeps_default_hub_and_scales_picker(isolated_router):
    guild = FakeGuild(100)
    hub = guild.categories[0]
    for n in range(50):
        channel = FakeTextChannel(guild, f"existing-{n}", hub, next(guild._ids))
        hub.channels.append(channel)
        guild.channels[channel.id] = channel

    async def check():
        added = await router.create_custom_share_source(guild, None, "Gaming Clips")
        assert added.category.name == "🔗 SHARE ROUTES 2"
        assert len(hub.channels) == 50
        assert len(guild.categories) == 2
        assert added.name == "share-gaming-clips"
        assert is_share_router_design_resource(added)
        second = await router.create_custom_share_source(guild, None, "Funny Clips")
        assert second.category is added.category
        assert await router.guild_custom_share_sources(guild.id) == [added.id, second.id]

    asyncio.run(check())


def test_custom_proxy_delete_cleans_its_route_but_not_other_guilds(isolated_router):
    guild, other = FakeGuild(333), FakeGuild(444)

    async def check():
        source = await router.create_custom_share_source(guild, None, "Gaming Clips")
        separate = await router.create_custom_share_source(other, None, "Gaming Clips")
        await router.save_route(guild.id, source_channel_id=source.id, target_channel_id=777)
        await router.save_route(other.id, source_channel_id=separate.id, target_channel_id=888)
        with pytest.raises(ValueError, match="not a registered"):
            await router.delete_custom_share_source(other, source.id)
        assert not source.deleted
        await router.delete_custom_share_source(guild, source.id)
        assert source.deleted
        assert await router.guild_custom_share_sources(guild.id) == []
        assert await router.guild_routes(guild.id) == []
        assert await router.guild_custom_share_sources(other.id) == [separate.id]
        assert (await router.guild_routes(other.id))[0]["target_channel_id"] == "888"

    asyncio.run(check())


def test_failed_registration_rolls_back_channel_and_preserves_saved_data(isolated_router, monkeypatch):
    guild = FakeGuild(555)
    async def initial():
        await router.save_route(guild.id, source_channel_id=111, target_channel_id=222)
    asyncio.run(initial())
    existing = router.ROUTES_FILE.read_bytes()

    def failed_write(_data):
        raise OSError("disk full")
    monkeypatch.setattr(router, "_save_all_unlocked", failed_write)

    async def check():
        with pytest.raises(OSError, match="disk full"):
            await router.create_custom_share_source(guild, None, "Gaming Clips")

    asyncio.run(check())
    assert router.ROUTES_FILE.read_bytes() == existing
    assert not guild.categories[0].channels


def test_corrupt_route_file_never_resets_other_guilds(isolated_router):
    router.ROUTES_FILE.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError):
        asyncio.run(router.guild_custom_share_sources(12))
    assert router.ROUTES_FILE.read_text(encoding="utf-8") == "{broken"


def test_new_ui_pages_sources_and_routes_and_preserves_old_routing_behavior():
    root = Path(__file__).resolve().parents[1]
    ui = (root / "stoney_verify/commands_ext/public_share_router.py").read_text(encoding="utf-8")
    runtime = (root / "stoney_verify/share_router_runtime.py").read_text(encoding="utf-8")
    assert 'label="Create Proxy"' in ui
    assert 'label="Delete Custom Proxy"' in ui
    assert "ShareRouterDeleteSourceConfirmView" in ui
    assert 'custom_id="dank:share_router:source"' in ui
    assert 'custom_id="dank:share_router:remove"' in ui
    assert 'custom_id="dank:share_router:delete_source"' in ui
    assert ui.count("DankGuildResourceBrowserView(") >= 4
    assert "choices=choices[:25]" not in ui
    assert 'route = route_for_source(routes, int(message.channel.id))' in runtime
    assert 'await save_route(' in ui
    assert tuple(DEFAULT_SHARE_CHANNELS) == (
        "share-gaming-news", "share-deals", "share-memes", "share-announcements",
    )
