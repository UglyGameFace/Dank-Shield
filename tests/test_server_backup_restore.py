from __future__ import annotations

import asyncio
import itertools
from types import SimpleNamespace

import discord
import pytest

from stoney_verify.services import server_backup_restore as restore
from stoney_verify.services import server_backup_service as backups

# ---------------------------------------------------------------------------
# Mutable fake guild harness: fake objects implement the serializer-facing
# attributes plus the async mutation surface the apply engine drives, and
# record every mutation as an event dict on the guild (in order).
# ---------------------------------------------------------------------------

_ID_COUNTER = itertools.count(1)


def _next_id() -> int:
    return 1514374910000000000 + next(_ID_COUNTER) * 1000


class FakeRole:
    def __init__(
        self,
        role_id,
        name,
        *,
        position=0,
        permissions=0,
        managed=False,
        default=False,
        color=0,
        hoist=False,
        mentionable=False,
    ):
        self.id = role_id
        self.name = name
        self.position = position
        self.permissions = discord.Permissions(permissions)
        self.colour = discord.Colour(color)
        self.managed = managed
        self.hoist = hoist
        self.mentionable = mentionable
        self._default = default
        self.guild: FakeGuild | None = None

    def is_default(self):
        return self._default

    async def edit(self, **kwargs):
        self.guild.mutate({"op": "role_edit", "id": self.id, "name": self.name})
        if "name" in kwargs:
            self.name = kwargs["name"]
        if "colour" in kwargs:
            self.colour = kwargs["colour"]
        if "hoist" in kwargs:
            self.hoist = kwargs["hoist"]
        if "mentionable" in kwargs:
            self.mentionable = kwargs["mentionable"]
        if "permissions" in kwargs:
            self.permissions = kwargs["permissions"]
        return self

    async def delete(self, **kwargs):
        self.guild.mutate({"op": "role_delete", "id": self.id, "name": self.name})
        self.guild.roles.remove(self)


class FakeChannel:
    def __init__(
        self,
        channel_id,
        name,
        channel_type,
        *,
        category=None,
        position=0,
        topic=None,
        nsfw=False,
        slowmode_delay=0,
        bitrate=None,
        user_limit=None,
        rtc_region=None,
        video_quality_mode=None,
        overwrites=None,
        available_tags=None,
        default_sort_order=None,
        default_layout=None,
        default_thread_slowmode_delay=0,
        default_reaction_emoji=None,
        default_auto_archive_duration=1440,
    ):
        self.id = channel_id
        self.name = name
        self.type = channel_type
        self.category = category
        self.position = position
        self.topic = topic
        self.nsfw = nsfw
        self.slowmode_delay = slowmode_delay
        self.bitrate = bitrate
        self.user_limit = user_limit
        self.rtc_region = rtc_region
        self.video_quality_mode = video_quality_mode
        self.overwrites = dict(overwrites or {})
        self.available_tags = list(available_tags or [])
        self.default_sort_order = default_sort_order
        self.default_layout = default_layout
        self.default_forum_layout = default_layout
        self.default_thread_slowmode_delay = default_thread_slowmode_delay
        self.default_reaction_emoji = default_reaction_emoji
        self.default_auto_archive_duration = default_auto_archive_duration
        self.guild: FakeGuild | None = None

    async def edit(self, **kwargs):
        self.guild.mutate({"op": "channel_edit", "id": self.id, "name": self.name})
        for field_name in (
            "name",
            "position",
            "category",
            "topic",
            "nsfw",
            "slowmode_delay",
            "bitrate",
            "user_limit",
            "rtc_region",
            "video_quality_mode",
            "overwrites",
            "type",
            "available_tags",
            "default_sort_order",
            "default_layout",
            "default_thread_slowmode_delay",
            "default_reaction_emoji",
            "default_auto_archive_duration",
        ):
            if field_name in kwargs:
                setattr(self, field_name, kwargs[field_name])
        return self

    async def delete(self, **kwargs):
        op = "category_delete" if self.type == discord.ChannelType.category else "channel_delete"
        self.guild.mutate({"op": op, "id": self.id, "name": self.name})
        self.guild._remove_channel(self)


