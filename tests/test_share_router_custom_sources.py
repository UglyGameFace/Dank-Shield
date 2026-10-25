from __future__ import annotations

import asyncio
from itertools import count
from pathlib import Path
from types import SimpleNamespace

import pytest

from stoney_verify import share_router_runtime as router
from stoney_verify.share_router_resources import (
    DEFAULT_SHARE_CHANNELS,
    custom_share_source_name,
    is_share_router_design_resource,
    is_share_router_managed_category_name,
)


class FakeCategory:
    def __init__(self, category_id: int, name: str, guild: FakeGuild) -> None:
        self.id = category_id
        self.name = name
        self.guild = guild
        self.channels: list[FakeTextChannel] = []
        self.deleted = False

    async def delete(self, **_kwargs) -> None:
        self.deleted = True
        self.guild.categories.remove(self)

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
        self.categories = [FakeCategory(next(self._ids), "🔗 SHARE ROUTES", self)]

    def get_channel(self, channel_id: int):
        return self.channels.get(channel_id)

    async def fetch_channels(self):
        return [*self.categories, *self.channels.values()]

    async def create_category(self, name: str, **_kwargs) -> FakeCategory:
        category = FakeCategory(next(self._ids), name, self)
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


def test_empty_overflow_category_is_cleaned_after_delete(isolated_router):
    guild = FakeGuild(102)
    for n in range(50):
        channel = FakeTextChannel(guild, f"existing-{n}", guild.categories[0], next(guild._ids))
        guild.categories[0].channels.append(channel)
        guild.channels[channel.id] = channel

    async def check():
        source = await router.create_custom_share_source(guild, None, "Gaming Clips")
        overflow = source.category
        assert len(guild.categories) == 2
        await router.delete_custom_share_source(guild, source.id)
        assert overflow.deleted
        assert guild.categories == [guild.categories[0]]

    asyncio.run(check())


def test_overflow_creation_failure_cleans_category(isolated_router):
    guild = FakeGuild(103)
    for n in range(50):
        channel = FakeTextChannel(guild, f"existing-{n}", guild.categories[0], next(guild._ids))
        guild.categories[0].channels.append(channel)
        guild.channels[channel.id] = channel

    async def fail_creation(*_args, **_kwargs):
        raise OSError("guild channel quota exceeded")
    guild.create_text_channel = fail_creation

    async def check():
        with pytest.raises(OSError, match="quota"):
            await router.create_custom_share_source(guild, None, "Gaming Clips")
        assert len(guild.categories) == 1
        assert await router.guild_custom_share_sources(guild.id) == []

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


def test_route_save_revalidates_against_deleted_source(isolated_router):
    guild = FakeGuild(556)

    async def check():
        source = await router.create_custom_share_source(guild, None, "Gaming Clips")
        await router.save_managed_share_route(
            guild,
            source_channel_id=source.id,
            target_channel_id=777,
            created_by_id=555,
        )
        assert router.route_for_source(await router.guild_routes(guild.id), source.id) is not None
        await router.delete_custom_share_source(guild, source.id)
        with pytest.raises(ValueError, match="no longer"):
            await router.save_managed_share_route(
                guild,
                source_channel_id=source.id,
                target_channel_id=888,
                created_by_id=555,
            )
        assert await router.guild_routes(guild.id) == []

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


def test_verified_missing_proxy_cleanup_is_per_guild(isolated_router):
    a, b = FakeGuild(900), FakeGuild(901)

    async def check():
        source_a = await router.create_custom_share_source(a, None, "Gaming Clips")
        source_b = await router.create_custom_share_source(b, None, "Gaming Clips")
        await router.save_route(a.id, source_channel_id=source_a.id, target_channel_id=777)
        await router.save_route(b.id, source_channel_id=source_b.id, target_channel_id=888)
        # Simulate deleting a Discord source outside the bot's management UI.
        await source_a.delete()
        assert await router.prune_missing_custom_share_sources(a) == 1
        assert await router.guild_custom_share_sources(a.id) == []
        assert await router.guild_routes(a.id) == []
        assert await router.prune_missing_custom_share_sources(b) == 0
        assert await router.guild_custom_share_sources(b.id) == [source_b.id]
        assert (await router.guild_routes(b.id))[0]["target_channel_id"] == "888"

    asyncio.run(check())


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
    assert 'label="Remove Missing Proxies"' in ui
    assert "ShareRouterDeleteSourceConfirmView" in ui
    assert 'custom_id="dank:share_router:source"' in ui
    assert 'custom_id="dank:share_router:remove"' in ui
    assert 'custom_id="dank:share_router:delete_source"' in ui
    assert ui.count("DankGuildResourceBrowserView(") >= 4
    assert "choices=choices[:25]" not in ui
    assert 'route = route_for_source(routes, int(message.channel.id))' in runtime
    assert 'save = save_managed_share_route if delete_source else save_existing_share_route' in ui
    assert tuple(DEFAULT_SHARE_CHANNELS) == (
        "share-gaming-news", "share-deals", "share-memes", "share-announcements",
    )


