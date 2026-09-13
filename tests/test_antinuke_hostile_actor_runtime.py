from __future__ import annotations

import asyncio
from types import SimpleNamespace

from stoney_verify import anti_nuke_hostile_actor_runtime as runtime


class FakeMember:
    def __init__(self, user_id: int, guild=None, *, bot: bool = False) -> None:
        self.id = user_id
        self.guild = guild
        self.bot = bot
        self.mention = f"<@{user_id}>"

    def __str__(self) -> str:
        return f"user-{self.id}"


class FakeGuild:
    def __init__(self, member: FakeMember | None = None) -> None:
        self.id = 123
        self.owner_id = 999
        self._member = member
        self.banned: list[int] = []
        self.kicked: list[int] = []

    def get_member(self, user_id: int):
        if self._member is not None and int(self._member.id) == int(user_id):
            return self._member
        return None

    async def ban(self, target, reason=None) -> None:
        _ = reason
        self.banned.append(int(target.id))

    async def kick(self, target, reason=None) -> None:
        _ = reason
        self.kicked.append(int(target.id))


def _reset_runtime(monkeypatch, tmp_path) -> None:
    runtime._MEMORY.clear()
    runtime._NEGATIVE_CACHE.clear()
    runtime._ACTOR_LOCKS.clear()
    runtime._TABLE_AVAILABLE = None
    monkeypatch.setenv(
        "DANK_SECURITY_REPUTATION_FILE",
        str(tmp_path / "security_actor_reputation.json"),
    )
    monkeypatch.setattr(runtime, "get_supabase", lambda: None)


def test_confirmed_hostile_reputation_survives_memory_reset(monkeypatch, tmp_path) -> None:
    _reset_runtime(monkeypatch, tmp_path)

    row = asyncio.run(
        runtime.mark_confirmed_hostile(
            123,
            55,
            reason="confirmed destructive action",
            is_bot=True,
        )
    )
    assert row["active"] is True
    assert row["is_bot"] is True
    assert row["incident_count"] == 1

    runtime._MEMORY.clear()
    restored = asyncio.run(runtime.get_actor_reputation(123, 55))
    assert restored is not None
    assert restored["active"] is True
    assert restored["user_id"] == 55
    assert restored["classification"] == "confirmed_destructive_actor"


def test_antinuke_containment_bans_and_records_confirmed_actor(monkeypatch, tmp_path) -> None:
    _reset_runtime(monkeypatch, tmp_path)

    member = FakeMember(55, bot=True)
    guild = FakeGuild(member)
    member.guild = guild
    fallback_calls: list[int] = []

    async def fallback(_guild, actor, *, reason):
        _ = reason
        fallback_calls.append(int(actor.id))
        return ["fallback kick"], []

    fake_antinuke = SimpleNamespace(
        _contain_actor=fallback,
        antinuke_permission_health=lambda _guild, _settings=None: [],
        normalize_antinuke_settings=lambda settings: dict(settings or {}),
        bot=SimpleNamespace(user=SimpleNamespace(id=777)),
    )

    runtime._patch_antinuke(fake_antinuke)
    removed, blocked = asyncio.run(
        fake_antinuke._contain_actor(
            guild,
            member,
            reason="confirmed destructive action",
        )
    )

    assert removed == ["banned member from server"]
    assert blocked == []
    assert guild.banned == [55]
    assert fallback_calls == []

    saved = asyncio.run(runtime.get_actor_reputation(123, 55))
    assert saved is not None
    assert saved["active"] is True
    assert saved["is_bot"] is True


def test_antinuke_containment_keeps_canonical_fallback_when_ban_fails(
    monkeypatch,
    tmp_path,
) -> None:
    _reset_runtime(monkeypatch, tmp_path)

    class BanFailGuild(FakeGuild):
        async def ban(self, target, reason=None) -> None:
            _ = target, reason
            raise RuntimeError("missing ban permission")

    member = FakeMember(55)
    guild = BanFailGuild(member)
    member.guild = guild
    fallback_calls: list[int] = []

    async def fallback(_guild, actor, *, reason):
        _ = reason
        fallback_calls.append(int(actor.id))
        return ["removed member from server"], []

    fake_antinuke = SimpleNamespace(
        _contain_actor=fallback,
        antinuke_permission_health=lambda _guild, _settings=None: [],
        normalize_antinuke_settings=lambda settings: dict(settings or {}),
        bot=SimpleNamespace(user=SimpleNamespace(id=777)),
    )

    runtime._patch_antinuke(fake_antinuke)
    removed, blocked = asyncio.run(
        fake_antinuke._contain_actor(guild, member, reason="destructive action")
    )

    assert removed == ["removed member from server"]
    assert blocked == []
    assert fallback_calls == [55]
    saved = asyncio.run(runtime.get_actor_reputation(123, 55))
    assert saved is not None and saved["active"] is True


