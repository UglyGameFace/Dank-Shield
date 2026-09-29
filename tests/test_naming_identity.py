from __future__ import annotations

import asyncio
from types import SimpleNamespace

from stoney_verify import guild_config
from stoney_verify.services import naming_identity
from stoney_verify.services import server_design_studio as design


class _FakeResource:
    def __init__(self, resource_id: int, name: str, *, position: int = 1) -> None:
        self.id = int(resource_id)
        self.name = str(name)
        self.position = int(position)

    def is_default(self) -> bool:
        return False


class _FakeGuild:
    def __init__(
        self,
        *,
        roles: list[_FakeResource] | None = None,
        channels: list[_FakeResource] | None = None,
        categories: list[_FakeResource] | None = None,
    ) -> None:
        self.id = 999
        self.roles = list(roles or [])
        self.channels = list(channels or [])
        self.text_channels = list(channels or [])
        self.categories = list(categories or [])


class _FakeBot:
    def __init__(self) -> None:
        self.listeners: list[tuple[object, str]] = []

    def add_listener(self, callback: object, event_name: str) -> None:
        self.listeners.append((callback, event_name))


def _styled(value: str, style: str = "double_struck") -> str:
    rendered, _substitutions = design.transform_text_safe(value, style)
    assert rendered != value
    return rendered


def test_semantic_key_decodes_styled_role_letters_and_keeps_meaning() -> None:
    styled = f"🌿・{_styled('Stoner')}"
    assert naming_identity.semantic_key(styled) == "stoner"


def test_search_safe_display_name_preserves_decoration_but_normalizes_letters() -> None:
    styled = f"🌿・{_styled('Stoner')}"
    assert naming_identity.search_safe_display_name(styled) == "🌿・Stoner"
    assert naming_identity.has_stylized_search_text(styled) is True


def test_style_only_rename_needs_no_persisted_alias() -> None:
    styled = f"✅・{_styled('Verified')}"
    assert naming_identity.previous_alias_for_rename("✅・Verified", styled) == ""


def test_semantic_rename_keeps_previous_search_name() -> None:
    assert naming_identity.previous_alias_for_rename("✅・Verified", "✅・Members") == "verified"


def test_search_safe_policy_survives_bounded_alias_updates() -> None:
    state = {
        "version": naming_identity.NAMING_IDENTITY_VERSION,
        "policy": {"mode": naming_identity.NAMING_MODE_SEARCH_SAFE},
        "records": {},
    }
    state = naming_identity.remember_alias(
        state,
        kind="role",
        resource_id=1,
        alias="verified",
        updated_at=1.0,
    )
    assert naming_identity.naming_policy(state)["mode"] == naming_identity.NAMING_MODE_SEARCH_SAFE

    state = naming_identity.forget_resource(state, kind="role", resource_id=1)
    assert naming_identity.naming_policy(state)["mode"] == naming_identity.NAMING_MODE_SEARCH_SAFE


def test_unknown_policy_mode_fails_closed_to_preserve() -> None:
    policy = naming_identity.naming_policy({"policy": {"mode": "surprise-mode"}})
    assert policy["mode"] == naming_identity.NAMING_MODE_PRESERVE
    assert policy["roles"] is True
    assert policy["channels"] is True
    assert policy["categories"] is False


def test_alias_history_and_resource_records_are_strictly_bounded() -> None:
    state: dict[str, object] = {}
    for index, alias in enumerate(("one", "two", "three", "four"), start=1):
        state = naming_identity.remember_alias(
            state,
            kind="role",
            resource_id=1,
            alias=alias,
            updated_at=float(index),
        )

    aliases = naming_identity.aliases_for(state, kind="role", resource_id=1)
    assert aliases == ("four", "three", "two")
    assert len(aliases) == naming_identity.MAX_ALIASES_PER_RESOURCE

    for resource_id in range(2, naming_identity.MAX_TRACKED_RESOURCES + 4):
        state = naming_identity.remember_alias(
            state,
            kind="channel",
            resource_id=resource_id,
            alias=f"channel-{resource_id}",
            updated_at=float(resource_id + 10),
        )

    records = dict(state["records"])  # type: ignore[index]
    assert len(records) == naming_identity.MAX_TRACKED_RESOURCES
    assert "role:1" not in records