def test_managed_proxy_always_cleans_even_with_historical_keep_source_route(isolated_router):
    guild = FakeGuild(1101)

    async def check():
        source = await router.create_custom_share_source(guild, None, "Gaming Clips")
        legacy_keep = {"source_channel_id": str(source.id), "delete_source": False}
        assert router.route_deletes_source(source, legacy_keep) is True
        ordinary_category = FakeCategory(90909, "ordinary", guild)
        ordinary = FakeTextChannel(guild, "normal-chat", ordinary_category, 90910)
        assert router.route_deletes_source(ordinary, legacy_keep) is False
        assert router.route_deletes_source(ordinary, {"delete_source": True}) is True
    asyncio.run(check())


def test_successful_delivery_deletes_source_without_queue(isolated_router):
    guild = FakeGuild(1102)
    deleted = []

    async def check():
        source = await router.create_custom_share_source(guild, None, "Gaming Clips")
        async def delete():
            deleted.append(source.id)
        message = SimpleNamespace(id=31337, guild=guild, channel=source, delete=delete)
        await router._cleanup_delivered_proxy_post(message, enabled=True)
        assert await router.guild_pending_source_cleanup_count(guild.id) == 0
        await router._cleanup_delivered_proxy_post(message, enabled=False)
        assert deleted == [source.id]

    asyncio.run(check())


def test_delivered_cleanup_retries_after_permission_failure_per_guild(isolated_router):
    guild, other = FakeGuild(1103), FakeGuild(1104)
    deleted = []
    permit = False

    async def check():
        nonlocal permit
        source = await router.create_custom_share_source(guild, None, "Gaming Clips")
        neighbor = await router.create_custom_share_source(other, None, "Gaming Clips")

        async def failed_delete(**_kwargs):
            raise OSError("Discord deletion temporarily unavailable")

        message = SimpleNamespace(id=31338, guild=guild, channel=source, delete=failed_delete)
        await router._cleanup_delivered_proxy_post(message, enabled=True)
        await router._cleanup_delivered_proxy_post(message, enabled=True)
        assert await router.guild_pending_source_cleanup_count(guild.id) == 1
        assert await router.guild_pending_source_cleanup_count(other.id) == 0

        guild.me = object()
        source.permissions_for = lambda _member: SimpleNamespace(manage_messages=permit)
        async def retry_delete():
            deleted.append(31338)
        source.get_partial_message = lambda message_id: SimpleNamespace(delete=retry_delete)

        bot = SimpleNamespace(get_guild=lambda gid: {guild.id:guild, other.id:other}.get(gid))
        assert await router._sweep_delivered_cleanup_once(bot) == 0
        assert deleted == []
        permit = True
        assert await router._sweep_delivered_cleanup_once(bot) == 1
        assert deleted == [31338]
        assert await router.guild_pending_source_cleanup_count(guild.id) == 0
        assert await router.guild_custom_share_sources(other.id) == [neighbor.id]

    asyncio.run(check())


def test_cleanup_does_not_delete_message_from_repurposed_channel(isolated_router):
    guild = FakeGuild(1105)

    async def check():
        source = await router.create_custom_share_source(guild, None, "Gaming Clips")
        async def failed_delete(**_kwargs):
            raise OSError("simulated")
        message = SimpleNamespace(id=31339, guild=guild, channel=source, delete=failed_delete)
        await router._cleanup_delivered_proxy_post(message, enabled=True)
        source.category = FakeCategory(50505, "ordinary", guild)
        # The route was removed after delivery, so the channel is no longer a proxy.
        assert await router.guild_pending_source_cleanup_count(guild.id) == 1
        guild.me = object()
        source.permissions_for = lambda _member: SimpleNamespace(manage_messages=True)
        async def unsafe_delete(**_kwargs):
            raise AssertionError("must not delete from repurposed channel")
        source.get_partial_message = lambda _id: SimpleNamespace(delete=unsafe_delete)
        bot = SimpleNamespace(get_guild=lambda _id: guild)
        assert await router._sweep_delivered_cleanup_once(bot) == 0
        assert await router.guild_pending_source_cleanup_count(guild.id) == 1
    asyncio.run(check())