class FakeMember:
    def __init__(self, roles, permissions):
        self.roles = list(roles)
        self.guild_permissions = permissions
        self.top_role = max(self.roles, key=lambda r: r.position, default=None)


class FakeGuild:
    def __init__(self, guild_id, *, name="Test Guild"):
        self.id = guild_id
        self.name = name
        self.description = None
        self.preferred_locale = "en-US"
        self.icon = None
        self.banner = None
        self.features = []
        self.verification_level = discord.VerificationLevel.low
        self.explicit_content_filter = discord.ContentFilter.no_role
        self.default_notifications = discord.NotificationLevel.all_messages
        self.afk_timeout = 300
        self.afk_channel = None
        self.system_channel = None
        self.rules_channel = None
        self.public_updates_channel = None
        self.roles: list[FakeRole] = []
        self.channels: list[FakeChannel] = []
        self.categories: list[FakeChannel] = []
        self.events: list[dict] = []
        self.fail_hook = None
        bot_role = self.add_role(
            _next_id(), "Dank Shield", position=50, permissions=8, managed=True
        )
        self.me = FakeMember([bot_role], discord.Permissions(administrator=True))

    # -- harness plumbing ----------------------------------------------------
    def mutate(self, event):
        if self.fail_hook is not None:
            self.fail_hook(event)
        self.events.append(event)

    def add_role(self, role_id, name, **kwargs):
        role = FakeRole(role_id, name, **kwargs)
        role.guild = self
        self.roles.append(role)
        return role

    def add_channel(self, channel_id, name, channel_type, **kwargs):
        channel = FakeChannel(channel_id, name, channel_type, **kwargs)
        channel.guild = self
        self.channels.append(channel)
        if channel_type == discord.ChannelType.category:
            self.categories.append(channel)
        return channel

    def _remove_channel(self, channel):
        if channel in self.channels:
            self.channels.remove(channel)
        if channel in self.categories:
            self.categories.remove(channel)

    @property
    def default_role(self):
        return next((r for r in self.roles if r._default), None)

    def get_channel(self, channel_id):
        return next((c for c in self.channels if c.id == channel_id), None)

    def get_role(self, role_id):
        return next((r for r in self.roles if r.id == role_id), None)

    async def fetch_channels(self):
        return list(self.channels)

    async def fetch_roles(self):
        return list(self.roles)

    # -- mutation surface used by the apply engine ---------------------------
    async def create_role(self, **kwargs):
        self.mutate({"op": "role_create", "name": kwargs.get("name")})
        role = FakeRole(
            _next_id(),
            kwargs.get("name", ""),
            position=1,
            permissions=kwargs["permissions"].value if kwargs.get("permissions") else 0,
            color=kwargs["colour"].value if kwargs.get("colour") else 0,
            hoist=bool(kwargs.get("hoist")),
            mentionable=bool(kwargs.get("mentionable")),
        )
        role.guild = self
        self.roles.append(role)
        return role

    async def create_category(self, name, *, overwrites=None, reason=None, position=None):
        self.mutate({"op": "category_create", "name": name})
        return self.add_channel(
            _next_id(),
            name,
            discord.ChannelType.category,
            position=position or 0,
            overwrites=overwrites,
        )

    async def create_text_channel(self, name, **kwargs):
        self.mutate({"op": "channel_create", "name": name})
        return self.add_channel(
            _next_id(),
            name,
            discord.ChannelType.news if kwargs.get("news") else discord.ChannelType.text,
            category=kwargs.get("category"),
            position=kwargs.get("position") or 0,
            topic=kwargs.get("topic"),
            nsfw=bool(kwargs.get("nsfw")),
            slowmode_delay=kwargs.get("slowmode_delay") or 0,
            overwrites=kwargs.get("overwrites"),
        )

    async def create_voice_channel(self, name, **kwargs):
        self.mutate({"op": "channel_create", "name": name})
        return self.add_channel(
            _next_id(),
            name,
            discord.ChannelType.voice,
            category=kwargs.get("category"),
            position=kwargs.get("position") or 0,
            nsfw=bool(kwargs.get("nsfw")),
            bitrate=kwargs.get("bitrate"),
            user_limit=kwargs.get("user_limit"),
            rtc_region=kwargs.get("rtc_region"),
            video_quality_mode=kwargs.get("video_quality_mode"),
            overwrites=kwargs.get("overwrites"),
        )

    async def create_forum(self, name, **kwargs):
        self.mutate({"op": "channel_create", "name": name})
        return self.add_channel(
            _next_id(),
            name,
            discord.ChannelType.forum,
            category=kwargs.get("category"),
            position=kwargs.get("position") or 0,
            topic=kwargs.get("topic"),
            nsfw=bool(kwargs.get("nsfw")),
            slowmode_delay=kwargs.get("slowmode_delay") or 0,
            overwrites=kwargs.get("overwrites"),
            available_tags=kwargs.get("available_tags"),
            default_sort_order=kwargs.get("default_sort_order"),
            default_layout=kwargs.get("default_layout"),
            default_thread_slowmode_delay=kwargs.get("default_thread_slowmode_delay") or 0,
            default_reaction_emoji=kwargs.get("default_reaction_emoji"),
            default_auto_archive_duration=kwargs.get("default_auto_archive_duration") or 1440,
        )

    async def edit(self, **kwargs):
        kwargs.pop("reason", None)
        self.mutate({"op": "guild_edit", "name": ",".join(sorted(kwargs))})
        for field_name, value in kwargs.items():
            setattr(self, field_name, value)
        return self

    async def edit_role_positions(self, positions, *, reason=None):
        self.mutate(
            {
                "op": "role_positions",
                "name": ",".join(str(r.id) for r in positions),
            }
        )
        for role, position in positions.items():
            role.position = position
        return list(positions)