def test_process_state_cache_is_bounded_across_many_guilds(monkeypatch) -> None:
    naming_identity._STATE_CACHE.clear()  # noqa: SLF001
    monkeypatch.setattr(naming_identity, "MAX_CACHED_GUILDS", 3)

    for guild_id in range(1, 8):
        naming_identity._cache_state(  # noqa: SLF001
            guild_id,
            naming_identity.remember_alias(
                {},
                kind="role",
                resource_id=guild_id,
                alias=f"role-{guild_id}",
                updated_at=float(guild_id),
            ),
        )

    assert len(naming_identity._STATE_CACHE) == 3  # noqa: SLF001
    naming_identity._STATE_CACHE.clear()  # noqa: SLF001


def test_live_semantic_search_finds_styled_role_without_db_alias_lookup() -> None:
    role = _FakeResource(123, f"✅・{_styled('Verified')}", position=50)
    matches = naming_identity._search_resources(  # noqa: SLF001
        [role],
        kind="role",
        current="Verified",
        state=None,
    )
    assert matches == [role]


def test_saved_previous_alias_finds_resource_after_real_semantic_rename() -> None:
    role = _FakeResource(123, f"🌿・{_styled('Lounge')}", position=50)
    state = naming_identity.remember_alias(
        {},
        kind="role",
        resource_id=role.id,
        alias="Stoner",
        updated_at=1.0,
    )
    matches = naming_identity._search_resources(  # noqa: SLF001
        [role],
        kind="role",
        current="Stoner",
        state=state,
    )
    assert matches == [role]


def test_role_autocomplete_returns_styled_role_for_plain_query_without_history_read(monkeypatch) -> None:
    role = _FakeResource(123, f"✅・{_styled('Verified')}", position=50)
    guild = _FakeGuild(roles=[role])
    interaction = SimpleNamespace(guild=guild)

    async def fail_load(_guild_id: int):
        raise AssertionError("live semantic matches must not hit durable alias storage")

    monkeypatch.setattr(naming_identity, "_load_state", fail_load)
    choices = asyncio.run(naming_identity.role_autocomplete(interaction, "Verified"))

    assert len(choices) == 1
    assert choices[0].value == "123"
    assert "Verified" in choices[0].name


def test_runtime_is_event_driven_and_never_registers_an_on_ready_scan() -> None:
    bot = _FakeBot()
    assert naming_identity.install_naming_identity_runtime(bot) is True
    assert naming_identity.install_naming_identity_runtime(bot) is False

    events = [event for _callback, event in bot.listeners]
    assert events == [
        "on_guild_role_create",
        "on_guild_role_update",
        "on_guild_channel_create",
        "on_guild_channel_update",
        "on_guild_role_delete",
        "on_guild_channel_delete",
    ]
    assert "on_ready" not in events


def test_style_only_gateway_rename_does_not_queue_persistence(monkeypatch) -> None:
    scheduled: list[int] = []
    monkeypatch.setattr(naming_identity, "_queue_flush", lambda guild_id: scheduled.append(guild_id))
    naming_identity._PENDING_ALIASES.clear()  # noqa: SLF001

    styled = f"✅・{_styled('Verified')}"
    queued = naming_identity.queue_rename(
        guild_id=999,
        kind="role",
        resource_id=123,
        before_name="✅・Verified",
        after_name=styled,
    )

    assert queued is False
    assert scheduled == []
    assert naming_identity._PENDING_ALIASES == {}  # noqa: SLF001


def test_native_runtime_discovery_recognizes_styled_role_and_channel_names() -> None:
    verified = _FakeResource(1, f"✅・{_styled('Verified')}")
    verify_channel = _FakeResource(2, f"✅・{_styled('verify')}")
    guild = _FakeGuild(roles=[verified], channels=[verify_channel])

    assert guild_config._find_role_by_names(guild, ["verified"]) is verified  # noqa: SLF001
    assert guild_config._find_text_channel_by_names(guild, ["verify"]) is verify_channel  # noqa: SLF001