def test_cleanup_retry_queue_rotates_fairly_between_guilds(isolated_router, monkeypatch):
    guild_a, guild_b = FakeGuild(1106), FakeGuild(1107)
    monkeypatch.setattr(router, "_SOURCE_CLEANUP_MAX_PER_PASS", 1)
    monkeypatch.setattr(router, "_SOURCE_CLEANUP_CURSOR", 0)

    async def failed_delete(**_kwargs):
        raise OSError("initial delete failed")

    async def check():
        source_a = await router.create_custom_share_source(guild_a, None, "Gaming Clips")
        source_b = await router.create_custom_share_source(guild_b, None, "Gaming Clips")
        for guild, source, message_id in ((guild_a, source_a, 901), (guild_b, source_b, 902)):
            message = SimpleNamespace(
                id=message_id, guild=guild, channel=source, delete=failed_delete
            )
            await router._cleanup_delivered_proxy_post(message, enabled=True)
            guild.me = object()

        source_a.permissions_for = lambda _member: SimpleNamespace(manage_messages=False)
        source_b.permissions_for = lambda _member: SimpleNamespace(manage_messages=True)
        removed = []
        async def delete_b():
            removed.append(902)
        source_b.get_partial_message = lambda _id: SimpleNamespace(delete=delete_b)
        bot = SimpleNamespace(
            get_guild=lambda guild_id: {guild_a.id: guild_a, guild_b.id: guild_b}.get(guild_id)
        )
        assert await router._sweep_delivered_cleanup_once(bot) == 0
        assert await router._sweep_delivered_cleanup_once(bot) == 1
        assert removed == [902]
        assert await router.guild_pending_source_cleanup_count(guild_a.id) == 1
        assert await router.guild_pending_source_cleanup_count(guild_b.id) == 0

    asyncio.run(check())


def test_source_cleanup_only_enqueues_successful_route_acks():
    root = Path(__file__).resolve().parents[1]
    runtime = (root / "stoney_verify/share_router_runtime.py").read_text(encoding="utf-8")
    ui = (root / "stoney_verify/commands_ext/public_share_router.py").read_text(encoding="utf-8")
    source = runtime.split("async def route_message(", 1)[1].split("def _merged_overwrite(", 1)[0]
    # discord.py 2.7.1 Message.delete/PartialMessage.delete accept no reason kwarg.
    assert "await message.delete()" in runtime
    assert "source.get_partial_message(message_id).delete()" in runtime
    assert "message.delete(reason=" not in runtime
    assert "get_partial_message(message_id).delete(reason=" not in runtime
    assert source.count("await _cleanup_delivered_proxy_post(message, enabled=cleanup_source)") == 1
    assert source.index("await _cleanup_delivered_proxy_post") > source.index("native_sent = await _relay_native_video_upload(")
    assert source.index("native_sent = await _relay_native_video_upload(") < source.rindex(
        "await _cleanup_delivered_proxy_post"
    )
    assert 'name="Automatic proxy cleanup"' in ui
    assert "delivered posts are removed" not in ui.lower() or "Failed shares" in ui


def test_failed_share_notifies_sender_without_deleting_original(isolated_router, monkeypatch):
    guild = FakeGuild(1200)
    guild.name = "Test Guild"
    notifications = []

    class Author:
        id = 303
        mention = "<@303>"

        async def send(self, content: str, **kwargs):
            notifications.append((content, kwargs))

    async def ignored_modlog(*_args, **_kwargs):
        return None
    monkeypatch.setattr(router, "_send_modlog", ignored_modlog)

    async def check():
        source = await router.create_custom_share_source(guild, None, "Gaming Clips")
        source.mention = "<#123>"
        message = SimpleNamespace(
            id=90001,
            guild=guild,
            channel=source,
            author=Author(),
            jump_url="https://discord.com/channels/1200/123/90001",
        )
        await router._route_rejection_log(
            message,
            target=None,
            reason="No destination is configured.",
        )
        assert len(notifications) == 1
        assert "No destination is configured" in notifications[0][0]
        assert "original post was kept" in notifications[0][0]
        assert "90001" in notifications[0][0]
        assert await router.guild_pending_source_cleanup_count(guild.id) == 0

    asyncio.run(check())


def test_failure_paths_preserve_proxy_messages_and_report_rejection():
    root = Path(__file__).resolve().parents[1]
    runtime = (root / "stoney_verify/share_router_runtime.py").read_text(encoding="utf-8")
    section = runtime.split("async def route_message(", 1)[1].split("def _merged_overwrite(", 1)[0]
    assert "This Share Router proxy has no destination configured." in section
    assert "No forwardable text, link, or attachment" in section
    assert "Destination delivery failed:" in section
    assert "await _route_rejection_log(" in section
    assert "Your original post was kept" in runtime