def test_contain_readiness_requires_ban_members() -> None:
    async def unused(*args, **kwargs):
        raise AssertionError("unused")

    fake_antinuke = SimpleNamespace(
        _contain_actor=unused,
        antinuke_permission_health=lambda _guild, _settings=None: [],
        normalize_antinuke_settings=lambda settings: dict(settings or {}),
        bot=SimpleNamespace(user=SimpleNamespace(id=777)),
    )
    runtime._patch_antinuke(fake_antinuke)

    guild = SimpleNamespace(
        me=SimpleNamespace(
            guild_permissions=SimpleNamespace(
                administrator=False,
                ban_members=False,
            )
        )
    )
    missing = fake_antinuke.antinuke_permission_health(
        guild,
        {"antinuke_enabled": True, "antinuke_mode": "contain"},
    )
    assert "Ban Members" in missing


def test_exact_known_hostile_is_enforced_on_rejoin(monkeypatch) -> None:
    guild = FakeGuild()
    member = FakeMember(55, guild, bot=True)
    guild._member = member

    async def reputation(guild_id, user_id, *, refresh=False):
        assert (guild_id, user_id, refresh) == (123, 55, True)
        return {
            "guild_id": 123,
            "user_id": 55,
            "active": True,
            "classification": "confirmed_destructive_actor",
        }

    enforced: list[int] = []

    async def enforce(found, row, *, anti_nuke, source, settings=None):
        _ = row, anti_nuke, source, settings
        enforced.append(int(found.id))
        return True

    monkeypatch.setattr(runtime, "get_actor_reputation", reputation)
    monkeypatch.setattr(runtime, "_enforce_reputation_member", enforce)

    asyncio.run(runtime._on_member_join(member))
    assert enforced == [55]


def test_explicit_clear_is_not_recreated_from_old_alt_evidence(monkeypatch) -> None:
    guild = FakeGuild()
    member = FakeMember(66, guild, bot=False)
    guild._member = member

    async def reputation(_guild_id, _user_id, *, refresh=False):
        _ = refresh
        return {
            "guild_id": 123,
            "user_id": 66,
            "active": False,
            "classification": "confirmed_hostile_identity_link",
            "cleared_by": 999,
        }

    async def should_not_link(*args, **kwargs):
        _ = args, kwargs
        raise AssertionError("cleared exact ID must not be re-inherited from old evidence")

    async def should_not_enforce(*args, **kwargs):
        _ = args, kwargs
        raise AssertionError("cleared exact ID must not be enforced")

    monkeypatch.setattr(runtime, "get_actor_reputation", reputation)
    monkeypatch.setattr(runtime, "_confirmed_hostile_link", should_not_link)
    monkeypatch.setattr(runtime, "_enforce_reputation_member", should_not_enforce)

    asyncio.run(runtime._on_member_join(member))


def test_only_hard_identity_link_inherits_hostile_disposition(monkeypatch) -> None:
    from stoney_verify import raidguard

    guild = FakeGuild()
    member = FakeMember(66, guild, bot=False)
    guild._member = member

    def hard_context(guild_id: int, user_id: int):
        assert (guild_id, user_id) == (123, 66)
        return {
            "proof_matches": [{"user_id": 55}],
            "manual_confirmed": [],
            "manual_likely": [{"user_id": 77}],
            "manual_not_linked_ids": set(),
        }

    async def reputation(guild_id, user_id, *, refresh=False):
        _ = refresh
        if (guild_id, user_id) == (123, 55):
            return {
                "guild_id": 123,
                "user_id": 55,
                "active": True,
                "classification": "confirmed_destructive_actor",
            }
        return None

    recorded: list[tuple[int, int, int | None]] = []

    async def mark(guild_id, user_id, **kwargs):
        recorded.append((guild_id, user_id, kwargs.get("related_user_id")))
        return {
            "guild_id": guild_id,
            "user_id": user_id,
            "active": True,
            "classification": kwargs["classification"],
            "related_user_id": kwargs.get("related_user_id"),
        }

    enforced: list[int] = []

    async def enforce(found, row, *, anti_nuke, source, settings=None):
        _ = row, anti_nuke, source, settings
        enforced.append(int(found.id))
        return True

    monkeypatch.setattr(raidguard, "_load_hard_identity_context", hard_context)
    monkeypatch.setattr(runtime, "get_actor_reputation", reputation)
    monkeypatch.setattr(runtime, "mark_confirmed_hostile", mark)
    monkeypatch.setattr(runtime, "_enforce_reputation_member", enforce)

    asyncio.run(runtime._on_member_join(member))

    assert recorded == [(123, 66, 55)]
    assert enforced == [66]