def test_rapid_alias_flush_preserves_event_chronology(monkeypatch) -> None:
    naming_identity._PENDING_ALIASES.clear()  # noqa: SLF001
    naming_identity._PENDING_DELETES.clear()  # noqa: SLF001
    naming_identity._FLUSH_TASKS.clear()  # noqa: SLF001
    monkeypatch.setattr(naming_identity, "PERSIST_DEBOUNCE_SECONDS", 0.0)
    monkeypatch.setattr(naming_identity, "_queue_flush", lambda _guild_id: None)

    assert naming_identity.queue_rename(
        guild_id=999,
        kind="role",
        resource_id=42,
        before_name="Alpha",
        after_name="Beta",
    )
    assert naming_identity.queue_rename(
        guild_id=999,
        kind="role",
        resource_id=42,
        before_name="Beta",
        after_name="Charlie",
    )
    assert naming_identity.queue_rename(
        guild_id=999,
        kind="role",
        resource_id=42,
        before_name="Charlie",
        after_name="Delta",
    )

    captured: dict[str, object] = {}

    async def fake_load(_guild_id: int):
        return {}

    async def fake_persist(_guild_id: int, state):
        captured["state"] = dict(state)

    monkeypatch.setattr(naming_identity, "_load_state", fake_load)
    monkeypatch.setattr(naming_identity, "_persist_state", fake_persist)

    asyncio.run(naming_identity._debounced_flush(999))  # noqa: SLF001

    state = captured["state"]
    assert isinstance(state, dict)
    assert naming_identity.aliases_for(state, kind="role", resource_id=42) == (
        "charlie",
        "beta",
        "alpha",
    )


def test_failed_alias_persistence_requeues_old_events_ahead_of_newer_events(monkeypatch) -> None:
    naming_identity._PENDING_ALIASES.clear()  # noqa: SLF001
    naming_identity._PENDING_DELETES.clear()  # noqa: SLF001
    naming_identity._FLUSH_TASKS.clear()  # noqa: SLF001
    naming_identity._PENDING_ALIASES[999] = {"role:42": ["alpha"]}  # noqa: SLF001
    naming_identity._PENDING_DELETES[999] = {"channel:88"}  # noqa: SLF001
    monkeypatch.setattr(naming_identity, "PERSIST_DEBOUNCE_SECONDS", 0.0)

    scheduled: list[int] = []
    monkeypatch.setattr(
        naming_identity,
        "_queue_flush",
        lambda guild_id: scheduled.append(int(guild_id)),
    )

    async def fake_load(_guild_id: int):
        return {}

    async def fail_after_new_event(_guild_id: int, _state):
        naming_identity._PENDING_ALIASES.setdefault(999, {})["role:42"] = ["beta"]  # noqa: SLF001
        raise RuntimeError("temporary database outage")

    monkeypatch.setattr(naming_identity, "_load_state", fake_load)
    monkeypatch.setattr(naming_identity, "_persist_state", fail_after_new_event)

    asyncio.run(naming_identity._debounced_flush(999))  # noqa: SLF001

    assert naming_identity._PENDING_ALIASES[999]["role:42"] == ["alpha", "beta"]  # noqa: SLF001
    assert naming_identity._PENDING_DELETES[999] == {"channel:88"}  # noqa: SLF001
    assert scheduled == [999]


def test_exact_saved_alias_outranks_unrelated_partial_live_role_match(monkeypatch) -> None:
    partial = _FakeResource(1, "Verified Team", position=50)
    alias_target = _FakeResource(2, "Members", position=40)
    guild = _FakeGuild(roles=[partial, alias_target])
    interaction = SimpleNamespace(guild=guild)

    state = naming_identity.remember_alias(
        {},
        kind="role",
        resource_id=alias_target.id,
        alias="Verified",
        updated_at=1.0,
    )

    async def fake_load(_guild_id: int):
        return state

    monkeypatch.setattr(naming_identity, "_load_state", fake_load)
    choices = asyncio.run(naming_identity.role_autocomplete(interaction, "Verified"))

    assert [choice.value for choice in choices[:2]] == ["2", "1"]


def test_exact_live_role_match_still_avoids_alias_state_read(monkeypatch) -> None:
    exact = _FakeResource(1, "Verified", position=50)
    partial = _FakeResource(2, "Verified Team", position=40)
    guild = _FakeGuild(roles=[exact, partial])
    interaction = SimpleNamespace(guild=guild)

    async def fail_load(_guild_id: int):
        raise AssertionError("exact live match must stay on the no-I/O fast path")

    monkeypatch.setattr(naming_identity, "_load_state", fail_load)
    choices = asyncio.run(naming_identity.role_autocomplete(interaction, "Verified"))

    assert choices[0].value == "1"