def test_repair_post_defer_render_failure_reports_explicit_error():
    """Guard against the user's infinite 'Dank Shield is thinking' UI."""
    source = (
        Path(__file__).resolve().parents[1]
        / "stoney_verify/commands_ext/public_share_router.py"
    ).read_text(encoding="utf-8")
    repair = source.split("async def repair(self, interaction:", 1)[1].split(
        '    @discord.ui.button(label="Add / Change Route"', 1
    )[0]
    assert "await interaction.response.defer(ephemeral=True, thinking=True)" in repair
    assert 'embed = await _share_router_embed(guild)' in repair
    assert "Hub repair ran, but Share Router could not display its status" in repair
    assert "share_router repair status failed" in repair
    assert "except discord.HTTPException:" in repair


def _ordinary_route_fixture(monkeypatch, router_module, *, guild_id=1666):
    """An ordinary public channel, not hidden Share Router infrastructure."""
    import discord

    guild = FakeGuild(guild_id)
    ordinary = FakeCategory(55001, "Community", guild)
    guild.categories.append(ordinary)
    source = FakeTextChannel(guild, "gaming-clips", ordinary, 55101)
    target = FakeTextChannel(guild, "games-and-media", ordinary, 55102)
    for channel in (source, target):
        ordinary.channels.append(channel)
        guild.channels[channel.id] = channel
        channel.nsfw = False
        channel.mention = f"<#{channel.id}>"
        channel.sent = []
        channel.fail = False

    class Member:
        id = 4343
        mention = "<@4343>"
        bot = False
        def __init__(self, guild):
            self.guild = guild

    member = Member(guild)
    guild.me = member

    def permissions(_channel, _actor):
        from types import SimpleNamespace
        return SimpleNamespace(
            view_channel=True, read_message_history=True,
            send_messages=True, manage_messages=False,
            attach_files=True, embed_links=True,
        )

    async def send(self, content="", **_kwargs):
        if self.fail:
            raise RuntimeError("simulated target outage")
        self.sent.append(content)
        from types import SimpleNamespace
        return SimpleNamespace(id=92929)

    monkeypatch.setattr(router_module.discord, "Member", Member)
    monkeypatch.setattr(FakeTextChannel, "permissions_for", permissions, raising=False)
    monkeypatch.setattr(FakeTextChannel, "is_nsfw", lambda ch: ch.nsfw, raising=False)
    monkeypatch.setattr(FakeTextChannel, "send", send, raising=False)
    return guild, source, target, member


def test_existing_channel_routes_are_non_destructive_and_guild_scoped(
    isolated_router, monkeypatch,
):
    guild, source, target, _member = _ordinary_route_fixture(monkeypatch, router)

    async def check():
        await router.save_existing_share_route(
            guild, source_channel_id=source.id,
            target_channel_id=target.id, created_by_id=12345,
        )
        routes = await router.guild_routes(guild.id)
        assert len(routes) == 1
        assert routes[0]["source_channel_id"] == str(source.id)
        assert routes[0]["target_channel_id"] == str(target.id)
        assert routes[0]["delete_source"] is False
        assert router.route_deletes_source(source, routes[0]) is False
        assert router.route_health(guild, routes[0]).ok
        assert guild.me and not source.deleted

        # Reconfiguration never silently converts an ordinary source to a
        # destructive proxy route. A second guild cannot use these channels.
        with pytest.raises(ValueError, match="different channels"):
            await router.save_existing_share_route(
                guild, source_channel_id=source.id,
                target_channel_id=source.id, created_by_id=12345,
            )
        other = FakeGuild(2666)
        with pytest.raises(ValueError, match="no longer exists"):
            await router.save_existing_share_route(
                other, source_channel_id=source.id,
                target_channel_id=target.id, created_by_id=12345,
            )
        assert len(await router.guild_routes(guild.id)) == 1
        assert await router.guild_routes(other.id) == []

    asyncio.run(check())