def make_guild(guild_id=None):
    """A guild with just @everyone + the bot role, ready to be shaped."""
    guild = FakeGuild(guild_id or _next_id())
    guild.add_role(guild.id, "@everyone", position=0, permissions=100, default=True)
    return guild


def snapshot_of(guild):
    return backups.serialize_guild_structure(guild, actor_id=42)


def rows_by_key(plan):
    return {row["key"]: row for row in plan}


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------


def test_plan_identical_structure_is_all_skip():
    guild = make_guild()
    verified = guild.add_role(_next_id(), "Verified", position=5, permissions=200)
    category = guild.add_channel(_next_id(), "Lobby", discord.ChannelType.category, position=0)
    guild.add_channel(
        _next_id(), "general", discord.ChannelType.text,
        category=category, position=1, topic="Main chat",
        overwrites={verified: discord.PermissionOverwrite(view_channel=True)},
    )
    plan = restore.build_restore_plan(guild, snapshot_of(guild))
    assert plan, "plan should list the matched rows"
    assert all(row["action"] == "skip" for row in plan), plan
    assert all(row["status"] == "unchanged" for row in plan)
    assert not any(row["action"] == "delete_candidate" for row in plan)


def test_plan_matches_by_name_and_category():
    source = make_guild()
    source.add_role(_next_id(), "Verified", position=5, permissions=200)
    source_cat = source.add_channel(_next_id(), "Lobby", discord.ChannelType.category, position=0)
    source.add_channel(_next_id(), "general", discord.ChannelType.text,
                       category=source_cat, position=1, topic="Main chat")
    source.add_channel(_next_id(), "quiet", discord.ChannelType.text,
                       category=source_cat, position=2, topic="Same")
    snapshot = snapshot_of(source)

    live = make_guild()  # every id differs; names carry the match
    live.add_role(_next_id(), "Verified", position=5, permissions=200)
    live_cat = live.add_channel(_next_id(), "Lobby", discord.ChannelType.category, position=0)
    live.add_channel(_next_id(), "general", discord.ChannelType.text,
                     category=live_cat, position=1, topic="Edited topic")
    live.add_channel(_next_id(), "quiet", discord.ChannelType.text,
                     category=live_cat, position=2, topic="Same")

    rows = rows_by_key(restore.build_restore_plan(live, snapshot))
    general = next(r for r in rows.values() if r["kind"] == "channel" and r["after"]["name"] == "general")
    quiet = next(r for r in rows.values() if r["kind"] == "channel" and r["after"]["name"] == "quiet")
    assert general["action"] == "update" and general["target_id"] is not None
    assert quiet["action"] == "skip"
    assert not any(row["action"] == "create" for row in rows.values())