def test_db_clear_overrides_stale_local_active_record(monkeypatch) -> None:
    active_local = {
        55: {
            "guild_id": 123,
            "user_id": 55,
            "active": True,
            "classification": "confirmed_destructive_actor",
            "source": "antinuke",
            "incident_count": 1,
        }
    }
    inactive_db = {
        "guild_id": 123,
        "user_id": 55,
        "active": False,
        "classification": "confirmed_destructive_actor",
        "source": "owner_clear",
        "incident_count": 1,
        "cleared_by": 999,
    }

    monkeypatch.setattr(runtime, "_read_local_guild_records", lambda _gid: active_local)
    monkeypatch.setattr(
        runtime,
        "_fetch_db_guild_records_sync",
        lambda _gid: ("ok", [inactive_db]),
    )
    monkeypatch.setattr(runtime, "_write_local_record", lambda _row: True)
    runtime._MEMORY.clear()

    rows = asyncio.run(runtime.list_active_reputations(123))

    assert rows == []
    assert runtime._MEMORY[(123, 55)]["active"] is False


def test_message_backstop_does_not_exempt_known_hostile_bot(monkeypatch) -> None:
    from stoney_verify import anti_nuke

    guild = FakeGuild()
    member = FakeMember(55, guild, bot=True)
    guild._member = member
    runtime._MEMORY[(123, 55)] = {
        "guild_id": 123,
        "user_id": 55,
        "active": True,
        "classification": "confirmed_destructive_actor",
    }

    async def settings(_guild_id):
        return {"antinuke_enabled": True, "antinuke_mode": "contain"}

    enforced: list[int] = []

    async def enforce(found, row, *, anti_nuke, source, settings=None):
        _ = row, anti_nuke, source, settings
        enforced.append(int(found.id))
        return True

    class FakeMessage:
        def __init__(self):
            self.guild = guild
            self.author = member
            self.deleted = False

        async def delete(self):
            self.deleted = True

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", settings)
    monkeypatch.setattr(runtime, "_enforce_reputation_member", enforce)

    message = FakeMessage()
    asyncio.run(runtime._on_message_known_hostile(message))

    assert message.deleted is True
    assert enforced == [55]


def test_message_backstop_respects_alert_only_mode(monkeypatch) -> None:
    from stoney_verify import anti_nuke

    guild = FakeGuild()
    member = FakeMember(55, guild, bot=True)
    runtime._MEMORY[(123, 55)] = {
        "guild_id": 123,
        "user_id": 55,
        "active": True,
        "classification": "confirmed_destructive_actor",
    }

    async def settings(_guild_id):
        return {"antinuke_enabled": True, "antinuke_mode": "alert"}

    class FakeMessage:
        def __init__(self):
            self.guild = guild
            self.author = member
            self.deleted = False

        async def delete(self):
            self.deleted = True

    monkeypatch.setattr(anti_nuke, "get_antinuke_settings", settings)
    message = FakeMessage()
    asyncio.run(runtime._on_message_known_hostile(message))
    assert message.deleted is False


def test_owner_readding_known_hostile_bot_is_still_blocked(monkeypatch) -> None:
    guild = FakeGuild()
    target = FakeMember(55, guild, bot=True)
    owner = FakeMember(999, guild, bot=False)
    entry = SimpleNamespace(target=target)

    async def original(_guild, _entry, _actor):
        raise AssertionError("known hostile target must bypass ordinary bot-add path")

    fake_guardian = SimpleNamespace(_handle_bot_add=original)
    incidents: list[str] = []

    async def settings(_guild_id):
        return {"antinuke_enabled": True, "antinuke_mode": "contain"}

    async def contain(_guild, _actor, *, reason):
        _ = reason
        raise AssertionError("guild owner must never be contained")

    async def post(_guild, **kwargs):
        incidents.append(str(kwargs.get("title")))

    fake_antinuke = SimpleNamespace(
        get_antinuke_settings=settings,
        _contain_actor=contain,
        _post_incident=post,
        bot=SimpleNamespace(user=SimpleNamespace(id=777)),
    )

    async def reputation(_guild_id, _user_id, *, refresh=False):
        _ = refresh
        return {
            "active": True,
            "classification": "confirmed_destructive_actor",
        }

    monkeypatch.setattr(runtime, "get_actor_reputation", reputation)
    runtime._patch_guardian(fake_guardian, fake_antinuke)

    asyncio.run(fake_guardian._handle_bot_add(guild, entry, owner))

    assert guild.banned == [55]
    assert incidents == ["🛑 Known Hostile Bot Re-add Detected"]