def test_existing_channel_saves_reject_age_leak_managed_hubs_and_permission_gaps(
    isolated_router, monkeypatch,
):
    guild, source, target, _member = _ordinary_route_fixture(monkeypatch, router, guild_id=1667)

    async def check():
        source.nsfw = True
        with pytest.raises(ValueError, match="age-restricted"):
            await router.save_existing_share_route(
                guild, source_channel_id=source.id,
                target_channel_id=target.id, created_by_id=5,
            )
        source.nsfw = False
        original = FakeTextChannel.permissions_for
        def source_blocked(ch, actor):
            if ch is source:
                from types import SimpleNamespace
                return SimpleNamespace(
                    view_channel=False, read_message_history=True,
                    manage_messages=False, send_messages=True,
                )
            return original(ch, actor)
        monkeypatch.setattr(FakeTextChannel, "permissions_for", source_blocked)
        with pytest.raises(ValueError, match="cannot view source"):
            await router.save_existing_share_route(
                guild, source_channel_id=source.id,
                target_channel_id=target.id, created_by_id=5,
            )
        monkeypatch.setattr(FakeTextChannel, "permissions_for", original)
        # Managed proxy channels are exclusively controlled by the existing UI.
        proxy = await router.create_custom_share_source(guild, None, "Extra Videos")
        with pytest.raises(ValueError, match="managed Share Router proxies"):
            await router.save_existing_share_route(
                guild, source_channel_id=proxy.id,
                target_channel_id=target.id, created_by_id=5,
            )
        with pytest.raises(ValueError, match="cannot be forwarding destinations"):
            await router.save_existing_share_route(
                guild, source_channel_id=source.id,
                target_channel_id=proxy.id, created_by_id=5,
            )
        assert await router.guild_routes(guild.id) == []

    asyncio.run(check())


def test_actual_existing_forwarding_keeps_original_even_on_duplicate_and_error(
    isolated_router, monkeypatch,
):
    from types import SimpleNamespace
    guild, source, target, member = _ordinary_route_fixture(monkeypatch, router, guild_id=1668)
    deleted = []
    rejected = []
    cleaned = []

    async def relay(*_args, **_kwargs):
        return False

    async def modlog(*_args, **_kwargs):
        return None

    async def rejection(*_args, **kwargs):
        rejected.append(kwargs.get("reason"))

    async def cleanup(_msg, *, enabled):
        cleaned.append(enabled)
        assert enabled is False

    monkeypatch.setattr(router, "_relay_native_video_upload", relay)
    monkeypatch.setattr(router, "_send_modlog", modlog)
    monkeypatch.setattr(router, "_route_rejection_log", rejection)
    monkeypatch.setattr(router, "_cleanup_delivered_proxy_post", cleanup)
    monkeypatch.setattr(router, "_RECENT_ROUTE_KEYS", {})

    def message(content="A gaming clip! https://example.com/video", message_id=121):
        async def delete(*_args, **_kwargs):
            deleted.append(message_id)
        return SimpleNamespace(
            id=message_id, guild=guild, channel=source, author=member,
            content=content, attachments=[], embeds=[], webhook_id=None,
            delete=delete,
        )

    async def check():
        await router.save_existing_share_route(
            guild, source_channel_id=source.id,
            target_channel_id=target.id, created_by_id=6,
            content_filter="everything",  # Explicit legacy all-post behavior
        )
        await router.route_message(message())
        assert len(target.sent) == 1
        assert "gaming clip" in target.sent[0].lower()
        assert deleted == []
        assert cleaned == [False]

        # Existing Router dedupe never deletes either original.
        await router.route_message(message(message_id=122))
        assert len(target.sent) == 1
        assert deleted == []
        assert cleaned == [False, False]

        # Existing sources never recursively copy webhook-originated posts.
        webhook = message(content="Webhook copy", message_id=125)
        webhook.webhook_id = 92827
        await router.route_message(webhook)
        assert len(target.sent) == 1
        assert deleted == []
        assert cleaned == [False, False]

        target.fail = True
        await router.route_message(message(content="Something else", message_id=123))
        assert len(target.sent) == 1
        assert deleted == []
        assert cleaned == [False, False]
        assert rejected and "Destination delivery failed" in rejected[-1]

        # Age restrictions are rechecked during runtime, not only on save.
        target.fail = False
        source.nsfw = True
        await router.route_message(message(content="Third clip", message_id=124))
        assert len(target.sent) == 1
        assert "age-restricted" in rejected[-1]
        assert deleted == []

    asyncio.run(check())