def test_plan_ambiguous_name_is_blocked():
    source = make_guild()
    source_cat = source.add_channel(_next_id(), "Lobby", discord.ChannelType.category, position=0)
    source.add_channel(_next_id(), "general", discord.ChannelType.text,
                       category=source_cat, position=1, topic="Main")
    snapshot = snapshot_of(source)

    live = make_guild()
    live_cat = live.add_channel(_next_id(), "Lobby", discord.ChannelType.category, position=0)
    live.add_channel(_next_id(), "general", discord.ChannelType.text,
                     category=live_cat, position=1, topic="One")
    live.add_channel(_next_id(), "general", discord.ChannelType.text,
                     category=live_cat, position=2, topic="Two")

    plan = restore.build_restore_plan(live, snapshot)
    channel_rows = [r for r in plan if r["kind"] == "channel" and r["action"] == "blocked"]
    assert len(channel_rows) == 1
    assert channel_rows[0]["status"] == "failed"


def test_plan_managed_role_is_skip_and_never_a_delete_candidate():
    source = make_guild()
    source.add_role(_next_id(), "Integration", position=7, permissions=0, managed=True)
    snapshot = snapshot_of(source)

    live = make_guild()
    live_managed = live.add_role(_next_id(), "Integration", position=7, permissions=999, managed=True)
    live.add_role(_next_id(), "Other Bot", position=8, permissions=0, managed=True)

    plan = restore.build_restore_plan(live, snapshot)
    managed_rows = [r for r in plan if r["kind"] == "role" and r["after"] and r["after"]["name"] == "Integration"]
    assert managed_rows and managed_rows[0]["action"] == "skip"
    assert not any(
        row["action"] == "delete_candidate" and row["target_id"] == str(live_managed.id)
        for row in plan
    )
    assert not any(
        row["action"] == "delete_candidate" and (row["before"] or {}).get("managed")
        for row in plan
    )


def test_plan_hierarchy_blocks_roles_at_or_above_the_bot():
    source = make_guild()
    source.add_role(_next_id(), "High New", position=12, permissions=8)
    existing_id = _next_id()
    source.add_role(existing_id, "Existing High", position=15, permissions=8, color=111)
    snapshot = snapshot_of(source)

    live = make_guild()
    live.me.top_role = next(r for r in live.roles if r.name == "Dank Shield")
    live.me.top_role.position = 10
    live.add_role(existing_id, "Existing High", position=15, permissions=8, color=222)

    rows = rows_by_key(restore.build_restore_plan(live, snapshot))
    high_new = next(r for r in rows.values() if (r["after"] or {}).get("name") == "High New")
    existing = rows[f"role:{existing_id}"]
    assert high_new["action"] == "blocked"
    assert existing["action"] == "blocked"
    assert any("R3" in blocker for blocker in existing["blockers"])


def test_plan_delete_candidate_exclusions():
    source = make_guild()
    moved_id = _next_id()
    main_cat = source.add_channel(_next_id(), "Main", discord.ChannelType.category, position=0)
    source.add_channel(moved_id, "pinned", discord.ChannelType.text,
                       category=main_cat, position=1, topic="Pinned")
    snapshot = snapshot_of(source)

    live = make_guild()
    keep_cat = live.add_channel(_next_id(), "Keep", discord.ChannelType.category, position=3)
    live.add_channel(moved_id, "pinned", discord.ChannelType.text,
                     category=keep_cat, position=1, topic="Pinned")
    extra = live.add_channel(_next_id(), "extra", discord.ChannelType.text, position=9)
    own_role = live.add_role(_next_id(), "Bot Helper", position=3, permissions=0)
    live.me.roles.append(own_role)  # non-managed bot role: protected via me.roles

    plan = restore.build_restore_plan(live, snapshot)
    candidates = {row["key"] for row in plan if row["action"] == "delete_candidate"}
    assert f"chan:{extra.id}" in candidates
    assert f"role:{own_role.id}" not in candidates
    keep_row = next(row for row in plan if row["key"] == f"cat:{keep_cat.id}")
    assert keep_row["action"] == "blocked"  # still holds the staying "pinned"
    assert not any(
        row["action"] == "delete_candidate" and (row["before"] or {}).get("is_default")
        for row in plan
    )