def test_existing_source_uses_shared_search_safe_browser_and_real_ui_controls():
    from stoney_verify.commands_ext import public_share_router as ui
    from discord import ui as discord_ui

    view = ui.ShareRouterView(42)
    buttons = {part.label for part in view.children if isinstance(part, discord_ui.Button)}
    assert "Use Existing Channel" in buttons
    assert "Create Proxy" in buttons
    assert "Add / Change Route" in buttons
    assert len(view.children) <= 25
    text = Path(ui.__file__).read_text(encoding="utf-8")
    assert 'custom_id="dank:share_router:existing_source"' in text
    assert 'resource_kinds=("text",)' in text
    assert "DankGuildResourceBrowserView(" in text
    assert "await _open_existing_source_picker(interaction)" in text
    assert "delete_source=False" in text
    assert "route_source_blockers(" in text
    assert "route_permission_blockers(" in text
    assert "save_existing_share_route" in text
    assert "discord.ui.ChannelSelect" not in text



def test_inflight_legacy_cleanup_honors_newly_saved_keep_original_mode(
    isolated_router,
):
    from types import SimpleNamespace

    guild = FakeGuild(1670)
    ordinary = FakeCategory(55100, "Public Posts", guild)
    source = FakeTextChannel(guild, "my-existing-source", ordinary, 55155)
    guild.channels[source.id] = source
    deleted = []

    async def delete():
        deleted.append(source.id)

    message = SimpleNamespace(
        id=31337, guild=guild, channel=source, delete=delete,
    )

    async def check():
        # An in-flight legacy route had previously captured delete_source=True.
        await router.save_route(
            guild.id, source_channel_id=source.id,
            target_channel_id=888, delete_source=True,
        )
        # Staff switched the channel to Keep Original before delivery ended.
        await router.save_route(
            guild.id, source_channel_id=source.id,
            target_channel_id=999, delete_source=False,
        )
        await router._cleanup_delivered_proxy_post(message, enabled=True)
        assert deleted == []
        assert await router.guild_pending_source_cleanup_count(guild.id) == 0

        # An actual still-destructive legacy route can preserve its old behavior.
        await router.save_route(
            guild.id, source_channel_id=source.id,
            target_channel_id=888, delete_source=True,
        )
        await router._cleanup_delivered_proxy_post(message, enabled=True)
        assert deleted == [source.id]

    asyncio.run(check())



def test_content_filter_matcher_ignores_chat_and_classifies_media_without_network():
    from types import SimpleNamespace as SN

    def msg(content="", *, attachments=None, embeds=None):
        return SN(content=content, attachments=attachments or [], embeds=embeds or [])

    video = SN(filename="clip.MP4", content_type="video/mp4")
    image = SN(filename="image.PNG", content_type="image/png")
    gif = SN(filename="animated.gif", content_type="image/gif")
    image_embed = SN(type="image", image=SN(url="https://img.example.net/a.jpg"), url="https://img.example.net/a.jpg", video=None)
    preview_embed = SN(type="rich", image=SN(url="https://cdn.example.net/thumb.jpg"), video=None, url="https://news.example.net/story")
    video_embed = SN(type="video", video=SN(url="https://cdn.example.net/clip.mp4"), image=None, url=None)
    assert router.route_content_matches(msg("How was your night?"), "everything")
    for mode in ("videos", "images", "links"):
        assert not router.route_content_matches(msg("How was your night?"), mode)
    assert router.route_content_matches(msg(attachments=[video]), "videos")
    assert not router.route_content_matches(msg(attachments=[video]), "links")
    assert router.route_content_matches(msg(embeds=[video_embed]), "videos")
    assert router.route_content_matches(msg(attachments=[image]), "images")
    assert router.route_content_matches(msg(attachments=[gif]), "images")
    assert not router.route_content_matches(msg(attachments=[gif]), "videos")
    assert router.route_content_matches(msg(embeds=[image_embed]), "images")
    assert not router.route_content_matches(msg(embeds=[preview_embed]), "images")
    assert router.route_content_matches(msg("https://youtube.com/shorts/abcDEF"), "videos")
    assert router.route_content_matches(msg("https://youtu.be/xyz123"), "videos")
    assert router.route_content_matches(msg("https://www.tiktok.com/@member/video/7654321"), "videos")
    assert router.route_content_matches(msg("https://instagram.com/reel/abc123"), "videos")
    assert router.route_content_matches(msg("https://facebook.com/watch?v=123"), "videos")
    for facebook_video_share in (
        "https://www.facebook.com/share/v/Abc123_-/?mibextid=something",
        "https://m.facebook.com/share/r/ABCabc123/?mibextid=foo",
        "https://web.facebook.com/share/video/ABCabc123/",
        "https://facebook.com/share/reel/abc123/",
        "https://www.facebook.com/video.php?v=123456789",
        "https://fb.watch/AbC123/",
    ):
        assert router.route_content_matches(msg(facebook_video_share), "videos"), facebook_video_share
    for facebook_not_known_video in (
        "https://www.facebook.com/share/p/photo123/",
        "https://www.facebook.com/share/post/post123/",
        "https://www.facebook.com/groups/123/permalink/456/",
        "https://www.facebook.com/profile.php?id=123",
    ):
        assert not router.route_content_matches(
            msg(facebook_not_known_video), "videos"
        ), facebook_not_known_video
    assert not router.route_content_matches(msg("https://instagram.com/p/photo123"), "videos")
    assert not router.route_content_matches(msg("https://x.com/someone/status/123"), "videos")
    assert not router.route_content_matches(msg("https://example.com/news"), "videos")
    assert not router.route_content_matches(msg("https://www.tiktok.com/@somebody"), "videos")
    assert not router.route_content_matches(msg("https://vimeo.com/user123"), "videos")
    assert not router.route_content_matches(msg("https://streamable.com/login"), "videos")
    assert router.route_content_matches(msg("https://example.com/photo.webp"), "images")
    assert router.route_content_matches(msg("https://example.com/clip.mp4"), "videos")
    assert router.route_content_matches(msg("https://example.com/news"), "links")
    assert router.route_content_matches(msg(embeds=[preview_embed]), "links")
    assert not router.route_content_matches(msg("ordinary chat"), "links")
    assert not router.route_content_matches(msg("https://example.com/news"), "images")
    for bad in ("UNKNOWN", "all", "video", "", True, {"mode": "everything"}):
        assert not router.route_content_matches(msg("Anything"), bad)
        with pytest.raises(ValueError):
            router.validate_route_content_filter(bad)
    assert router.validate_route_content_filter(None) == "everything"


def test_existing_route_defaults_to_video_only_but_saved_legacy_routes_stay_everything(
    isolated_router, monkeypatch,
):
    guild, source, target, _member = _ordinary_route_fixture(monkeypatch, router, guild_id=1671)

    async def check():
        await router.save_existing_share_route(
            guild, source_channel_id=source.id,
            target_channel_id=target.id, created_by_id=44,
        )
        latest = (await router.guild_routes(guild.id))[0]
        assert latest["content_filter"] == "videos"
        assert latest["delete_source"] is False
        await router.save_existing_share_route(
            guild, source_channel_id=source.id,
            target_channel_id=target.id, created_by_id=44,
            content_filter="images",
        )
        saved = (await router.guild_routes(guild.id))[0]
        assert saved["content_filter"] == "images"
        await router.save_route(
            guild.id, source_channel_id=source.id, target_channel_id=target.id,
            delete_source=False, created_by_id=44,
        )
        legacy = (await router.guild_routes(guild.id))[0]
        assert legacy["content_filter"] == "everything"
        # A preexisting file with NO content_filter also remains Everything.
        data = router._load_all_unlocked()
        data[str(guild.id)]["routes"][0].pop("content_filter", None)
        router._save_all_unlocked(data)
        older = (await router.guild_routes(guild.id))[0]
        assert "content_filter" not in older
        assert router.route_content_matches(
            type("Message", (), {"content": "hello", "attachments": [], "embeds": []})(),
            older.get("content_filter"),
        )
        with pytest.raises(ValueError, match="Everything"):
            await router.save_route(
                guild.id, source_channel_id=source.id, target_channel_id=target.id,
                content_filter="not_valid",
            )
        assert (await router.guild_routes(guild.id))[0].get("content_filter") is None

    asyncio.run(check())