def test_plan_modes_missing_and_selected():
    source = make_guild()
    role = source.add_role(_next_id(), "Greeter", position=4, permissions=16)
    category = source.add_channel(_next_id(), "Lobby", discord.ChannelType.category, position=0)
    source.add_channel(
        _next_id(), "welcome", discord.ChannelType.text, category=category, position=1,
        overwrites={role: discord.PermissionOverwrite(view_channel=True)},
    )
    source.add_channel(_next_id(), "other", discord.ChannelType.text,
                       category=category, position=2)
    source.add_role(_next_id(), "Changed", position=6, permissions=1)
    snapshot = snapshot_of(source)

    live = make_guild()
    live.add_role(_next_id(), "Changed", position=6, permissions=2)
    live.add_channel(_next_id(), "stale", discord.ChannelType.text, position=8)

    missing = restore.build_restore_plan(live, snapshot, mode="missing")
    assert not any(row["action"] == "delete_candidate" for row in missing)
    changed_row = next(r for r in missing if (r["after"] or {}).get("name") == "Changed")
    assert changed_row["action"] == "skip"
    assert any("missing-only" in b for b in changed_row["blockers"])
    assert any(row["action"] == "create" for row in missing)

    welcome_key = next(
        entry["key"] for entry in snapshot["channels"] if entry["name"] == "welcome"
    )
    selected = restore.build_restore_plan(
        live, snapshot, mode="selected", selected_keys={welcome_key}
    )
    selected_keys = {row["key"] for row in selected}
    assert welcome_key in selected_keys
    # Dependencies pulled in: the category create and the overwrite role create.
    assert any(row["kind"] == "category" and row["action"] == "create" for row in selected)
    assert any(
        (row["after"] or {}).get("name") == "Greeter" and row["action"] == "create"
        for row in selected
    )
    assert not any((row["after"] or {}).get("name") == "other" for row in selected)


def test_plan_role_limit_blocks_excess_creates(monkeypatch):
    monkeypatch.setattr(restore, "ROLE_LIMIT", 3)
    source = make_guild()
    source.add_role(_next_id(), "One", position=3, permissions=0)
    source.add_role(_next_id(), "Two", position=4, permissions=0)
    snapshot = snapshot_of(source)

    live = make_guild()  # @everyone + bot role = 2 live roles; cap is 3
    plan = restore.build_restore_plan(live, snapshot)
    creates = [r for r in plan if r["kind"] == "role" and r["action"] == "create"]
    blocked = [r for r in plan if r["kind"] == "role" and r["action"] == "blocked"]
    assert len(creates) == 1
    assert len(blocked) == 1
    assert any("role limit" in b for b in blocked[0]["blockers"])


def test_plan_stage_channel_is_blocked_and_verification_level_is_its_own_row():
    source = make_guild()
    source.add_channel(_next_id(), "Live Talk", discord.ChannelType.stage_voice, position=5)
    source.verification_level = discord.VerificationLevel.high
    snapshot = snapshot_of(source)

    live = make_guild()
    plan = restore.build_restore_plan(live, snapshot)
    stage_rows = [r for r in plan if r["kind"] == "channel"]
    assert stage_rows and stage_rows[0]["action"] == "blocked"
    setting_rows = [r for r in plan if r["kind"] == "guild_setting"]
    assert [r["key"] for r in setting_rows] == ["setting:verification_level"]


# ---------------------------------------------------------------------------
# Apply engine
# ---------------------------------------------------------------------------