def test_filtered_existing_route_skips_chat_while_forwarding_video_link_without_cleanup(
    isolated_router, monkeypatch,
):
    from types import SimpleNamespace as SN
    guild, source, target, member = _ordinary_route_fixture(monkeypatch, router, guild_id=1672)
    calls = []
    deleted = []

    async def fake_relay(*args, **kwargs):
        calls.append("media")
        return False

    async def silent_modlog(*_args, **_kwargs):
        return None

    async def reject(*_args, **_kwargs):
        calls.append("rejected")

    async def cleanup(_msg, *, enabled):
        calls.append("cleanup")
        assert enabled is False

    monkeypatch.setattr(router, "_relay_native_video_upload", fake_relay)
    monkeypatch.setattr(router, "_send_modlog", silent_modlog)
    monkeypatch.setattr(router, "_route_rejection_log", reject)
    monkeypatch.setattr(router, "_cleanup_delivered_proxy_post", cleanup)
    monkeypatch.setattr(router, "_RECENT_ROUTE_KEYS", {})

    def message(content, i):
        async def delete():
            deleted.append(i)
        return SN(
            id=i, guild=guild, channel=source, author=member, content=content,
            attachments=[], embeds=[], webhook_id=None, delete=delete,
        )

    async def check():
        await router.save_existing_share_route(
            guild, source_channel_id=source.id,
            target_channel_id=target.id, created_by_id=44,
        )
        await router.route_message(message("Hey everyone, what are you playing?", 1))
        await router.route_message(message("https://example.org/news", 2))
        assert target.sent == [] and calls == []
        await router.route_message(message("https://youtu.be/ABCD123456", 3))
        assert len(target.sent) == 1
        assert calls.count("media") == 1
        assert calls.count("cleanup") == 1
        assert deleted == []

        # Facebook short-video share-sheet URLs must pass Videos Only before
        # the existing extractor/fallback is invoked.
        await router.route_message(message("https://www.facebook.com/share/v/Abc123/", 4))
        assert len(target.sent) == 2
        assert calls.count("media") == 2
        assert calls.count("cleanup") == 2
        assert deleted == []

        # Photo share links are not unconditionally treated as videos.
        await router.route_message(message("https://facebook.com/share/p/Photo123/", 5))
        assert len(target.sent) == 2
        assert calls.count("media") == 2

    asyncio.run(check())


def test_content_filter_ui_has_selectable_four_modes_and_preserves_proxy_defaults():
    from stoney_verify.commands_ext import public_share_router as ui
    import discord

    class Source:
        guild = type("Guild", (), {"id": 100})()
        name = "gaming-clips"

    class Browser:
        pass

    source = Source()
    existing = ui.ShareRouterTargetLandingView(
        42, source=source, browser=Browser(), delete_source=False,
        content_filter="videos",
    )
    proxy = ui.ShareRouterTargetLandingView(
        42, source=source, browser=Browser(), delete_source=True,
        content_filter="everything",
    )
    for view, current in ((existing, "videos"), (proxy, "everything")):
        selects = [part for part in view.children if isinstance(part, discord.ui.Select)]
        assert len(selects) == 1
        assert {opt.value for opt in selects[0].options} == {
            "videos", "images", "links", "everything",
        }
        assert [opt.value for opt in selects[0].options if opt.default] == [current]
    assert existing.back.label == "Back to Existing Channels"
    embed = ui._target_options_embed(source, delete_source=False, content_filter="videos")
    assert "Videos Only" in str(embed.to_dict())
    saved_code = Path(ui.__file__).read_text()
    assert "content_filter=content_filter" in saved_code
    assert "save = save_managed_share_route if delete_source else save_existing_share_route" in saved_code



def test_filtered_proxy_mismatch_does_not_delete_source_or_attempt_delivery(
    isolated_router, monkeypatch,
):
    from types import SimpleNamespace as SN
    guild, source, target, member = _ordinary_route_fixture(monkeypatch, router, guild_id=1673)
    deleted = []
    calls = []

    async def delete():
        deleted.append(True)

    async def fail_relay(*_args, **_kwargs):
        calls.append("relay")
        raise AssertionError("Ignored messages cannot reach the video engine")

    monkeypatch.setattr(router, "_relay_native_video_upload", fail_relay)

    async def check():
        await router.save_route(
            guild.id, source_channel_id=source.id,
            target_channel_id=target.id, delete_source=True,
            content_filter="videos",
        )
        msg = SN(id=1, guild=guild, channel=source, author=member,
                 content="Just checking in", attachments=[], embeds=[],
                 webhook_id=None, delete=delete)
        await router.route_message(msg)
        assert calls == []
        assert deleted == []
        assert target.sent == []

    asyncio.run(check())


def test_invalid_saved_filter_is_never_due_to_forward_and_health_flags_it(
    isolated_router, monkeypatch,
):
    from types import SimpleNamespace as SN
    guild, source, target, member = _ordinary_route_fixture(monkeypatch, router, guild_id=1674)

    async def check():
        await router.save_route(
            guild.id, source_channel_id=source.id,
            target_channel_id=target.id, delete_source=False,
            content_filter="videos",
        )
        data = router._load_all_unlocked()
        data[str(guild.id)]["routes"][0]["content_filter"] = "bad-version"
        router._save_all_unlocked(data)
        route = (await router.guild_routes(guild.id))[0]
        assert any(
            "Invalid saved content filter" in reason
            for reason in router.route_health(guild, route).blockers
        )
        msg = SN(id=1, guild=guild, channel=source, author=member,
                 content="https://youtu.be/video123", attachments=[], embeds=[],
                 webhook_id=None)
        await router.route_message(msg)
        assert target.sent == []
    asyncio.run(check())