@pytest.fixture
def storage(monkeypatch):
    """Fake Slice 1 storage: no Postgres, full call recording."""
    calls = {"states": [], "created": []}

    async def fake_create_backup(guild, *, created_by, label):
        guild.events.append({"op": "auto_backup", "name": label})
        calls["created"].append(label)
        return {"backup_id": 777}

    async def fake_update_state(guild_id, backup_id, state):
        calls["states"].append(dict(state) if state else state)
        return True

    monkeypatch.setattr(backups, "create_backup", fake_create_backup)
    monkeypatch.setattr(backups, "update_restore_state", fake_update_state)
    monkeypatch.setattr(backups, "list_backups_sync", lambda guild_id, *, limit=10: ())
    return calls


def _run_apply(guild, plan, **kwargs):
    kwargs.setdefault("user_id", 42)
    kwargs.setdefault("backup_id", 5)
    kwargs.setdefault("delay_seconds", 0)
    return asyncio.run(restore.apply_restore_plan(guild, plan, **kwargs))


def _rich_source():
    """The structure the backup describes."""
    source = make_guild()
    verified = source.add_role(_next_id(), "Verified", position=5, permissions=200,
                               color=3066993, hoist=True)
    source.add_role(_next_id(), "Mod", position=6, permissions=300)
    category = source.add_channel(_next_id(), "Lobby", discord.ChannelType.category, position=0)
    source.add_channel(_next_id(), "general", discord.ChannelType.text,
                       category=category, position=1, topic="Main chat",
                       overwrites={verified: discord.PermissionOverwrite(view_channel=True)})
    voice = source.add_channel(_next_id(), "Lounge", discord.ChannelType.voice,
                               category=category, position=2, bitrate=64000, user_limit=0)
    source.add_channel(
        _next_id(), "recipes", discord.ChannelType.forum, category=category, position=3,
        topic="Guidelines", available_tags=[
            SimpleNamespace(name="Vodka", moderated=False,
                            emoji=SimpleNamespace(id=None, name="🍸")),
        ],
        default_sort_order=discord.ForumOrderType.creation_date,
        default_layout=discord.ForumLayoutType.list_view,
        default_thread_slowmode_delay=30,
        default_reaction_emoji=SimpleNamespace(id=None, name="🍹"),
    )
    source.verification_level = discord.VerificationLevel.medium
    source.afk_channel = voice
    return source


def _rich_live():
    """The same guild, drifted: Mod and Lounge deleted, Verified and
    general edited, settings lowered."""
    live = make_guild()
    live.add_role(_next_id(), "Verified", position=5, permissions=200,
                  color=123, hoist=False)
    category = live.add_channel(_next_id(), "Lobby", discord.ChannelType.category, position=0)
    live.add_channel(_next_id(), "general", discord.ChannelType.text,
                     category=category, position=1, topic="Old topic")
    live.add_channel(
        _next_id(), "recipes", discord.ChannelType.forum, category=category, position=3,
        topic="Guidelines", available_tags=[
            SimpleNamespace(name="Vodka", moderated=False,
                            emoji=SimpleNamespace(id=None, name="🍸")),
        ],
        default_sort_order=discord.ForumOrderType.creation_date,
        default_layout=discord.ForumLayoutType.list_view,
        default_thread_slowmode_delay=30,
        default_reaction_emoji=SimpleNamespace(id=None, name="🍹"),
    )
    return live


def test_apply_happy_path(storage):
    snapshot = snapshot_of(_rich_source())
    live = _rich_live()
    plan = restore.build_restore_plan(live, snapshot)
    result = _run_apply(live, plan)

    assert result.ok, result.failure
    assert result.pre_restore_backup_id == 777
    assert live.events[0]["op"] == "auto_backup"  # R5 before any mutation
    names = {role.name for role in live.roles}
    assert "Mod" in names
    mod = next(r for r in live.roles if r.name == "Mod")
    assert mod.permissions.value == 300
    verified = next(r for r in live.roles if r.name == "Verified")
    assert verified.colour.value == 3066993 and verified.hoist is True
    general = next(c for c in live.channels if c.name == "general")
    assert general.topic == "Main chat"
    assert verified in general.overwrites  # overwrite restored onto the matched role
    lounge = next(c for c in live.channels if c.name == "Lounge")
    assert lounge.bitrate == 64000
    assert live.verification_level == discord.VerificationLevel.medium
    assert live.afk_channel is lounge  # setting resolved to the created channel
    assert storage["states"][0]["status"] == "running"
    assert storage["states"][-1]["status"] == "done"
    assert set(result.applied) == set(storage["states"][-1]["applied_keys"])


def test_apply_role_positions_pass(storage):
    source = make_guild()
    role_a = source.add_role(_next_id(), "Alpha", position=3, permissions=10)
    role_b = source.add_role(_next_id(), "Beta", position=4, permissions=20)
    snapshot = snapshot_of(source)

    live = make_guild()
    live_a = live.add_role(role_a.id, "Alpha", position=8, permissions=10)
    live_b = live.add_role(role_b.id, "Beta", position=9, permissions=20)
    plan = restore.build_restore_plan(live, snapshot)
    result = _run_apply(live, plan)

    assert result.ok, result.failure
    assert (live_a.position, live_b.position) == (3, 4)
    assert any(event["op"] == "role_positions" for event in live.events)


def test_preflight_aborts_with_zero_mutations(storage):
    snapshot = snapshot_of(_rich_source())
    live = _rich_live()
    plan = restore.build_restore_plan(live, snapshot)
    general = next(c for c in live.channels if c.name == "general")
    general.name = "renamed-after-preview"  # drift between plan and apply

    errors = asyncio.run(restore.preflight_restore(live, plan))
    assert errors and any("general" in e or "chan:" in e for e in errors)

    result = _run_apply(live, plan)
    assert not result.ok
    assert live.events == []  # no Discord mutation, not even the auto-backup
    assert storage["created"] == []


def test_apply_never_deletes_without_confirmation(storage):
    source = make_guild()
    source.add_channel(_next_id(), "general", discord.ChannelType.text, position=1)
    snapshot = snapshot_of(source)

    live = make_guild()
    live.add_channel(_next_id(), "general", discord.ChannelType.text, position=1)
    extra = live.add_channel(_next_id(), "extra", discord.ChannelType.text, position=9)
    plan = restore.build_restore_plan(live, snapshot)
    assert any(row["action"] == "delete_candidate" for row in plan)

    result = _run_apply(live, plan)  # delete_confirmed defaults to False
    assert result.ok, result.failure
    assert extra in live.channels
    assert result.delete_results == []
    assert result.skipped_deletes == [f"chan:{extra.id}"]
    assert not any(e["op"] == "channel_delete" for e in live.events)


def test_apply_deletes_last_only_with_confirmation(storage):
    source = make_guild()
    source.add_channel(_next_id(), "general", discord.ChannelType.text, position=1, topic="New")
    snapshot = snapshot_of(source)

    live = make_guild()
    live.add_channel(_next_id(), "general", discord.ChannelType.text, position=1, topic="Old")
    extra = live.add_channel(_next_id(), "extra", discord.ChannelType.text, position=9)
    plan = restore.build_restore_plan(live, snapshot)

    result = _run_apply(live, plan, delete_confirmed=True)
    assert result.ok, result.failure
    assert extra not in live.channels
    assert result.delete_results and result.delete_results[0]["ok"] is True
    assert live.events[-1]["op"] == "channel_delete"  # deletes run last


def test_apply_compensates_on_mid_plan_failure(storage):
    source = make_guild()
    source.add_role(_next_id(), "Mod", position=6, permissions=300, color=555)
    source.add_channel(_next_id(), "first", discord.ChannelType.text, position=1)
    source.add_channel(_next_id(), "second", discord.ChannelType.text, position=2)
    snapshot = snapshot_of(source)

    live = make_guild()
    live_mod = live.add_role(_next_id(), "Mod", position=6, permissions=300, color=111)

    def fail_on_second(event):
        if event["op"] == "channel_create" and event["name"] == "second":
            raise RuntimeError("Discord said no")

    live.fail_hook = fail_on_second
    plan = restore.build_restore_plan(live, snapshot)
    result = _run_apply(live, plan)

    assert not result.ok
    assert "second" in result.failure or "chan:" in result.failure
    assert live_mod.colour.value == 111  # update reverted
    assert not any(c.name == "first" for c in live.channels)  # create deleted
    assert set(result.compensated) == {
        next(r["key"] for r in plan if (r["after"] or {}).get("name") == "Mod"),
        next(r["key"] for r in plan if (r["after"] or {}).get("name") == "first"),
    }
    assert result.residual == []
    final_state = storage["states"][-1]
    assert final_state["status"] == "failed"
    assert final_state["failed_key"]


def test_apply_pre_restore_backup_failure_aborts(storage, monkeypatch):
    async def exploding_create_backup(guild, *, created_by, label):
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(backups, "create_backup", exploding_create_backup)
    snapshot = snapshot_of(_rich_source())
    live = _rich_live()
    plan = restore.build_restore_plan(live, snapshot)
    result = _run_apply(live, plan)
    assert not result.ok
    assert "pre-restore auto-backup" in result.failure.lower()
    assert live.events == []


def test_apply_in_flight_guard(storage, monkeypatch):
    monkeypatch.setattr(
        backups,
        "list_backups_sync",
        lambda guild_id, *, limit=10: (
            {"backup_id": 99, "restore_state": {"status": "running"}},
        ),
    )
    snapshot = snapshot_of(_rich_source())
    live = _rich_live()
    plan = restore.build_restore_plan(live, snapshot)
    result = _run_apply(live, plan)
    assert not result.ok
    assert "already in flight" in result.failure
    assert live.events == []


def test_resume_skips_already_matching_rows(storage):
    snapshot = snapshot_of(_rich_source())
    live = _rich_live()
    # Simulate the first half of an earlier restore: Mod already recreated
    # exactly as in the backup before the run was interrupted.
    live.add_role(_next_id(), "Mod", position=6, permissions=300)
    plan = restore.build_restore_plan(live, snapshot)
    mod_row = next(r for r in plan if (r["after"] or {}).get("name") == "Mod")
    assert mod_row["action"] == "skip"  # resume replans applied work as skip

    result = _run_apply(live, plan)
    assert result.ok, result.failure
    assert not any(
        event["op"] == "role_create" and event["name"] == "Mod"
        for event in live.events
    )
    assert any(c.name == "Lounge" for c in live.channels)


def test_preflight_blocks_delete_of_config_referenced_channel(storage, monkeypatch):
    source = make_guild()
    source.add_channel(_next_id(), "general", discord.ChannelType.text, position=1)
    snapshot = snapshot_of(source)

    live = make_guild()
    live.add_channel(_next_id(), "general", discord.ChannelType.text, position=1)
    extra = live.add_channel(_next_id(), "verify-here", discord.ChannelType.text, position=9)

    async def fake_config(guild_id, *, refresh=False):
        return {"verification_channel_id": extra.id}

    monkeypatch.setattr(
        "stoney_verify.guild_config.get_guild_config", fake_config
    )
    plan = restore.build_restore_plan(live, snapshot)

    errors = asyncio.run(restore.preflight_restore(live, plan, delete_confirmed=True))
    assert any("can never delete" in e for e in errors)

    result = _run_apply(live, plan, delete_confirmed=True)
    assert not result.ok
    assert extra in live.channels
    assert live.events == []

    # Without confirmation the same plan is inert and applies cleanly.
    result = _run_apply(live, plan)
    assert result.ok, result.failure
    assert extra in live.channels


def test_apply_cancel_leaves_applied_rows_and_resumable_state(storage):
    snapshot = snapshot_of(_rich_source())
    live = _rich_live()
    plan = restore.build_restore_plan(live, snapshot)
    checks = itertools.count(1)

    def cancel_after_first():
        return next(checks) > 1

    result = _run_apply(live, plan, cancel_check=cancel_after_first)
    assert not result.ok
    assert "cancelled" in result.failure
    assert len(result.applied) == 1  # first row stayed applied, no compensation
    assert result.compensated == []
    assert storage["states"][-1]["status"] == "cancelled"
